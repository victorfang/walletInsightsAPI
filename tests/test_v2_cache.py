"""Capacity, persistence and transaction invariants for the age-only V2 cache."""

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from v2.cache import Cache


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "age.sqlite3"
        self.cache = Cache(self.path, max_entries=2)

    async def asyncTearDown(self):
        await self.cache.close()
        self.temp.cleanup()

    async def put(self, address, epoch=100):
        return await self.cache.put("eth", address, "v1", epoch)

    async def get(self, address):
        return await self.cache.get("eth", address, "v1")

    def state(self):
        with closing(sqlite3.connect(self.path)) as db:
            counter, count = db.execute(
                "SELECT access_counter,row_count FROM state"
            ).fetchone()
            actual = db.execute("SELECT COUNT(*) FROM first_seen").fetchone()[0]
        self.assertEqual(count, actual)
        return counter, count

    async def test_lru_access_order_survives_restart(self):
        await self.put("a")
        await self.put("b")
        self.assertEqual(await self.get("a"), 100)
        await self.cache.close()
        self.cache = Cache(self.path, max_entries=2)
        await self.put("c")
        self.assertIsNone(await self.get("b"))
        self.assertEqual(await self.get("a"), 100)
        self.assertEqual(await self.get("c"), 100)
        self.assertEqual(self.state()[1], 2)

    async def test_minimum_updates_do_not_evict_existing_keys(self):
        await self.put("a", 100)
        await self.put("b", 200)
        self.assertEqual(await self.put("a", 300), 100)
        self.assertEqual(await self.put("a", 50), 50)
        self.assertEqual(await self.get("b"), 200)
        self.assertEqual(await self.get("a"), 50)
        await self.put("c")
        self.assertIsNone(await self.get("b"))
        self.assertEqual(await self.get("a"), 50)

    async def test_chain_and_coverage_are_part_of_the_key(self):
        await self.cache.close()
        self.cache = Cache(self.path, max_entries=4)
        for chain, coverage, epoch in (("eth", "v1", 10), ("eth", "v2", 20),
                                       ("base", "v1", 30)):
            await self.cache.put(chain, "same", coverage, epoch)
        for chain, coverage, epoch in (("eth", "v1", 10), ("eth", "v2", 20),
                                       ("base", "v1", 30)):
            self.assertEqual(await self.cache.get(chain, "same", coverage), epoch)
        self.assertEqual(self.state()[1], 3)

    async def test_concurrent_admission_stays_bounded(self):
        await asyncio.gather(*(self.put(str(i), i + 1) for i in range(150)))
        self.assertEqual(self.state(), (150, 2))

    async def test_concurrent_same_key_retains_minimum(self):
        await asyncio.gather(*(self.put("a", i) for i in range(100, 1, -1)))
        self.assertEqual(await self.get("a"), 2)
        self.assertEqual(self.state()[1], 1)

    async def test_capacity_shrink_evicts_oldest_on_reopen(self):
        await self.cache.close()
        self.cache = Cache(self.path, max_entries=4)
        for address in "abcd":
            await self.put(address)
        await self.get("a")
        await self.cache.close()
        self.cache = Cache(self.path, max_entries=2)
        self.assertIsNone(await self.get("b"))
        self.assertIsNone(await self.get("c"))
        self.assertEqual(await self.get("d"), 100)
        self.assertEqual(await self.get("a"), 100)
        self.assertEqual(self.state()[1], 2)

    async def test_failed_admission_rolls_back_eviction_and_metadata(self):
        await self.put("a")
        await self.put("b")
        before = self.state()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""CREATE TRIGGER reject_new BEFORE INSERT ON first_seen
                WHEN new.address='denied' BEGIN SELECT RAISE(ABORT,'injected'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.put("denied")
        self.assertEqual(self.state(), before)
        self.assertEqual(await self.get("a"), 100)
        self.assertEqual(await self.get("b"), 100)

    async def test_separate_connections_keep_metadata_consistent(self):
        other = Cache(self.path, max_entries=2)
        try:
            await asyncio.gather(*(
                (self.cache if i % 2 else other).put("eth", str(i), "v1", i + 1)
                for i in range(80)))
            self.assertEqual(self.state(), (80, 2))
        finally:
            await other.close()

    async def test_miss_does_not_advance_recency(self):
        await self.put("a")
        before = self.state()
        self.assertIsNone(await self.get("missing"))
        self.assertEqual(self.state(), before)

    async def test_queued_put_expiring_behind_lock_never_admits_epoch(self):
        await self.put("a")
        before = self.state()
        with self.cache._lock:
            task = asyncio.create_task(self.cache.put(
                "eth", "late", "v1", 50, deadline=time.monotonic() + .02))
            await asyncio.sleep(.04)
        with self.assertRaises(TimeoutError):
            await task
        self.assertIsNone(await self.get("late"))
        self.assertEqual(self.state(), before)

    async def test_transaction_expiring_before_commit_rolls_back_eviction(self):
        await self.put("a")
        await self.put("b")
        before = self.state()
        with patch("v2.cache.monotonic", side_effect=[9, 11]):
            with self.assertRaises(TimeoutError):
                await self.cache.put("eth", "late", "v1", 50, deadline=10)
        self.assertEqual(self.state(), before)
        self.assertIsNone(await self.get("late"))
        self.assertEqual(await self.get("a"), 100)
        self.assertEqual(await self.get("b"), 100)

    async def test_invalid_data_and_idempotent_close(self):
        for epoch in (0, -1, True, 1.5, "100", 2**63):
            with self.assertRaises(ValueError):
                await self.put("a", epoch)
        with self.assertRaises(ValueError):
            await self.cache.get("eth", "", "v1")
        for limit in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                Cache(self.path, max_entries=limit)
        await self.cache.close()
        await self.cache.close()
        with self.assertRaises(RuntimeError):
            await self.get("a")
