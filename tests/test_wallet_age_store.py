"""First-observation values, persistent LRU ordering and bounded capacity."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from contextlib import closing
import tempfile
import time
import unittest

from wallet_age_days.store import EpochStore


class EpochStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "epochs.sqlite3"
        self.now = 1_800_000_000

    def store(self, capacity=1_000_000):
        store = EpochStore(self.path, capacity=capacity, clock=lambda: self.now)
        self.addCleanup(store.close)
        return store

    def test_positive_epoch_survives_restart_and_has_no_expiration(self):
        first = self.store()
        self.assertTrue(first.put_min("eth", "0xabc", 1_600_000_000))
        first.close()
        self.now += 40 * 365 * 86400
        second = self.store()
        self.assertEqual(second.get("eth", "0xabc"), 1_600_000_000)

    def test_key_preserves_chain_and_address_case_exactly(self):
        store = self.store()
        store.put_min("eth", "AbC", 100)
        store.put_min("polygon", "AbC", 200)
        self.assertEqual(store.get("eth", "AbC"), 100)
        self.assertEqual(store.get("polygon", "AbC"), 200)
        self.assertIsNone(store.get("eth", "abc"))

    def test_minimum_can_improve_but_never_move_later(self):
        store = self.store()
        for epoch in (1000, 2000, 500, 500):
            self.assertTrue(store.put_min("eth", "address", epoch))
        self.assertEqual(store.get("eth", "address"), 500)
        self.assertEqual(store.size(), 1)

    def test_full_store_evicts_least_recent_read_not_oldest_insert_or_epoch(self):
        store = self.store(capacity=2)
        store.put_min("eth", "a", 1000)
        store.put_min("eth", "b", 2000)
        self.assertEqual(store.get("eth", "a"), 1000)
        self.assertTrue(store.put_min("eth", "c", 3000))
        self.assertEqual(store.peek("eth", "a"), 1000)
        self.assertIsNone(store.peek("eth", "b"))
        self.assertEqual(store.peek("eth", "c"), 3000)
        self.assertEqual(store.size(), 2)

    def test_existing_put_touches_recency_and_keeps_minimum_epoch(self):
        store = self.store(capacity=2)
        store.put_min("eth", "a", 1000)
        store.put_min("eth", "b", 2000)
        store.put_min("eth", "a", 1500)
        store.put_min("eth", "c", 3000)
        self.assertEqual(store.peek("eth", "a"), 1000)
        self.assertIsNone(store.peek("eth", "b"))
        store.put_min("eth", "a", 500)
        store.put_min("eth", "d", 4000)
        self.assertEqual(store.peek("eth", "a"), 500)
        self.assertIsNone(store.peek("eth", "c"))

    def test_miss_and_peek_do_not_change_recency(self):
        store = self.store(capacity=2)
        store.put_min("eth", "a", 1000)
        store.put_min("eth", "b", 2000)
        self.assertEqual(store.peek("eth", "a"), 1000)
        self.assertIsNone(store.get("eth", "absent"))
        store.put_min("eth", "c", 3000)
        self.assertIsNone(store.peek("eth", "a"))
        self.assertEqual(store.peek("eth", "b"), 2000)

    def test_recency_survives_restart_and_clock_rollback(self):
        first = self.store(capacity=2)
        first.put_min("eth", "a", 1000)
        first.put_min("eth", "b", 2000)
        self.now -= 5000
        self.assertEqual(first.get("eth", "a"), 1000)
        first.close()
        second = self.store(capacity=2)
        second.put_min("eth", "c", 3000)
        self.assertEqual(second.peek("eth", "a"), 1000)
        self.assertIsNone(second.peek("eth", "b"))

    def test_other_connection_reads_share_one_recency_order(self):
        first = self.store(capacity=2)
        first.put_min("eth", "a", 1000)
        first.put_min("eth", "b", 2000)
        second = self.store(capacity=2)
        second.get("eth", "a")
        first.put_min("eth", "c", 3000)
        self.assertEqual(second.peek("eth", "a"), 1000)
        self.assertIsNone(second.peek("eth", "b"))

    def test_invalid_operations_do_not_protect_a_row_from_eviction(self):
        store = self.store(capacity=2)
        store.put_min("eth", "a", 1000)
        store.put_min("eth", "b", 2000)
        with self.assertRaises(ValueError):
            store.put_min("eth", "a", self.now + 1)
        with self.assertRaises(ValueError):
            store.get("", "a")
        store.put_min("eth", "c", 3000)
        self.assertIsNone(store.peek("eth", "a"))
        self.assertEqual(store.peek("eth", "b"), 2000)

    def test_persisted_capacity_cannot_be_enlarged_by_another_instance(self):
        first, second = self.store(capacity=1), self.store(capacity=100)
        self.assertEqual(second.capacity, 1)
        first.put_min("eth", "a", 100)
        self.assertTrue(second.put_min("eth", "b", 200))
        self.assertIsNone(first.peek("eth", "a"))
        self.assertEqual(first.peek("eth", "b"), 200)
        self.assertEqual(second.size(), 1)

    def test_tighter_reopen_trims_and_updates_capacity_for_existing_instances(self):
        first = self.store(capacity=5)
        for address, epoch in [("a", 1000), ("b", 2000), ("c", 3000)]:
            first.put_min("eth", address, epoch)
        first.get("eth", "a")
        second = self.store(capacity=2)
        self.assertEqual(second.size(), 2)
        self.assertIsNone(second.peek("eth", "b"))
        first.put_min("eth", "d", 4000)
        self.assertEqual(first.capacity, 2)
        self.assertEqual(first.size(), 2)
        self.assertIsNone(second.peek("eth", "c"))
        self.assertEqual(second.peek("eth", "a"), 1000)

    def test_legacy_schema_migrates_values_counter_and_deterministic_ties(self):
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute("CREATE TABLE first_appearances (chain TEXT NOT NULL, address TEXT NOT NULL, "
                           "epoch INTEGER NOT NULL, PRIMARY KEY(chain,address)) WITHOUT ROWID")
                db.execute("CREATE TABLE epoch_store_metadata (singleton INTEGER PRIMARY KEY, "
                           "row_count INTEGER NOT NULL, capacity INTEGER NOT NULL)")
                db.executemany("INSERT INTO first_appearances VALUES (?,?,?)", [("eth", "b", 2000), ("eth", "a", 1000)])
                db.execute("INSERT INTO epoch_store_metadata VALUES (1,2,2)")
        store = self.store(capacity=2)
        self.assertEqual(store.peek("eth", "a"), 1000)
        self.assertEqual(store.peek("eth", "b"), 2000)
        # Legacy entries had no access history: ties are deterministic by key.
        store.put_min("eth", "c", 3000)
        self.assertIsNone(store.peek("eth", "a"))
        self.assertEqual(store.peek("eth", "b"), 2000)
        store.get("eth", "b")
        store.put_min("eth", "d", 4000)
        self.assertIsNone(store.peek("eth", "c"))
        self.assertEqual(store.size(), 2)

    def test_legacy_epoch_rows_without_metadata_are_counted_once(self):
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute("CREATE TABLE first_appearances (chain TEXT NOT NULL, address TEXT NOT NULL, "
                           "epoch INTEGER NOT NULL, PRIMARY KEY(chain,address)) WITHOUT ROWID")
                db.executemany("INSERT INTO first_appearances VALUES (?,?,?)", [("eth", "a", 1000), ("eth", "b", 2000)])
        store = self.store(capacity=3)
        self.assertEqual(store.size(), 2)
        store.put_min("eth", "c", 3000)
        self.assertEqual(store.size(), 3)

    def test_parallel_process_connections_share_atomic_capacity(self):
        # Multiple instances use independent connections and metadata reads.
        stores = [self.store(capacity=5) for _ in range(4)]

        def put(index):
            # Retry explicit fail-fast SQLite contention; admission is still
            # atomic and must never overshoot the shared five-row limit.
            for attempt in range(5):
                try:
                    return stores[index % len(stores)].put_min("eth", str(index), 1000 + index)
                except sqlite3.OperationalError:
                    if attempt == 4:
                        raise
                    time.sleep(0.005)

        with ThreadPoolExecutor(max_workers=4) as executor:
            admitted = list(executor.map(put, range(20)))
        self.assertEqual(sum(admitted), 20)
        self.assertEqual(stores[0].size(), 5)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM first_appearances").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT MIN(last_access),MAX(last_access),COUNT(DISTINCT last_access) "
                                        "FROM first_appearances").fetchone(), (16, 20, 5))

    def test_epoch_requires_positive_nonfuture_integer(self):
        store = self.store()
        for epoch in (None, False, True, -1, 0, 1.2, "100", self.now + 1):
            with self.subTest(epoch=epoch), self.assertRaises(ValueError):
                store.put_min("eth", "address", epoch)
        self.assertEqual(store.size(), 0)

    def test_capacity_is_positive_integer_and_never_above_one_million(self):
        for capacity in (0, -1, True, 1.2, "1", 1_000_001):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                self.store(capacity)

    def test_missing_and_invalid_keys_do_not_create_entries(self):
        store = self.store()
        self.assertIsNone(store.get("eth", "missing"))
        for key in (("", "a"), ("eth", ""), (None, "a"), ("eth", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                store.get(*key)
        self.assertEqual(store.size(), 0)

    def test_in_memory_instances_are_isolated_and_read_across_connections(self):
        first, second = EpochStore(":memory:"), EpochStore(":memory:")
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.put_min("eth", "a", 1000)
        self.assertEqual(first.get("eth", "a"), 1000)
        self.assertIsNone(second.get("eth", "a"))


if __name__ == "__main__":
    unittest.main()
