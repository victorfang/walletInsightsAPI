"""Cache behavior at the service's externally meaningful lifetime boundary."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from eth_wallet_check.cache import SQLiteCache


class SQLiteCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "wallets.sqlite3"
        self.now = 1_800_000_000.0
        self.cache = SQLiteCache(self.path, clock=lambda: self.now)

    def test_entries_survive_a_new_cache_instance(self) -> None:
        self.cache.set("ethereum:0xabc", {"status": "seen", "balance_wei": "12"})
        self.cache.close()
        other = SQLiteCache(self.path, clock=lambda: self.now)
        self.assertEqual(
            other.get("ethereum:0xabc"), {"status": "seen", "balance_wei": "12"}
        )

    def test_expiration_is_exactly_twenty_four_hours(self) -> None:
        self.cache.set("wallet", {"seen": False})
        self.now += 86400 - 0.001
        self.assertEqual(self.cache.get("wallet"), {"seen": False})
        self.now += 0.001
        self.assertIsNone(self.cache.get("wallet"))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0],
                0,
            )

    def test_reads_do_not_extend_lifetime(self) -> None:
        self.cache.set("wallet", {"seen": True})
        self.now += 86399
        self.assertIsNotNone(self.cache.get("wallet"))
        self.now += 1
        self.assertIsNone(self.cache.get("wallet"))

    def test_values_are_isolated_from_mutation(self) -> None:
        original = {"evidence": [{"source": "publicnode"}]}
        self.cache.set("wallet", original)
        original["evidence"][0]["source"] = "changed"
        first_read = self.cache.get("wallet")
        self.assertEqual(first_read["evidence"][0]["source"], "publicnode")
        first_read["evidence"].clear()
        self.assertEqual(
            self.cache.get("wallet"), {"evidence": [{"source": "publicnode"}]}
        )

    def test_overwrite_restarts_the_expiration(self) -> None:
        self.cache.set("wallet", {"balance_wei": "1"})
        self.now += 86400 - 1
        self.cache.set("wallet", {"balance_wei": "2"})
        self.now += 1
        self.assertEqual(self.cache.get("wallet"), {"balance_wei": "2"})
        self.now += 86400 - 1
        self.assertIsNone(self.cache.get("wallet"))

    def test_writes_purge_other_expired_entries(self) -> None:
        self.cache.set("old", {"seen": False})
        self.now += 86400
        self.cache.set("new", {"seen": True})
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                connection.execute("SELECT cache_key FROM cache_entries").fetchall(),
                [("new",)],
            )

    def test_parallel_request_threads_can_share_a_cache(self) -> None:
        # Exercise persistence under contention rather than the production
        # fail-fast lock policy. Request-level tests cover a busy cache's
        # deadline and safe bypass separately.
        self.cache.timeout = 2.0
        def write_and_read(index: int) -> dict:
            key = f"wallet:{index}"
            self.cache.set(key, {"index": index})
            return self.cache.get(key)

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(write_and_read, range(32)))
        self.assertEqual(results, [{"index": index} for index in range(32)])

    def test_in_memory_cache_works_with_separate_connections(self) -> None:
        cache = SQLiteCache(":memory:", clock=lambda: self.now)
        self.addCleanup(cache.close)
        cache.set("wallet", {"seen": True})
        self.assertEqual(cache.get("wallet"), {"seen": True})

    def test_non_positive_or_non_finite_lifetimes_are_rejected(self) -> None:
        for ttl in (0, -1, float("nan"), float("inf")):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                SQLiteCache(self.path, ttl_seconds=ttl)

    def test_missing_key_returns_none(self) -> None:
        self.assertIsNone(self.cache.get("missing"))


if __name__ == "__main__":
    unittest.main()
