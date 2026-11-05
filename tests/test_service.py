from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from unittest.mock import Mock

from eth_wallet_check.cache import SQLiteCache
from eth_wallet_check.service import WalletChecker, valid_address, wei_to_eth


ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_700_000_000.0
        self.clock = lambda: self.now
        self.cache = SQLiteCache(self.directory.name + "/cache.sqlite3", clock=self.clock)
        self.history = Mock()
        self.history.inspect.return_value = {
            "source": "index", "seen": False, "scope": ["transactions", "token_transfers"],
            "evidence": [], "attempts": [],
        }
        self.rpc = Mock()
        self.rpc.inspect.return_value = {
            "source": "node", "block_number": 123, "balance_wei": "0", "nonce": "0",
            "code_present": False, "evidence": [], "attempts": [],
        }
        self.checker = WalletChecker(self.history, self.rpc, self.cache, clock=self.clock)

    def test_regex_full_string_only_and_invalid_types(self):
        for address in [ADDRESS, ADDRESS.lower(), "0x" + "0" * 40]:
            self.assertTrue(valid_address(address))
        for address in [None, 2, "0x123", ADDRESS + "\n", " " + ADDRESS, ADDRESS + "g", "0X" + "0" * 40]:
            self.assertFalse(valid_address(address), address)

    def test_invalid_address_does_not_touch_network(self):
        result = self.checker.check("bad")
        self.assertFalse(result["valid_format"])
        self.assertEqual(result["status"], "invalid_address")
        self.history.inspect.assert_not_called()
        self.rpc.inspect.assert_not_called()

    def test_confirmed_absence_returns_scoped_new_candidate(self):
        result = self.checker.check(ADDRESS)
        self.assertEqual(result["status"], "no_observed_activity")
        self.assertFalse(result["has_appeared"])
        self.assertTrue(result["is_new_wallet"])
        self.assertTrue(result["cache"]["stored"])

    def test_token_only_history_proves_seen_with_zero_native_state(self):
        self.history.inspect.return_value.update(seen=True, evidence=["token transfer"])
        result = self.checker.check(ADDRESS)
        self.assertTrue(result["has_appeared"])
        self.assertFalse(result["is_new_wallet"])
        self.assertIn("token transfer", result["evidence"])

    def test_each_positive_state_signal_overrides_empty_index(self):
        for signal in [{"balance_wei": "1"}, {"nonce": "1"}, {"code_present": True}]:
            with self.subTest(signal=signal):
                self.rpc.inspect.return_value.update(balance_wei="0", nonce="0", code_present=False)
                self.rpc.inspect.return_value.update(signal)
                cache = SQLiteCache(":memory:")
                self.addCleanup(cache.close)
                checker = WalletChecker(self.history, self.rpc, cache, clock=self.clock)
                self.assertTrue(checker.check(ADDRESS)["has_appeared"])

    def test_no_history_and_zero_rpc_is_unknown_and_not_cached(self):
        self.history.inspect.return_value.update(source=None, seen=None)
        first = self.checker.check(ADDRESS)
        second = self.checker.check(ADDRESS)
        self.assertEqual(first["status"], "unknown")
        self.assertIsNone(first["is_new_wallet"])
        self.assertFalse(first["cache"]["stored"])
        self.assertEqual(self.history.inspect.call_count, 2)
        self.assertFalse(second["cache"]["hit"])

    def test_empty_history_without_rpc_is_unknown(self):
        self.rpc.inspect.return_value = {"source": None, "attempts": [], "evidence": []}
        self.assertIsNone(self.checker.check(ADDRESS)["has_appeared"])

    def test_seen_history_remains_known_when_rpc_is_unavailable(self):
        self.history.inspect.return_value.update(seen=True)
        self.rpc.inspect.return_value = {"source": None, "attempts": [], "evidence": []}
        result = self.checker.check(ADDRESS, details=True)
        self.assertTrue(result["has_appeared"])
        self.assertIsNone(result["balance_wei"])
        self.assertTrue(result["warnings"])

    def test_normalized_cache_lasts_24h_without_sliding(self):
        result = self.checker.check(ADDRESS)
        self.now += 86_399
        hit = self.checker.check(ADDRESS.lower())
        self.assertTrue(hit["cache"]["hit"])
        self.assertEqual(result["cache"]["expires_at"], hit["cache"]["expires_at"])
        self.assertEqual(self.history.inspect.call_count, 1)
        self.now += 1
        self.assertFalse(self.checker.check(ADDRESS)["cache"]["hit"])
        self.assertEqual(self.history.inspect.call_count, 2)

    def test_basic_and_detailed_cache_are_distinct(self):
        self.checker.check(ADDRESS)
        detailed = self.checker.check(ADDRESS, details=True)
        self.assertIn("balance_wei", detailed)
        self.assertEqual(self.history.inspect.call_count, 2)
        self.assertTrue(self.checker.check(ADDRESS, details=True)["cache"]["hit"])

    def test_nonce_is_not_used_as_transaction_count(self):
        self.rpc.inspect.return_value["nonce"] = "250"
        self.history.inspect.return_value.update(transaction_count=123, count_scope="normal transactions")
        detailed = self.checker.check(ADDRESS, details=True)
        self.assertEqual(detailed["transaction_count"], 123)
        self.assertEqual(detailed["nonce"], "250")

    def test_concurrent_identical_misses_call_sources_once(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.checker.check(ADDRESS), range(8)))
        self.assertEqual(self.history.inspect.call_count, 1)
        self.assertEqual(sum(not row["cache"]["hit"] for row in results), 1)

    def test_balance_format_does_not_round_large_integers(self):
        self.assertEqual(wei_to_eth("1"), "0.000000000000000001")
        self.assertEqual(wei_to_eth("1000000000000000000"), "1")
        self.assertEqual(wei_to_eth("12345678901234567890123456789"), "12345678901.234567890123456789")

    def test_rpc_first_positive_skips_history_and_then_all_network_on_cache_hit(self):
        self.rpc.inspect.return_value["nonce"] = "1"
        checker = WalletChecker(self.history, self.rpc, self.cache, clock=self.clock, rpc_first=True)
        result = checker.check(ADDRESS)
        self.assertTrue(result["has_appeared"])
        self.assertTrue(result["history_skipped"])
        self.assertEqual(result["warnings"], [])
        self.history.inspect.assert_not_called()
        self.assertTrue(checker.check(ADDRESS)["cache"]["hit"])
        self.assertEqual(self.rpc.inspect.call_count, 1)

    def test_rpc_first_zero_state_still_checks_token_history(self):
        self.history.inspect.return_value["seen"] = True
        checker = WalletChecker(self.history, self.rpc, self.cache, clock=self.clock, rpc_first=True)
        result = checker.check(ADDRESS)
        self.assertTrue(result["has_appeared"])
        self.assertFalse(result["history_skipped"])
        self.history.inspect.assert_called_once_with(ADDRESS.lower(), details=False)

    def test_rpc_first_details_always_fetches_optional_history(self):
        self.rpc.inspect.return_value["nonce"] = "1"
        checker = WalletChecker(self.history, self.rpc, self.cache, clock=self.clock, rpc_first=True)
        result = checker.check(ADDRESS, details=True)
        self.assertFalse(result["history_skipped"])
        self.history.inspect.assert_called_once_with(ADDRESS.lower(), details=True)


if __name__ == "__main__":
    unittest.main()
