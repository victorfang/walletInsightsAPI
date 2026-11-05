from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from eth_wallet_check.age_store import AgeStore


class AgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "ages.sqlite3"
        self.now = 1_800_000_000
        self.store = AgeStore(self.path, clock=lambda: self.now)
        self.addCleanup(self.store.close)

    def observe(self, timestamp, **kwargs):
        return self.store.observe("eth", "mainnet", "0xabc", timestamp, **kwargs)

    def test_persists_across_instances_without_expiry(self):
        self.observe(1_500_000_000, complete=True, finalized=True)
        self.now += 10 * 365 * 86400
        other = AgeStore(self.path, clock=lambda: self.now)
        self.addCleanup(other.close)
        row = other.get("eth", "mainnet", "0xabc")
        self.assertEqual(row["first_seen_timestamp"], 1_500_000_000)
        self.assertTrue(row["complete"])

    def test_only_earlier_observation_can_move_timestamp(self):
        self.observe(1_600_000_000, source="later", complete=True, finalized=True)
        self.observe(1_700_000_000, source="newer")
        row = self.observe(1_500_000_000, source="earlier", complete=False, finalized=False)
        self.assertEqual(row["first_seen_timestamp"], 1_500_000_000)
        self.assertEqual(row["source"], "earlier")
        self.assertFalse(row["complete"])
        self.assertFalse(row["finalized"])

    def test_equal_event_can_upgrade_flags_without_manufacturing_completeness(self):
        self.observe(1_500_000_000, block_number=12, block_hash="hash12", scope=["normal"], finalized=True)
        row = self.observe(1_500_000_000, block_number=12, block_hash="hash12", scope=["token"])
        self.assertEqual(row["scope"], ["normal", "token"])
        self.assertFalse(row["complete"])
        row = self.observe(1_500_000_000, block_number=12, block_hash="hash12", scope=["all"], complete=True)
        self.assertTrue(row["complete"])
        self.assertTrue(row["finalized"])
        self.assertEqual(row["scope"], ["all"])

    def test_equal_timestamp_unrelated_events_do_not_combine_quality(self):
        self.observe(1_500_000_000, block_number=1, complete=True)
        row = self.observe(1_500_000_000, block_number=2, finalized=True)
        self.assertTrue(row["finalized"])
        self.assertFalse(row["complete"])
        self.assertEqual(row["block_number"], 2)

    def test_conflicting_block_hashes_cannot_combine_finality_and_completeness(self):
        self.observe(1_500_000_000, block_number=12, block_hash="orphan", complete=True)
        row = self.observe(1_500_000_000, block_number=12, block_hash="canonical", finalized=True)
        self.assertEqual(row["block_hash"], "canonical")
        self.assertTrue(row["finalized"])
        self.assertFalse(row["complete"])

    def test_existing_database_can_add_block_hash_column(self):
        legacy_path = Path(self.directory.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as db:
            db.execute("CREATE TABLE address_first_seen (proto TEXT, network TEXT, address TEXT, first_seen_timestamp INTEGER, block_number INTEGER, transaction_hash TEXT, source TEXT, scope_json TEXT, complete INTEGER, finalized INTEGER, updated_at INTEGER, PRIMARY KEY(proto,network,address)) WITHOUT ROWID")
            db.commit()
        store = AgeStore(legacy_path, clock=lambda: self.now)
        self.addCleanup(store.close)
        store.observe("eth", "mainnet", "address", 1_500_000_000, block_hash="canonical", finalized=True)
        self.assertEqual(store.get("eth", "mainnet", "address")["block_hash"], "canonical")

    def test_namespace_and_native_case_are_preserved(self):
        for proto, network, address in [("eth", "mainnet", "Address"), ("btc", "mainnet", "Address"),
                                         ("eth", "testnet", "Address"), ("sol", "mainnet", "address")]:
            self.store.observe(proto, network, address, 1_500_000_000)
        self.assertIsNone(self.store.get("sol", "mainnet", "Address"))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM address_first_seen").fetchone()[0], 4)

    def test_null_absent_future_zero_and_wrong_type_are_not_saved(self):
        for value in [None, 0, -1, self.now + 1, True, 1.5, "1500000000"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.observe(value)
        self.assertIsNone(self.store.get("eth", "mainnet", "0xabc"))

    def test_returned_scope_is_an_independent_snapshot(self):
        row = self.observe(1_500_000_000, scope=["normal"])
        row["scope"].append("mutated")
        fetched = self.store.get("eth", "mainnet", "0xabc")
        self.assertEqual(fetched["scope"], ["normal"])
        fetched["scope"].clear()
        self.assertEqual(self.store.get("eth", "mainnet", "0xabc")["scope"], ["normal"])

    def test_writes_are_atomic_and_keep_the_minimum_across_threads(self):
        # Separate store instances exercise the database transaction, not a
        # process-local Python lock. Sequential small batches avoid lock storms.
        stores = [AgeStore(self.path, clock=lambda: self.now) for _ in range(4)]
        for store in stores:
            self.addCleanup(store.close)
        values = [1_600_000_000 + i for i in reversed(range(20))]
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda pair: stores[pair[0] % 4].observe("eth", "mainnet", "same", pair[1]), enumerate(values)))
        self.assertEqual(self.store.get("eth", "mainnet", "same")["first_seen_timestamp"], min(values))

    def test_schema_is_without_rowid_and_lock_wait_is_bounded(self):
        with self.store._connection() as db:
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='address_first_seen'").fetchone()[0]
            self.assertIn("WITHOUT ROWID", sql)
            self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 50)
            self.assertEqual(db.execute("PRAGMA cache_size").fetchone()[0], -4096)
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_memory_store_works_without_loading_all_rows(self):
        store = AgeStore(":memory:", clock=lambda: self.now)
        self.addCleanup(store.close)
        store.observe("sol", "mainnet", "Case", 1_500_000_000)
        self.assertEqual(store.get("sol", "mainnet", "Case")["first_seen_timestamp"], 1_500_000_000)


if __name__ == "__main__":
    unittest.main()
