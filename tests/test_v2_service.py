"""Age-only orchestration, deadline and coverage behavior without live providers."""

import asyncio
from copy import deepcopy
import sqlite3
import time
from types import SimpleNamespace
import unittest

from v2.service import AgeError, Service


NOW = 1_700_000_000
DAY = 86400
ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
OTHER_ADDRESS = "0x" + "a" * 40
CONFIG = {
    "wallet_age": {"deadline_ms": 150, "soft_deadline_ms": 30, "max_inflight": 32},
    "chains": {"eth": {"chain_id": 1, "name": "ethereum", "family": "evm",
                       "aliases": ["eth", "ethereum"], "coverage_version": "history-v1"}},
    "scopes": {"evm": {"chains": ["eth"], "aliases": ["evm", "polygon"]}},
}


class MemoryCache:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.closed = False
        self.fail = False

    async def get(self, *key):
        if self.fail:
            raise sqlite3.OperationalError("database unavailable")
        return self.rows.get(key)

    async def put(self, chain, address, coverage, epoch, *, deadline=None):
        if self.fail:
            raise sqlite3.OperationalError("database unavailable")
        key = (chain, address, coverage)
        self.rows[key] = min(self.rows.get(key, epoch), epoch)
        self.writes.append((key, epoch))
        return self.rows[key]

    async def close(self):
        self.closed = True


