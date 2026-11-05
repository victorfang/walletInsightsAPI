"""Independent request-level checks for deadlines and immutable age observations."""

import copy
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from eth_wallet_check.age_store import AgeStore
from eth_wallet_check.api import format_result
from eth_wallet_check.cache import SQLiteCache
from eth_wallet_check.deadline import BoundedExecutor
from eth_wallet_check.fast_service import FastWalletChecker
from eth_wallet_check.http import JSONHTTP
from eth_wallet_check.service import utc_timestamp


ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
BLOCK_HASH = "0x" + "b" * 64
TX_HASH = "0x" + "a" * 64
FIRST_EPOCH = 1_600_000_000


def observation(*, seen=None, finalized=False, complete=False, epoch=None):
    result = {
        "source": "test-index", "seen": seen,
        "scope": ["normal_transactions", "token_transfers"],
        "evidence": [], "attempts": [], "warnings": [],
    }
    if epoch is not None:
        result.update({
            "first_seen": {"epoch": epoch, "timestamp": utc_timestamp(epoch),
                           "block_number": 123, "block_hash": BLOCK_HASH,
                           "transaction_hash": TX_HASH, "source": "test-index"},
            "first_seen_scope": ["normal_transactions"],
            "first_seen_complete": complete,
            "first_seen_finalized": finalized,
        })
    return result


class FastServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_700_000_000.0
        self.clock = lambda: self.now
        self.cache = SQLiteCache(self.directory.name + "/state.sqlite3", clock=self.clock)
        self.age_store = AgeStore(self.directory.name + "/age.sqlite3", clock=self.clock)
        self.addCleanup(self.cache.close)
        self.addCleanup(self.age_store.close)
        self.history = Mock()
        self.history.sources = ("test-index",)
        self.history.inspect_source.side_effect = lambda *_args, **_kwargs: observation()
        self.rpc = Mock()
        self.rpc.inspect.return_value = {
            "source": "test-node", "balance_wei": "0", "nonce": "0",
            "code_present": False, "block_number": 1000,
            "evidence": [], "attempts": [],
        }
        self.executor = BoundedExecutor(max_workers=8)
        self.addCleanup(self.executor.shutdown)
        self.http = JSONHTTP()

    def checker(self, *, budget=0.05, executor=None):
        return FastWalletChecker(
            self.history, self.rpc, self.cache, self.age_store, self.http,
            clock=self.clock, budget=budget, executor=executor or self.executor,
        )

    def stats(self, result):
        _status, envelope = format_result(result, clock=self.clock)
        return next(iter(envelope["data"].values()))["stats"]

    def test_slow_sources_cannot_extend_request_past_deadline(self):
        def slow_history(*_args, **_kwargs):
            time.sleep(0.3)
            return observation(seen=False)

        def slow_rpc(*_args, **_kwargs):
            time.sleep(0.3)
            return copy.deepcopy(self.rpc.inspect.return_value)

        self.history.sources = ("test-index", "other-index", "third-index")
        self.history.inspect_source.side_effect = slow_history
        self.rpc.inspect.side_effect = slow_rpc
        started = time.monotonic()
        result = self.checker().check(ADDRESS, details=True)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.2, f"Request waited {elapsed:.3f}s for unfinished sources")
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["is_new_wallet"])
        self.assertFalse(result["cache"]["stored"])
        self.assertIsNone(self.age_store.get("eth", "mainnet", ADDRESS))

    def test_exhausted_executor_returns_without_queueing_requests(self):
        executor = BoundedExecutor(max_workers=1)
        release = threading.Event()
        started_worker = threading.Event()

        def occupied():
            started_worker.set()
            release.wait(1)

        executor.submit(occupied)
        self.assertTrue(started_worker.wait(0.2))
        try:
            started = time.monotonic()
            result = self.checker(executor=executor).check(ADDRESS, details=True)
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertEqual(result["status"], "unknown")
            self.history.inspect_source.assert_not_called()
            self.rpc.inspect.assert_not_called()
        finally:
            release.set()
            executor.shutdown()

    def test_locked_ttl_database_does_not_extend_request_to_sqlite_default_timeout(self):
        connection = sqlite3.connect(self.directory.name + "/state.sqlite3")
        connection.execute("BEGIN EXCLUSIVE")
        try:
            started = time.monotonic()
            result = self.checker().check(ADDRESS, details=True)
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertFalse(result["cache"]["stored"])
            self.assertTrue(any("24-hour cache" in text for text in result["warnings"]))
        finally:
            connection.rollback()
            connection.close()

    def test_late_finalized_source_cannot_modify_a_returned_unknown_response(self):
        release = threading.Event()

        def delayed_history(*_args, **_kwargs):
            release.wait(1)
            return observation(seen=True, finalized=True, epoch=FIRST_EPOCH)

        self.history.inspect_source.side_effect = delayed_history
        checker = self.checker()
        try:
            result = checker.check(ADDRESS, details=True)
            self.assertEqual(result["status"], "unknown")
        finally:
            release.set()
            self.executor.shutdown()
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result.get("first_seen"))
        self.assertIsNone(self.age_store.get("eth", "mainnet", ADDRESS))
        self.assertFalse(result["age_cache"]["stored"])

    def test_unknown_history_and_zero_state_never_become_new_or_permanent(self):
        checker = self.checker()
        first = checker.check(ADDRESS, details=True)
        second = checker.check(ADDRESS, details=True)
        self.assertEqual(first["status"], "unknown")
        self.assertIsNone(first["has_appeared"])
        self.assertIsNone(first["is_new_wallet"])
        self.assertIsNone(self.stats(first)["age_days"])
        self.assertIsNone(self.stats(first)["first_txn"])
        self.assertFalse(second["cache"]["hit"])
        self.assertEqual(self.history.inspect_source.call_count, 2)
        self.assertIsNone(self.age_store.get("eth", "mainnet", ADDRESS))

    def test_provisional_timestamp_is_never_added_to_permanent_age_store(self):
        self.history.inspect_source.side_effect = lambda *_args, **_kwargs: observation(
            seen=True, finalized=False, complete=True, epoch=FIRST_EPOCH,
        )
        result = self.checker().check(ADDRESS, details=True)
        self.assertTrue(result["has_appeared"])
        self.assertEqual(self.stats(result)["age_status"], "provisional")
        self.assertIsNone(self.age_store.get("eth", "mainnet", ADDRESS))

    def test_finalized_observation_is_durable_even_when_scope_is_partial(self):
        self.history.inspect_source.side_effect = lambda *_args, **_kwargs: observation(
            seen=True, finalized=True, complete=False, epoch=FIRST_EPOCH,
        )
        result = self.checker().check(ADDRESS, details=True)
        self.assertTrue(result["has_appeared"])
        self.assertEqual(self.stats(result)["age_status"], "lower_bound")
        stored = self.age_store.get("eth", "mainnet", ADDRESS)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["first_seen_timestamp"], FIRST_EPOCH)
        self.assertEqual(stored["block_hash"], BLOCK_HASH)
        self.assertTrue(stored["finalized"])
        self.assertFalse(stored["complete"])

    def test_permanent_observation_survives_ttl_and_makes_zero_network_calls(self):
        self.age_store.observe(
            "eth", "mainnet", ADDRESS, FIRST_EPOCH,
            block_number=123, block_hash=BLOCK_HASH, transaction_hash=TX_HASH,
            source="test-index", scope=["normal_transactions"], finalized=True,
        )
        self.now += 10 * 365 * 86400
        result = self.checker().check(ADDRESS.upper().replace("0X", "0x"), details=False)
        self.assertTrue(result["has_appeared"])
        self.assertFalse(result["is_new_wallet"])
        self.assertTrue(result["age_cache"]["hit"])
        self.assertEqual(self.stats(result)["first_txn"], FIRST_EPOCH)
        self.history.inspect_source.assert_not_called()
        self.rpc.inspect.assert_not_called()

    def test_age_days_is_recomputed_from_epoch_on_each_response(self):
        self.age_store.observe(
            "eth", "mainnet", ADDRESS, FIRST_EPOCH,
            block_hash=BLOCK_HASH, transaction_hash=TX_HASH,
            source="test-index", scope=["normal_transactions"], finalized=True,
        )
        checker = self.checker()
        result = checker.check(ADDRESS, details=False)
        before = self.stats(result)["age_days"]
        expected = int((self.now - FIRST_EPOCH) // 86400)
        self.assertEqual(before, expected)
        self.now += 3 * 86400
        self.assertEqual(self.stats(result)["age_days"], expected + 3)
        self.assertEqual(self.stats(checker.check(ADDRESS, details=False))["age_days"], expected + 3)
        self.history.inspect_source.assert_not_called()
        self.rpc.inspect.assert_not_called()

    def test_permanent_age_keeps_epoch_while_mutable_details_expire(self):
        self.age_store.observe(
            "eth", "mainnet", ADDRESS, FIRST_EPOCH,
            block_hash=BLOCK_HASH, transaction_hash=TX_HASH,
            source="test-index", scope=["normal_transactions"], finalized=True,
        )
        checker = self.checker()
        first = checker.check(ADDRESS, details=True)
        self.assertEqual(first["balance_wei"], "0")
        self.assertEqual(self.stats(first)["first_txn"], FIRST_EPOCH)
        self.assertEqual(self.rpc.inspect.call_count, 1)
        self.rpc.inspect.return_value["balance_wei"] = "1000000000000000000"
        self.now += self.cache.ttl_seconds - 1
        cached = checker.check(ADDRESS, details=True)
        self.assertEqual(cached["balance_wei"], "0")
        self.assertTrue(cached["cache"]["hit"])
        self.assertEqual(self.rpc.inspect.call_count, 1)
        self.now += 1
        refreshed = checker.check(ADDRESS, details=True)
        self.assertEqual(refreshed["balance_wei"], "1000000000000000000")
        self.assertEqual(self.rpc.inspect.call_count, 2)
        self.assertTrue(refreshed["age_cache"]["hit"])
        self.assertEqual(self.stats(refreshed)["first_txn"], FIRST_EPOCH)

    def test_scoped_negative_expires_after_24_hours_and_is_not_durable(self):
        self.history.inspect_source.side_effect = lambda *_args, **_kwargs: observation(seen=False)
        checker = self.checker()
        first = checker.check(ADDRESS, details=True)
        self.assertEqual(first["status"], "no_observed_activity")
        self.assertTrue(first["is_new_wallet"])
        self.assertIsNone(self.stats(first)["age_days"])
        self.assertIsNone(self.age_store.get("eth", "mainnet", ADDRESS))
        self.now += self.cache.ttl_seconds - 1
        self.assertTrue(checker.check(ADDRESS, details=True)["cache"]["hit"])
        self.assertEqual(self.history.inspect_source.call_count, 1)
        self.now += 1
        self.assertFalse(checker.check(ADDRESS, details=True)["cache"]["hit"])
        self.assertEqual(self.history.inspect_source.call_count, 2)
        self.assertIsNone(self.age_store.get("eth", "mainnet", ADDRESS))

    def test_positive_chain_state_without_timestamp_has_unknown_age(self):
        self.rpc.inspect.return_value["nonce"] = "3"
        result = self.checker().check(ADDRESS, details=True)
        self.assertTrue(result["has_appeared"])
        self.assertIsNone(self.stats(result)["age_days"])
        self.assertIsNone(self.stats(result)["first_txn"])
        self.assertIsNone(self.age_store.get("eth", "mainnet", ADDRESS))


if __name__ == "__main__":
    unittest.main()
