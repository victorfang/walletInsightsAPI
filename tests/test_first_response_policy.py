"""Behavioral checks for the benchmark-only first-response policy."""

import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from benchmarks.first_response_policy import FirstResponseAgeService
from eth_wallet_check.deadline import BoundedExecutor, REQUEST_BUDGET_SECONDS
from eth_wallet_check.http import JSONHTTP
from wallet_age_days.providers import Observation
from wallet_age_days.service import WalletAgeError
from wallet_age_days.store import EpochStore


ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
NOW, FIRST = 1_700_000_000, 1_600_000_000


def observed(epoch, source, delay=0):
    def run():
        time.sleep(delay)
        return Observation(epoch, True, source)
    return run


class FirstResponsePolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = EpochStore(self.directory.name + "/cache.sqlite3", clock=lambda: NOW)
        self.addCleanup(self.store.close)
        self.executor = BoundedExecutor(max_workers=8)
        self.addCleanup(self.executor.shutdown)
        self.sources = Mock()
        self.http = JSONHTTP()

    def service(self, *, soft=.08, hard=.25, refine=False, background_executor=None):
        service = FirstResponseAgeService(
            self.sources, self.store, self.http, clock=lambda: NOW,
            soft_deadline=soft, hard_deadline=hard, executor=self.executor,
            refine=refine, background_executor=background_executor)
        self.addCleanup(service.close)
        return service

    def test_cache_hit_computes_days_without_constructing_provider_jobs(self):
        self.store.put_min("evm:1:v1", ADDRESS, FIRST)
        service = self.service()
        result = service.check("eth", ADDRESS)
        self.assertEqual(result.days, (NOW-FIRST)//86400)
        self.assertEqual(result.cache, "hit")
        self.assertEqual(result.evidence, "cached-canonical")
        self.sources.jobs.assert_not_called()
        self.assertTrue(service.snapshot(result.benchmark_token)["finished"])

    def test_two_independent_different_epochs_return_the_minimum_without_agreement(self):
        self.sources.jobs.return_value = [
            ("etherscan_normal", observed(FIRST+1000, "etherscan", .01)),
            ("routescan_normal", observed(FIRST, "routescan", .025)),
            ("slow", observed(FIRST-1000, "slow", .18))]
        service = self.service()
        started = time.monotonic()
        result = service.check("eth", ADDRESS)
        self.assertLess(time.monotonic()-started, .12)
        self.assertEqual(result.epoch, FIRST)
        self.assertEqual(result.response_reason, "two_usable_providers")
        self.assertEqual(result.positive_sources, 2)
        self.executor.shutdown(wait=True)
        self.assertEqual(self.store.peek(service.namespace, ADDRESS), FIRST)

    def test_two_alchemy_directions_are_one_provider_until_soft_deadline(self):
        self.sources.jobs.return_value = [
            ("alchemy_from", observed(FIRST+1000, "alchemy", .01)),
            ("alchemy_to", observed(FIRST, "alchemy", .02)),
            ("slow", observed(FIRST-1000, "slow", .2))]
        service = self.service()
        started = time.monotonic()
        result = service.check("eth", ADDRESS)
        elapsed = time.monotonic()-started
        self.assertGreaterEqual(elapsed, .07)
        self.assertLess(elapsed, .15)
        self.assertEqual(result.response_reason, "one_provider_soft_deadline")
        self.assertEqual(result.positive_sources, 1)

    def test_one_second_soft_deadline_is_measured_from_request_start(self):
        self.sources.jobs.return_value = [
            ("fast", observed(FIRST, "fast", .01)),
            ("slow", observed(FIRST-1000, "slow", 1.2))]
        service = self.service(soft=1, hard=1.3)
        started = time.monotonic()
        result = service.check("eth", ADDRESS)
        elapsed = time.monotonic()-started
        self.assertGreaterEqual(elapsed, .98)
        self.assertLess(elapsed, 1.15)
        self.assertEqual(result.response_reason, "one_provider_soft_deadline")

    def test_empty_and_error_responses_do_not_count_as_two_usable_providers(self):
        def failed():
            raise ValueError("https://provider.invalid/SECRET")
        self.sources.jobs.return_value = [
            ("empty", lambda: None), ("bad", failed),
            ("valid", observed(FIRST, "valid", .09)),
            ("slow", observed(FIRST-1000, "slow", .2))]
        service = self.service(soft=.05)
        started = time.monotonic()
        result = service.check("eth", ADDRESS)
        self.assertGreaterEqual(time.monotonic()-started, .08)
        self.assertEqual(result.epoch, FIRST)
        self.assertEqual(result.response_reason, "one_provider_soft_deadline")
        self.assertNotIn("SECRET", str(service.snapshot(result.benchmark_token)))

    def test_background_refinement_can_only_decrease_the_cached_epoch(self):
        self.sources.jobs.return_value = [
            ("a", observed(FIRST+1000, "a", .01)),
            ("b", observed(FIRST, "b", .02)),
            ("c", observed(FIRST-86400, "c", .1))]
        service = self.service(refine=True)
        initial = service.check("eth", ADDRESS)
        self.assertEqual(initial.epoch, FIRST)
        self.assertTrue(service.drain(timeout=.4))
        trace = service.snapshot(initial.benchmark_token)
        self.assertEqual(trace["initial_epoch"], FIRST)
        self.assertEqual(trace["final_epoch"], FIRST-86400)
        self.assertEqual(trace["cached_final_epoch"], FIRST-86400)
        self.assertTrue(trace["late_smaller_epoch"])
        self.assertEqual(trace["background_updates"], 1)
        self.assertEqual(service.check("eth", ADDRESS).days, initial.days+1)

    def test_fast_normal_indexes_can_preempt_an_older_incoming_nft(self):
        # Deliberately differs from the production agreement/Alchemy guard.
        self.sources.jobs.return_value = [
            ("etherscan_normal", observed(FIRST, "etherscan", .01)),
            ("routescan_normal", observed(FIRST, "routescan", .02)),
            ("alchemy_from", observed(FIRST+1000, "alchemy", .005)),
            ("alchemy_to", observed(FIRST-86400, "alchemy", .12))]
        service = self.service(refine=True)
        result = service.check("eth", ADDRESS)
        self.assertEqual(result.epoch, FIRST)
        self.assertEqual(result.response_reason, "two_usable_providers")
        self.assertTrue(service.drain(.4))
        self.assertEqual(service.snapshot(result.benchmark_token)["final_epoch"], FIRST-86400)

    def test_partial_group_observation_can_contribute_minimum_at_soft_return(self):
        self.sources.jobs.return_value = [
            ("etherscan_normal", observed(FIRST, "etherscan", .01)),
            ("alchemy_from", observed(FIRST-1000, "alchemy", .02)),
            ("alchemy_to", observed(FIRST-86400, "alchemy", .18))]
        service = self.service(soft=.06)
        result = service.check("eth", ADDRESS)
        self.assertEqual(result.epoch, FIRST-1000)
        trace = service.snapshot(result.benchmark_token)
        self.assertEqual(trace["usable_provider_groups_at_response"], 1)
        self.assertEqual(trace["positive_sources_at_response"], 2)

    def test_hard_deadline_returns_unknown_and_never_accepts_late_cache_write(self):
        self.sources.jobs.return_value = [("late", observed(FIRST, "late", .16))]
        service = self.service(soft=.03, hard=.08, refine=True)
        started = time.monotonic()
        with self.assertRaises(WalletAgeError) as error:
            service.check("eth", ADDRESS)
        self.assertEqual(error.exception.status, 503)
        self.assertLess(time.monotonic()-started, .14)
        self.executor.shutdown(wait=True)
        self.assertIsNone(self.store.peek(service.namespace, ADDRESS))
        self.assertEqual(service.trace_for(ADDRESS)["response_reason"], "hard_deadline")

    def test_refinement_stops_at_original_deadline_without_late_write(self):
        self.sources.jobs.return_value = [
            ("a", observed(FIRST, "a", .01)),
            ("b", observed(FIRST+1000, "b", .02)),
            ("late", observed(FIRST-86400, "late", .2))]
        service = self.service(soft=.04, hard=.1, refine=True)
        result = service.check("eth", ADDRESS)
        self.assertTrue(service.drain(.3))
        self.executor.shutdown(wait=True)
        self.assertEqual(self.store.peek(service.namespace, ADDRESS), FIRST)
        self.assertFalse(service.snapshot(result.benchmark_token)["late_smaller_epoch"])

    def test_missing_provisional_and_invalid_observations_never_become_zero(self):
        for value, expected in [(None, 404), (Observation(FIRST, False, "p"), 503),
                                (Observation(NOW+1, True, "f"), 503)]:
            with self.subTest(value=value):
                self.sources.jobs.return_value = [("one", lambda value=value: value)]
                service = self.service()
                with self.assertRaises(WalletAgeError) as error:
                    service.check("eth", ADDRESS)
                self.assertEqual(error.exception.status, expected)
                self.assertEqual(self.store.size(), 0)
        self.sources.jobs.return_value = [("one", observed(NOW-10, "one"))]
        self.assertEqual(self.service().check("eth", ADDRESS).days, 0)

    def test_background_capacity_rejects_work_instead_of_building_queue(self):
        background = BoundedExecutor(max_workers=1, max_pending=0)
        release, running = threading.Event(), threading.Event()
        def occupied():
            running.set()
            release.wait(1)
        background.submit(occupied)
        self.assertTrue(running.wait(.2))
        self.sources.jobs.return_value = [
            ("a", observed(FIRST, "a", .01)),
            ("b", observed(FIRST+1000, "b", .02)),
            ("late", observed(FIRST-86400, "late", .15))]
        service = self.service(refine=True, background_executor=background)
        try:
            result = service.check("eth", ADDRESS)
            self.assertFalse(service.snapshot(result.benchmark_token)["refinement_admitted"])
            self.executor.shutdown(wait=True)
            self.assertEqual(self.store.peek(service.namespace, ADDRESS), FIRST)
        finally:
            release.set()
            background.shutdown()

    def test_invalid_input_never_starts_jobs_or_creates_trace(self):
        service = self.service()
        with self.assertRaises(WalletAgeError) as error:
            service.check("eth", "0x123")
        self.assertEqual(error.exception.status, 400)
        self.sources.jobs.assert_not_called()
        self.assertEqual(service.snapshot(), [])

    def test_five_second_comparison_does_not_change_the_production_budget(self):
        service = self.service(soft=1, hard=5)
        self.assertEqual(service.hard_deadline, 5)
        self.assertEqual(REQUEST_BUDGET_SECONDS, 2.6)


if __name__ == "__main__":
    unittest.main()
