"""TTL freshness and durable least-recently-used cache replacement."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from wallet_insights.cache import InsightsCache


class WalletInsightsCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'insights.sqlite3'
        self.now = 1_800_000_000.0

    def cache(self, capacity=1_000_000):
        cache = InsightsCache(self.path, clock=lambda: self.now, capacity=capacity)
        self.addCleanup(cache.close)
        return cache

    def test_recent_read_keeps_old_insertion_and_evicts_lru(self):
        cache = self.cache(2)
        cache.put('a', {'balance': '1'}, 300)
        cache.put('b', {'balance': '2'}, 300)
        cache.get('a')
        cache.put('c', {'balance': '3'}, 300)
        self.assertIsNotNone(cache.peek('a'))
        self.assertIsNone(cache.peek('b'))
        self.assertIsNotNone(cache.peek('c'))
        self.assertEqual(cache.size(), 2)

    def test_peek_does_not_promote_an_entry(self):
        cache = self.cache(2)
        cache.put('a', {'a': 1}, 300)
        cache.put('b', {'b': 1}, 300)
        cache.peek('a')
        cache.put('c', {'c': 1}, 300)
        self.assertIsNone(cache.peek('a'))
        self.assertIsNotNone(cache.peek('b'))

    def test_overwrite_promotes_and_resets_only_written_ttl(self):
        cache = self.cache(2)
        cache.put('a', {'version': 1}, 15)
        old_b = cache.put('b', {}, 300)
        self.now += 5
        new_a = cache.put('a', {'version': 2}, 15)
        cache.put('c', {}, 300)
        self.assertIsNone(cache.peek('b'))
        self.assertEqual(cache.peek('a'), new_a)
        self.assertEqual(new_a['expires_at'], self.now+15)
        self.assertEqual(old_b['captured_at'], self.now-5)
        self.assertEqual(cache.size(), 2)

    def test_read_promotes_without_extending_freshness(self):
        cache = self.cache(2)
        original = cache.put('snapshot', {'nonce': '0'}, 15)
        self.now += 14.999
        self.assertEqual(cache.get('snapshot'), original)
        self.now = original['expires_at']
        self.assertIsNone(cache.get('snapshot'))
        self.assertIsNone(cache.peek('snapshot'))

    def test_snapshot_and_labels_share_capacity_with_independent_ttl(self):
        cache = self.cache(2)
        cache.put('snapshot:address', {}, 15)
        labels = cache.put('labels:address', {'labels': []}, 300)
        self.now += 15
        self.assertIsNone(cache.get('snapshot:address'))
        self.assertEqual(cache.get('labels:address'), labels)
        cache.put('snapshot:other', {'balance': '0'}, 15)
        self.assertEqual(cache.size(), 2)
        self.assertEqual(cache.peek('labels:address'), labels)

    def test_recent_but_expired_row_is_reclaimed_before_live_lru(self):
        cache = self.cache(2)
        cache.put('old_live', {}, 300)
        cache.put('recent_expired', {}, 1)
        self.now += 2
        cache.put('new', {}, 300)
        self.assertIsNotNone(cache.peek('old_live'))
        self.assertIsNone(cache.peek('recent_expired'))
        self.assertIsNotNone(cache.peek('new'))
        self.assertEqual(cache.size(), 2)

    def test_misses_and_expired_reads_do_not_change_recency(self):
        cache = self.cache(2)
        cache.put('a', {}, 300)
        cache.put('b', {}, 1)
        with closing(sqlite3.connect(self.path)) as db:
            before = db.execute('SELECT access_counter FROM wallet_insights_cache_metadata').fetchone()[0]
        self.now += 2
        self.assertIsNone(cache.get('missing'))
        self.assertIsNone(cache.get('b'))
        with closing(sqlite3.connect(self.path)) as db:
            after = db.execute('SELECT access_counter FROM wallet_insights_cache_metadata').fetchone()[0]
        self.assertEqual(before, after)

    def test_recency_survives_restart_and_capacity_cannot_be_enlarged(self):
        first = self.cache(2)
        first.put('a', {}, 300)
        first.put('b', {}, 300)
        first.get('a')
        first.close()
        reopened = self.cache(100)
        self.assertEqual(reopened.capacity, 2)
        reopened.put('c', {}, 300)
        self.assertIsNone(reopened.peek('b'))
        self.assertIsNotNone(reopened.peek('a'))

    def test_tighter_reopen_changes_shared_limit_and_other_writer_respects_it(self):
        first = self.cache(3)
        for key in ('a', 'b', 'c'):
            first.put(key, {}, 300)
        second = self.cache(2)
        self.assertEqual(second.size(), 2)
        self.assertIsNone(second.peek('a'))
        first.put('d', {}, 300)
        self.assertEqual(first.size(), 2)
        self.assertIsNone(second.peek('b'))
        self.assertIsNotNone(second.peek('c'))
        self.assertIsNotNone(second.peek('d'))

    def test_tighter_reopen_reclaims_more_than_64_expired_rows_before_live(self):
        first = self.cache(70)
        first.put('live', {'keep': True}, 300)
        for index in range(69):
            first.put(f'expired:{index:03}', {}, 1)
        self.now += 2
        second = self.cache(2)
        self.assertIsNotNone(second.peek('live'))
        self.assertEqual(second.size(), 2)
        second.put('new', {}, 300)
        self.assertIsNotNone(second.peek('live'))
        self.assertIsNotNone(second.peek('new'))
        self.assertEqual(second.size(), 2)

    def test_legacy_four_column_schema_migrates_without_losing_values_or_ttl(self):
        captured, expiry = self.now-5, self.now+295
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE wallet_insights_cache(cache_key TEXT PRIMARY KEY, '
                       'value_json TEXT NOT NULL,captured_at REAL NOT NULL,expires_at REAL NOT NULL) WITHOUT ROWID')
            db.executemany('INSERT INTO wallet_insights_cache VALUES (?,?,?,?)',
                           [(key, json.dumps({'key': key}), captured, expiry) for key in ('a', 'b')])
            db.commit()
        cache = self.cache(2)
        expected = {'value': {'key': 'b'}, 'captured_at': captured, 'expires_at': expiry}
        self.assertEqual(cache.get('b'), expected)
        cache.put('c', {}, 300)
        self.assertIsNone(cache.peek('a'))
        self.assertEqual(cache.peek('b'), expected)
        self.assertEqual(cache.size(), 2)

    def test_parallel_connections_never_exceed_shared_capacity_or_drift_counter(self):
        stores = [self.cache(5) for _ in range(4)]
        def write(index):
            for attempt in range(5):
                try:
                    return stores[index % 4].put(str(index), {'index': index}, 300)
                except sqlite3.OperationalError:
                    if attempt == 4:
                        raise
                    time.sleep(0.005)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(write, range(20)))
        self.assertEqual(len(results), 20)
        self.assertEqual(stores[0].size(), 5)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wallet_insights_cache').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT row_count FROM wallet_insights_cache_metadata').fetchone()[0], 5)

    def test_sequence_orders_reads_even_when_wall_clock_runs_backwards(self):
        cache = self.cache(2)
        cache.put('a', {}, 300)
        cache.put('b', {}, 300)
        self.now -= 1
        cache.get('a')
        cache.put('c', {}, 300)
        self.assertIsNone(cache.peek('b'))
        self.assertIsNotNone(cache.peek('a'))

    def test_invalid_limits_and_ttl_are_rejected_before_writing(self):
        for capacity in (0, -1, True, 1.5, '10', 1_000_001):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                self.cache(capacity)
        cache = self.cache(2)
        for ttl in (0, -1, True, '15', float('nan'), float('inf')):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                cache.put('bad', {}, ttl)
        self.assertEqual(cache.size(), 0)

    def test_memory_instances_are_isolated_and_json_values_are_not_shared(self):
        first = InsightsCache(':memory:')
        second = InsightsCache(':memory:')
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.put('a', {'items': [1]}, 300)
        value = first.get('a')
        value['value']['items'].append(2)
        self.assertEqual(first.get('a')['value']['items'], [1])
        self.assertIsNone(second.get('a'))


if __name__ == '__main__':
    unittest.main()
