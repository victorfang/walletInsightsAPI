"""Request-level tests for the focused integer walletAgeDays API service."""

import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from eth_wallet_check.deadline import BoundedExecutor
from eth_wallet_check.http import JSONHTTP
from wallet_age_days.providers import Observation
from wallet_age_days.service import WalletAgeError, WalletAgeService
from wallet_age_days.store import EpochStore


ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
OTHER = "0x" + "2" * 40
FIRST = 1_600_000_000


class WalletAgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.directory.name + "/epochs.sqlite3"
        self.now = 1_700_000_000
        self.clock = lambda: self.now
        self.store = EpochStore(self.path, clock=self.clock)
        self.addCleanup(self.store.close)
        self.sources = Mock()
        self.sources.jobs.return_value = [("test", lambda: Observation(FIRST, True, "test"))]
        self.executor = BoundedExecutor(max_workers=8)
        self.addCleanup(self.executor.shutdown)
        self.http = JSONHTTP()

    def service(self, *, budget=0.05, executor=None, store=None, chains=((1, "ethereum"),)):
        return WalletAgeService(self.sources, store or self.store, self.http,
                                clock=self.clock, budget=budget,
                                executor=executor or self.executor, chains=chains)

    def assert_error(self, service, status, blockchain="eth", address=ADDRESS):
        with self.assertRaises(WalletAgeError) as result:
            service.check(blockchain, address)
        self.assertEqual(result.exception.status, status)
        return result.exception

    def test_returns_only_whole_elapsed_days_via_public_method(self):
        result = self.service().walletAgeDays("eth", ADDRESS)
        self.assertIs(type(result), int)
        self.assertEqual(result, (self.now - FIRST) // 86400)
        self.assertEqual(self.store.get("evm:1:v1", ADDRESS), FIRST)

    def test_eth_evm_polygon_aliases_reuse_one_canonical_address_cache(self):
        service = self.service()
        first = service.check("ETH", ADDRESS.upper().replace("0X", "0x"))
        self.assertEqual(first.cache, "miss")
        for blockchain in ("Ethereum", "EVM", "Polygon", "matic", "polygon-mainnet"):
            result = service.check(blockchain, ADDRESS)
            self.assertEqual(result.cache, "hit")
            self.assertEqual(result.epoch, FIRST)
        self.sources.jobs.assert_called_once_with(ADDRESS)
        self.assertEqual(self.store.size(), 1)

    def test_age_advances_without_provider_calls_or_timestamp_expiration(self):
        service = self.service()
        first = service.walletAgeDays("eth", ADDRESS)
        self.now += 5 * 86400
        self.assertEqual(service.walletAgeDays("evm", ADDRESS), first + 5)
        self.sources.jobs.assert_called_once_with(ADDRESS)
        self.assertEqual(self.store.get(service.namespace, ADDRESS), FIRST)

    def test_current_day_can_return_legitimate_zero_only_from_verified_event(self):
        self.sources.jobs.return_value = [("today", lambda: Observation(self.now - 3600, True, "today"))]
        result = self.service().check("eth", ADDRESS)
        self.assertEqual(result.days, 0)
        self.assertTrue(result.finalized)
        self.assertEqual(self.store.size(), 1)

    def test_ill_formatted_addresses_are_invalid_before_cache_or_network(self):
        for address in ("", "0x123", "0X" + "1" * 40, "0x" + "g" * 40,
                        ADDRESS + "\n", " " + ADDRESS, None, 42):
            with self.subTest(address=address):
                error = self.assert_error(self.service(), 400, address=address)
                self.assertEqual(error.message, "invalid")
        self.sources.jobs.assert_not_called()
        self.assertEqual(self.store.size(), 0)

    def test_unsupported_chain_and_malformed_blockchain_are_explicit_errors(self):
        self.assert_error(self.service(), 501, blockchain="btc")
        for blockchain in ("", "eth ", "1eth", None, 42):
            with self.subTest(blockchain=blockchain):
                self.assert_error(self.service(), 400, blockchain=blockchain)
        self.sources.jobs.assert_not_called()

    def test_missing_observations_do_not_become_cached_age_zero(self):
        self.sources.jobs.return_value = [("empty", lambda: None), ("empty2", lambda: None)]
        service = self.service()
        self.assert_error(service, 404)
        self.assert_error(service, 404)
        self.assertEqual(self.sources.jobs.call_count, 2)
        self.assertEqual(self.store.size(), 0)

    def test_source_failure_is_unknown_and_is_not_cached(self):
        def fail():
            raise EOFError("https://private.example/SECRET")

        self.sources.jobs.return_value = [("error", fail), ("empty", lambda: None)]
        service = self.service()
        error = self.assert_error(service, 503)
        self.assertNotIn("SECRET", error.message)
        self.assert_error(service, 503)
        self.assertEqual(self.sources.jobs.call_count, 2)
        self.assertEqual(self.store.size(), 0)

    def test_provisional_only_observation_is_unknown_not_a_numeric_age(self):
        self.sources.jobs.return_value = [("recent", lambda: Observation(FIRST, False, "recent"))]
        self.assert_error(self.service(), 503)
        self.assertEqual(self.store.size(), 0)

    def test_finalized_fallback_is_used_when_earlier_candidate_is_provisional(self):
        self.sources.jobs.return_value = [
            ("index", lambda: Observation(FIRST - 1000, False, "index")),
            ("verified", lambda: Observation(FIRST, True, "verified")),
        ]
        service = self.service()
        result = service.check("eth", ADDRESS)
        self.assertEqual(result.epoch, FIRST)
        self.assertTrue(result.finalized)
        self.assertEqual(self.store.get(service.namespace, ADDRESS), FIRST)

    def test_earliest_finalized_observation_wins_across_provider_jobs(self):
        self.sources.jobs.return_value = [
            ("later", lambda: Observation(FIRST + 1000, True, "later")),
            ("earlier", lambda: Observation(FIRST, True, "earlier")),
        ]
        service = self.service()
        result = service.check("eth", ADDRESS)
        self.assertEqual(result.epoch, FIRST)
        self.assertEqual(self.store.get(service.namespace, ADDRESS), FIRST)

    def test_invalid_or_future_observation_is_unknown(self):
        for epoch in (0, -1, True, "123", self.now + 1):
            with self.subTest(epoch=epoch):
                self.sources.jobs.return_value = [("bad", lambda epoch=epoch: Observation(epoch, True, "bad"))]
                self.assert_error(self.service(), 503)
        self.assertEqual(self.store.size(), 0)

    def test_slow_source_is_bounded_and_late_results_never_write_cache(self):
        def late():
            time.sleep(0.25)
            return Observation(FIRST, True, "late")

        self.sources.jobs.return_value = [("late", late)]
        service = self.service()
        started = time.monotonic()
        self.assert_error(service, 503)
        self.assertLess(time.monotonic() - started, 0.2)
        self.executor.shutdown(wait=True)
        self.assertIsNone(self.store.get(service.namespace, ADDRESS))

    def test_finished_fallback_can_succeed_without_waiting_for_slow_source(self):
        def late():
            time.sleep(0.25)
            return Observation(FIRST - 1000, True, "late")

        self.sources.jobs.return_value = [("late", late), ("fast", lambda: Observation(FIRST, True, "fast"))]
        service = self.service()
        started = time.monotonic()
        result = service.check("eth", ADDRESS)
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(result.epoch, FIRST)
        self.executor.shutdown(wait=True)
        self.assertEqual(self.store.get(service.namespace, ADDRESS), FIRST)

    def test_saturated_worker_pool_rejects_without_queue_or_slow_wait(self):
        executor = BoundedExecutor(max_workers=1)
        release, running = threading.Event(), threading.Event()
        called = Mock(return_value=Observation(FIRST, True, "unexpected"))

        def occupied():
            running.set()
            release.wait(1)

        executor.submit(occupied)
        self.assertTrue(running.wait(0.2))
        self.sources.jobs.return_value = [("unavailable", called)]
        try:
            started = time.monotonic()
            self.assert_error(self.service(executor=executor), 503)
            self.assertLess(time.monotonic() - started, 0.2)
            called.assert_not_called()
        finally:
            release.set()
            executor.shutdown()

    def test_busy_cache_read_and_write_do_not_hide_verified_live_age(self):
        store = Mock()
        store.get.side_effect = sqlite3.OperationalError("database locked")
        store.put_min.side_effect = sqlite3.OperationalError("database locked")
        result = self.service(store=store).check("eth", ADDRESS)
        self.assertEqual(result.epoch, FIRST)
        self.assertEqual(result.cache, "bypass")
        self.assertTrue(result.finalized)

    def test_full_cache_admits_live_age_by_evicting_least_recent_entry(self):
        store = EpochStore(self.path, capacity=1, clock=self.clock)
        self.addCleanup(store.close)
        service = self.service(store=store)
        store.put_min(service.namespace, OTHER, FIRST - 1000)
        result = service.check("eth", ADDRESS)
        self.assertEqual(result.epoch, FIRST)
        self.assertEqual(result.cache, "miss")
        self.assertIsNone(store.get(service.namespace, OTHER))
        self.assertEqual(store.get(service.namespace, ADDRESS), FIRST)
        self.assertEqual(store.size(), 1)

    def test_expanding_enabled_chain_coverage_changes_cache_namespace(self):
        service = self.service()
        service.check("eth", ADDRESS)
        future = self.service(chains=((137, "polygon"), (1, "ethereum")))
        self.assertNotEqual(service.namespace, future.namespace)
        self.assertEqual(future.namespace, "evm:1,137:v1")
        result = future.check("evm", ADDRESS)
        self.assertEqual(result.cache, "miss")
        self.assertEqual(self.sources.jobs.call_count, 2)
        self.assertEqual(self.store.size(), 2)


if __name__ == "__main__":
    unittest.main()
