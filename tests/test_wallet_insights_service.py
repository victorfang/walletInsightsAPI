"""Request-level deadline, partial-evidence and independent-cache behavior."""

import copy
from concurrent.futures import Future
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from eth_wallet_check.deadline import BoundedExecutor
from eth_wallet_check.http import JSONHTTP
from eth_wallet_check.rpc import RPCResponseError
from wallet_age_days.service import WalletAgeError
from wallet_insights.cache import InsightsCache
from wallet_insights.service import WalletInsightsService


ADDRESS = "0x" + "ab" * 20
ASSETS = ({"symbol": "USDC", "address": "0x" + "12" * 20, "decimals": 6},)
NOW = 1_700_000_000


def snapshot(source="first", number=100, balance="0", *, complete=True):
    return {"source": source, "block": {"number": number, "hash": "0x" + "cd" * 32,
            "timestamp": NOW - 12}, "native_balance_wei": balance,
            "account_nonce": "0" if complete else None,
            "code_profile": {"kind": "empty_code", "code_size": 0, "delegation_target": None} if complete else None,
            "token_balances": [{**ASSETS[0], "balance_raw": "0" if complete else None,
                                "status": "available" if complete else "unavailable"}],
            "complete": complete, "warnings": []}


def labels(*, available=True):
    return {"status": "available" if available else "unavailable", "source": "anchain",
            "labels": [{"category": "exchange", "name": "Example entity"}] if available else None,
            "attribution": {"name": "Example entity"} if available else None,
            "checked_at": "2023-11-14T22:13:20Z" if available else None}


class WalletInsightsServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = NOW
        self.clock = lambda: self.now
        self.cache = InsightsCache(self.directory.name + "/insights.sqlite3", clock=self.clock)
        self.addCleanup(self.cache.close)
        self.rpc = Mock()
        self.rpc.endpoints = (("first", "https://first.test/PRIVATE"),)
        self.rpc.inspect_endpoint.return_value = snapshot()
        self.labels = Mock()
        self.labels.inspect.return_value = labels()
        self.executor = BoundedExecutor(max_workers=8)
        self.addCleanup(self.executor.shutdown)
        self.http = JSONHTTP()

    def service(self, *, budget=0.2, executor=None, cache=None):
        return WalletInsightsService(self.rpc, self.labels, cache or self.cache, self.http,
            ASSETS, clock=self.clock, budget=budget, executor=executor or self.executor)

    def check(self, service=None):
        return (service or self.service()).check("ETH", ADDRESS)

    def test_complete_snapshot_and_labels_have_independent_status_and_real_zeros(self):
        status, body = self.check()
        self.assertEqual((status, body["status"]), (200, "complete"))
        self.assertEqual(body["balances"]["native"]["balanceRaw"], "0")
        self.assertEqual(body["accountNonce"], "0")
        self.assertEqual(body["balances"]["tokens"][0]["balanceRaw"], "0")
        self.assertEqual(body["riskLabels"]["status"], "available")
        self.assertEqual(body["codeProfile"]["kind"], "empty_code")
        self.assertNotIn("PRIVATE", str(body))
        self.assertNotIn("safe", body)

    def test_slow_labels_return_partial_without_waiting_past_deadline(self):
        def slow(_address):
            time.sleep(0.2)
            return labels()
        self.labels.inspect.side_effect = slow
        started = time.monotonic()
        status, body = self.check(self.service(budget=0.05))
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertEqual((status, body["status"]), (200, "partial"))
        self.assertEqual(body["components"]["snapshot"], "complete")
        self.assertEqual(body["riskLabels"]["status"], "unavailable")
        self.assertIsNone(body["riskLabels"]["labels"])
        self.assertIsNone(body["riskLabels"]["attribution"])

    def test_slow_rpc_preserves_labels_and_unknown_balance_nonce_code(self):
        def slow(*_args):
            time.sleep(0.2)
            return snapshot()
        self.rpc.inspect_endpoint.side_effect = slow
        started = time.monotonic()
        status, body = self.check(self.service(budget=0.05))
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertEqual((status, body["status"]), (200, "partial"))
        self.assertEqual(body["riskLabels"]["status"], "available")
        self.assertIsNone(body["block"])
        self.assertIsNone(body["balances"]["native"]["balanceRaw"])
        self.assertIsNone(body["accountNonce"])
        self.assertIsNone(body["codeProfile"])
        self.assertIsNone(body["balances"]["tokens"][0]["balanceRaw"])

    def test_both_sources_failed_are_503_with_unknown_not_zero(self):
        self.rpc.inspect_endpoint.side_effect = RPCResponseError("Wrong chain")
        self.labels.inspect.side_effect = TimeoutError("SECRET")
        service = self.service()
        for _ in range(2):
            status, body = self.check(service)
            self.assertEqual((status, body["status"]), (503, "unavailable"))
            self.assertIsNone(body["balances"]["native"]["balanceRaw"])
            self.assertIsNone(body["accountNonce"])
            self.assertIsNone(body["riskLabels"]["labels"])
            self.assertFalse(body["cache"]["snapshot"]["hit"])
            self.assertFalse(body["cache"]["riskLabels"]["hit"])
            self.assertNotIn("SECRET", str(body))
        self.assertEqual(self.rpc.inspect_endpoint.call_count, 2)
        self.assertEqual(self.labels.inspect.call_count, 2)

    def test_wrong_chain_or_failed_rpc_falls_back_to_next_endpoint(self):
        self.rpc.endpoints = (("wrong-chain", "https://a.test"), ("failed", "https://b.test"),
                              ("public", "https://c.test"))
        self.rpc.inspect_endpoint.side_effect = [RPCResponseError("wrong chain"),
            TimeoutError(), snapshot(source="public", number=300, balance="123")]
        status, body = self.check()
        self.assertEqual(status, 200)
        self.assertEqual(body["block"]["source"], "public")
        self.assertEqual(body["block"]["number"], 300)
        self.assertEqual(body["balances"]["native"]["balanceRaw"], "123")
        self.assertEqual([call.args[0] for call in self.rpc.inspect_endpoint.call_args_list],
                         ["wrong-chain", "failed", "public"])

    def test_partial_provider_is_replaced_by_complete_coherent_snapshot(self):
        self.rpc.endpoints = (("partial", "https://a.test"), ("complete", "https://b.test"))
        self.rpc.inspect_endpoint.side_effect = [snapshot("partial", 100, "111", complete=False),
                                                snapshot("complete", 200, "222")]
        status, body = self.check()
        self.assertEqual((status, body["components"]["snapshot"]), (200, "complete"))
        self.assertEqual(body["block"]["number"], 200)
        self.assertEqual(body["block"]["source"], "complete")
        self.assertEqual(body["balances"]["native"]["balanceRaw"], "222")
        self.assertEqual(body["accountNonce"], "0")

    def test_simultaneously_completed_rpc_futures_do_not_race_pending_removal(self):
        self.rpc.endpoints = (("first", "https://a.test"), ("second", "https://b.test"))
        self.rpc.inspect_endpoint.side_effect = lambda name, *_args: snapshot(
            name, 100 if name == "first" else 200, "111" if name == "first" else "222")
        class ImmediateExecutor:
            def submit(self, fn, *args):
                future = Future()
                try:
                    future.set_result(fn(*args))
                except Exception as exc:
                    future.set_exception(exc)
                return future
        monotonic = [0.0]
        waits = [0]
        def ready_together(futures, **_kwargs):
            waits[0] += 1
            if waits[0] == 1:
                monotonic[0] = 0.7
                return set(), set(futures)
            return set(futures), set()
        with patch("wallet_insights.service.time.monotonic", side_effect=lambda: monotonic[0]), \
                patch("wallet_insights.service.wait", side_effect=ready_together):
            status, body = self.check(self.service(budget=2.6, executor=ImmediateExecutor()))
        self.assertEqual(status, 200)
        expected = {"first": (100, "111"), "second": (200, "222")}[body["block"]["source"]]
        self.assertEqual((body["block"]["number"], body["balances"]["native"]["balanceRaw"]), expected)

    def test_successful_components_expire_at_independent_15_and_300_second_boundaries(self):
        service = self.service()
        self.check(service)
        self.now = NOW + 14.999
        _status, cached = self.check(service)
        self.assertTrue(cached["cache"]["snapshot"]["hit"])
        self.assertTrue(cached["cache"]["riskLabels"]["hit"])
        self.assertEqual(self.rpc.inspect_endpoint.call_count, 1)
        self.assertEqual(self.labels.inspect.call_count, 1)
        self.now = NOW + 15
        _status, refreshed = self.check(service)
        self.assertFalse(refreshed["cache"]["snapshot"]["hit"])
        self.assertTrue(refreshed["cache"]["riskLabels"]["hit"])
        self.assertEqual(self.rpc.inspect_endpoint.call_count, 2)
        self.assertEqual(self.labels.inspect.call_count, 1)
        self.now = NOW + 300
        _status, refreshed = self.check(service)
        self.assertFalse(refreshed["cache"]["snapshot"]["hit"])
        self.assertFalse(refreshed["cache"]["riskLabels"]["hit"])
        self.assertEqual(self.rpc.inspect_endpoint.call_count, 3)
        self.assertEqual(self.labels.inspect.call_count, 2)

    def test_warm_components_schedule_no_workers(self):
        service = self.service()
        self.check(service)
        self.rpc.inspect_endpoint.reset_mock()
        self.labels.inspect.reset_mock()
        with patch.object(self.executor, "submit", wraps=self.executor.submit) as submitting:
            status, body = self.check(service)
        self.assertEqual((status, body["status"]), (200, "complete"))
        self.rpc.inspect_endpoint.assert_not_called()
        self.labels.inspect.assert_not_called()
        submitting.assert_not_called()

    def test_unavailable_labels_are_not_cached_or_turned_into_empty_available_labels(self):
        self.labels.inspect.side_effect = [labels(available=False), labels()]
        service = self.service()
        _status, first = self.check(service)
        self.assertEqual(first["status"], "partial")
        self.assertIsNone(first["riskLabels"]["labels"])
        self.assertEqual(first["riskLabels"]["status"], "unavailable")
        _status, second = self.check(service)
        self.assertTrue(second["cache"]["snapshot"]["hit"])
        self.assertFalse(second["cache"]["riskLabels"]["hit"])
        self.assertEqual(second["riskLabels"]["status"], "available")
        self.assertEqual(self.rpc.inspect_endpoint.call_count, 1)
        self.assertEqual(self.labels.inspect.call_count, 2)

    def test_partial_snapshots_are_not_cached_while_labels_can_be(self):
        self.rpc.inspect_endpoint.side_effect = [snapshot(complete=False), snapshot()]
        service = self.service()
        self.check(service)
        _status, second = self.check(service)
        self.assertFalse(second["cache"]["snapshot"]["hit"])
        self.assertTrue(second["cache"]["riskLabels"]["hit"])
        self.assertEqual(self.rpc.inspect_endpoint.call_count, 2)
        self.assertEqual(self.labels.inspect.call_count, 1)

    def test_late_workers_never_write_successful_components_after_return(self):
        def slow_rpc(*_args):
            time.sleep(0.15)
            return snapshot()
        def slow_labels(_address):
            time.sleep(0.15)
            return labels()
        self.rpc.inspect_endpoint.side_effect = slow_rpc
        self.labels.inspect.side_effect = slow_labels
        service = self.service(budget=0.05)
        status, body = self.check(service)
        before = copy.deepcopy(body)
        self.assertEqual(status, 503)
        self.executor.shutdown(wait=True)
        self.assertEqual(body, before)
        self.assertEqual(service.cache_probe(ADDRESS), {"snapshot": False, "riskLabels": False})

    def test_only_physical_ethereum_is_supported_and_invalid_format_does_no_io(self):
        cache = Mock()
        service = self.service(cache=cache)
        for blockchain in ("Polygon", "matic", "EVM", "btc", "sol"):
            with self.subTest(blockchain=blockchain), self.assertRaises(WalletAgeError) as error:
                service.check(blockchain, ADDRESS)
            self.assertEqual(error.exception.status, 501)
        for address in ("bad", "", "0X" + "a" * 40, ADDRESS + "\n", None, 1):
            with self.subTest(address=address), self.assertRaises(WalletAgeError) as error:
                service.check("ETH", address)
            self.assertEqual(error.exception.status, 400)
        cache.get.assert_not_called()
        self.rpc.inspect_endpoint.assert_not_called()
        self.labels.inspect.assert_not_called()

    def test_cache_lock_errors_bypass_cache_without_hiding_valid_metrics(self):
        cache = Mock()
        cache.get.side_effect = sqlite3.OperationalError("locked")
        cache.put.side_effect = sqlite3.OperationalError("locked")
        status, body = self.check(self.service(cache=cache))
        self.assertEqual((status, body["status"]), (200, "complete"))
        self.assertEqual(body["balances"]["native"]["balanceRaw"], "0")
        self.assertFalse(body["cache"]["snapshot"]["hit"])
        self.assertIsNone(body["cache"]["snapshot"]["expiresAt"])

    def test_block_only_snapshot_does_not_turn_missing_metrics_into_200(self):
        empty = snapshot(complete=False)
        empty["native_balance_wei"] = None
        self.rpc.inspect_endpoint.return_value = empty
        self.labels.inspect.return_value = labels(available=False)
        service = self.service()
        status, body = self.check(service)
        self.assertEqual((status, body["status"]), (503, "unavailable"))
        self.assertIsNone(body["balances"]["native"]["balanceRaw"])
        self.assertIsNone(body["accountNonce"])
        self.assertIsNone(body["codeProfile"])
        self.assertIsNone(body["block"])
        self.assertEqual(service.cache_probe(ADDRESS), {"snapshot": False, "riskLabels": False})


if __name__ == "__main__":
    unittest.main()