class Feeds:
    """Plans: (provider, epoch, complete, delay_seconds_or_event)."""

    def __init__(self, plans=(), by_chain=None):
        self.plans = plans
        self.by_chain = by_chain or {}
        self.calls = []
        self.cancelled = 0

    def jobs(self, chain, address):
        self.calls.append((chain["chain_id"], address))
        plans = self.by_chain.get(chain["chain_id"], self.plans)
        return [(name, self.run(epoch, complete, delay))
                for name, epoch, complete, delay in plans]

    async def run(self, epoch, complete, delay):
        try:
            if isinstance(delay, asyncio.Event):
                await delay.wait()
            else:
                await asyncio.sleep(delay)
            if isinstance(epoch, Exception):
                raise epoch
            return SimpleNamespace(epoch=epoch, complete=complete)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = deepcopy(CONFIG)
        self.cache = MemoryCache()
        self.services = []
        self.now = NOW

    async def asyncTearDown(self):
        for service in self.services:
            await service.close()

    def service(self, plans=(), *, feeds=None, config=None):
        feeds = feeds or Feeds(plans)
        service = Service(feeds, self.cache, config or self.config, clock=lambda: self.now)
        self.services.append(service)
        return service, feeds

    async def error(self, service, status, message, chain="eth", address=ADDRESS):
        with self.assertRaises(AgeError) as caught:
            await service.check(chain, address)
        self.assertEqual(caught.exception.status, status)
        self.assertEqual(caught.exception.message, message)

    async def test_cache_hit_normalizes_address_and_recalculates_days(self):
        epoch = NOW - 5 * DAY - 10
        self.cache.rows[("1", ADDRESS, "history-v1")] = epoch
        service, feeds = self.service()
        result = await service.check("ETH", "0x" + ADDRESS[2:].upper())
        self.assertEqual((result.days, result.epoch, result.cache), (5, epoch, "hit"))
        self.now += DAY
        self.assertEqual((await service.check("ethereum", ADDRESS)).days, 6)
        self.assertEqual(feeds.calls, [])

    async def test_two_complete_providers_return_minimum_before_slow_job(self):
        gate = asyncio.Event()
        service, _ = self.service([
            ("alchemy", NOW - 5 * DAY, True, 0),
            ("etherscan", NOW - 10 * DAY, True, 0),
            ("routescan", NOW - 15 * DAY, True, gate),
        ])
        result = await asyncio.wait_for(service.check("eth", ADDRESS), .1)
        self.assertEqual((result.days, result.epoch), (10, NOW - 10 * DAY))

    async def test_one_provider_waits_for_soft_deadline(self):
        service, _ = self.service([
            ("alchemy", NOW - 5 * DAY, True, 0),
            ("etherscan", None, True, asyncio.Event()),
        ])
        started = time.monotonic()
        result = await service.check("eth", ADDRESS)
        self.assertGreaterEqual(time.monotonic() - started, .02)
        self.assertLess(time.monotonic() - started, .12)
        self.assertEqual(result.days, 5)

    async def test_duplicate_provider_names_do_not_count_twice(self):
        service, _ = self.service([
            ("alchemy", NOW - 5 * DAY, True, 0),
            ("alchemy", NOW - 6 * DAY, True, 0),
            ("etherscan", None, True, asyncio.Event()),
        ])
        started = time.monotonic()
        result = await service.check("eth", ADDRESS)
        self.assertGreaterEqual(time.monotonic() - started, .02)
        self.assertEqual(result.days, 6)

    async def test_one_finished_positive_needs_no_artificial_soft_wait(self):
        self.config["wallet_age"]["soft_deadline_ms"] = 120
        service, _ = self.service([("alchemy", NOW - 5 * DAY, True, 0)])
        result = await asyncio.wait_for(service.check("eth", ADDRESS), .08)
        self.assertEqual(result.days, 5)

    async def test_background_refinement_updates_minimum_before_next_cache_hit(self):
        gate = asyncio.Event()
        service, _ = self.service([
            ("alchemy", NOW - 5 * DAY, True, 0),
            ("etherscan", NOW - 6 * DAY, True, 0),
            ("routescan", NOW - 20 * DAY, True, gate),
        ])
        self.assertEqual((await service.check("eth", ADDRESS)).days, 6)
        gate.set()
        for _ in range(30):
            if self.cache.rows[("1", ADDRESS, "history-v1")] == NOW - 20 * DAY:
                break
            await asyncio.sleep(.002)
        self.assertEqual(self.cache.rows[("1", ADDRESS, "history-v1")], NOW - 20 * DAY)
        result = await service.check("eth", ADDRESS)
        self.assertEqual((result.days, result.cache), (20, "hit"))

    async def test_verified_partial_observation_can_return_at_hard_deadline(self):
        self.config["wallet_age"]["deadline_ms"] = 65
        service, _ = self.service([
            ("alchemy", NOW - 5 * DAY, False, 0),
            ("etherscan", None, True, asyncio.Event()),
        ])
        started = time.monotonic()
        result = await service.check("eth", ADDRESS)
        self.assertGreaterEqual(time.monotonic() - started, .05)
        self.assertLess(time.monotonic() - started, .2)
        self.assertEqual(result.days, 5)

    async def test_all_complete_empty_is_not_found_and_not_cached_as_zero(self):
        service, _ = self.service([
            ("alchemy", None, True, 0), ("etherscan", None, True, 0)])
        await self.error(service, 404, "not_found")
        self.assertEqual(self.cache.rows, {})

    async def test_failures_incomplete_and_missing_sources_are_unknown(self):
        for plans in ([], [("alchemy", None, False, 0)],
                      [("alchemy", ValueError("provider SECRET"), True, 0),
                       ("etherscan", None, True, 0)]):
            with self.subTest(plans=plans):
                service, _ = self.service(plans)
                await self.error(service, 503, "unknown")
        self.assertEqual(self.cache.rows, {})

    async def test_deadline_cancels_late_results_without_cache_write(self):
        self.config["wallet_age"]["deadline_ms"] = 40
        service, feeds = self.service([("alchemy", NOW - 10 * DAY, True, .09)])
        await self.error(service, 503, "unknown")
        await asyncio.sleep(.1)
        self.assertEqual(self.cache.rows, {})
        self.assertEqual(feeds.cancelled, 1)

    async def test_zero_age_requires_a_positive_verified_epoch(self):
        service, _ = self.service([("alchemy", NOW - DAY + 1, True, 0)])
        result = await service.check("eth", ADDRESS)
        self.assertEqual(result.days, 0)
        self.assertEqual(result.epoch, NOW - DAY + 1)

    async def test_invalid_epochs_cannot_be_cached_or_reported_not_found(self):
        for epoch in (0, -1, NOW + 1, True, "123", 123.5):
            with self.subTest(epoch=epoch):
                service, _ = self.service([("alchemy", epoch, True, 0)])
                await self.error(service, 503, "unknown")
        self.assertEqual(self.cache.rows, {})

    async def test_cache_failure_does_not_discard_verified_provider_result(self):
        self.cache.fail = True
        service, _ = self.service([("alchemy", NOW - 5 * DAY, True, 0)])
        result = await service.check("eth", ADDRESS)
        self.assertEqual((result.days, result.cache), (5, "bypass"))

    async def test_slow_cache_read_obeys_request_deadline_before_provider_work(self):
        self.config["wallet_age"]["deadline_ms"] = 45
        cancelled = asyncio.Event()

        async def stalled_get(*_key):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        self.cache.get = stalled_get
        service, feeds = self.service([("alchemy", NOW - 5 * DAY, True, 0)])
        started = time.monotonic()
        await self.error(service, 503, "unknown")
        self.assertLess(time.monotonic() - started, .2)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(feeds.calls, [])
        self.assertEqual(self.cache.rows, {})

    async def test_invalid_and_unsupported_inputs_make_no_provider_calls(self):
        service, feeds = self.service()
        for address in ("", "0x123", ADDRESS + " ", "vitalik.eth", None):
            await self.error(service, 400, "invalid", address=address)
        await self.error(service, 501, "coming soon contact info@anchain.ai", chain="btc")
        self.assertEqual(feeds.calls, [])

    async def test_legacy_scope_alias_has_explicit_actual_coverage(self):
        service, feeds = self.service([("alchemy", NOW - 5 * DAY, True, 0)])
        result = await service.check("PoLyGoN", ADDRESS)
        self.assertEqual(result.days, 5)
        self.assertEqual(result.coverage, "ethereum")
        self.assertEqual(feeds.calls, [(1, ADDRESS)])

    async def test_scope_expansion_searches_missing_chain_despite_eth_cache_hit(self):
        self.cache.rows[("1", ADDRESS, "history-v1")] = NOW - 5 * DAY
        self.config["chains"]["base"] = {
            "chain_id": 8453, "name": "base", "family": "evm",
            "aliases": ["base"], "coverage_version": "history-v1"}
        self.config["scopes"]["evm"]["chains"].append("base")
        feeds = Feeds(by_chain={8453: [("alchemy", NOW - 20 * DAY, True, 0)]})
        service, _ = self.service(feeds=feeds)
        result = await service.check("evm", ADDRESS)
        self.assertEqual((result.days, result.epoch), (20, NOW - 20 * DAY))
        self.assertEqual(feeds.calls, [(8453, ADDRESS)])
        self.assertEqual(self.cache.rows[("1", ADDRESS, "history-v1")], NOW - 5 * DAY)
        self.assertEqual(self.cache.rows[("8453", ADDRESS, "history-v1")], NOW - 20 * DAY)
        self.assertIn("base", result.coverage)

    async def test_scope_with_an_unqueryable_chain_is_unknown_not_not_found(self):
        self.config["chains"]["base"] = {
            "chain_id": 8453, "name": "base", "family": "evm",
            "aliases": ["base"], "coverage_version": "history-v1"}
        self.config["scopes"]["evm"]["chains"].append("base")
        feeds = Feeds(by_chain={1: [("alchemy", None, True, 0)]})
        service, _ = self.service(feeds=feeds)
        await self.error(service, 503, "unknown", chain="evm")

    async def test_capacity_includes_background_jobs_and_rejects_without_queue(self):
        self.config["wallet_age"]["max_inflight"] = 1
        service, feeds = self.service([
            ("alchemy", NOW - 5 * DAY, True, 0),
            ("etherscan", None, True, asyncio.Event()),
        ])
        await service.check("eth", ADDRESS)
        started = time.monotonic()
        await self.error(service, 503, "unknown", address=OTHER_ADDRESS)
        self.assertLess(time.monotonic() - started, .03)
        self.assertEqual(feeds.calls, [(1, ADDRESS)])
        await service.close()
        self.assertTrue(self.cache.closed)
        self.assertEqual(feeds.cancelled, 1)
