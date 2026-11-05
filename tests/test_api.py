import copy
import json
import unittest

from eth_wallet_check.api import api_error, format_result


ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"


class APIFormatterTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "address": ADDRESS,
            "valid_format": True,
            "status": "seen",
            "has_appeared": True,
            "is_new_wallet": False,
            "checked_at": "2026-11-05T16:00:00Z",
            "history_source": "blockscout",
            "rpc_source": "alchemy",
            "history_scope": ["normal_transactions"],
            "warnings": [],
            "limitations": ["Observed history only."],
            "cache": {"hit": True, "ttl_seconds": 86400, "expires_at": "2026-11-06T16:00:00Z"},
            "source_attempts": {"rpc": [{"url": "https://endpoint/SECRET_TOKEN"}]},
        }

    def record(self, envelope):
        return next(iter(envelope["data"].values()))

    def test_common_error_envelope(self):
        self.assertEqual(api_error(401, "Unauthorized"), {"status": 401, "err_msg": "Unauthorized", "data": {}})

    def test_conclusive_seen_matches_documented_envelope_and_field_names(self):
        status, data = format_result(self.result)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], 200)
        self.assertEqual(data["err_msg"], "")
        record = self.record(data)
        self.assertTrue(record["is_address_valid"])
        self.assertEqual(record["proto"], "eth")
        self.assertEqual(record["stats"], {"err": 0, "new_address": False, "has_appeared": True, "check_status": "seen"})

    def test_new_address_retains_true_classification(self):
        self.result.update(status="no_observed_activity", has_appeared=False, is_new_wallet=True)
        status, data = format_result(self.result)
        self.assertEqual(status, 200)
        self.assertTrue(self.record(data)["stats"]["new_address"])
        self.assertFalse(self.record(data)["stats"]["has_appeared"])

    def test_unknown_remains_null_and_has_error_status(self):
        self.result.update(status="unknown", has_appeared=None, is_new_wallet=None, error="https://endpoint/SECRET_TOKEN")
        status, data = format_result(self.result)
        self.assertEqual(status, 503)
        self.assertEqual(data["status"], 503)
        stats = self.record(data)["stats"]
        self.assertEqual(stats["err"], 1)
        self.assertIsNone(stats["new_address"])
        self.assertIsNone(stats["has_appeared"])
        self.assertNotIn("SECRET_TOKEN", json.dumps(data))

    def test_invalid_address_uses_original_key_and_clear_error(self):
        result = {"address": "bad-address", "status": "invalid_address", "valid_format": False,
                  "is_new_wallet": None, "has_appeared": None, "error": "Invalid Ethereum address format."}
        status, data = format_result(result)
        self.assertEqual(status, 400)
        self.assertIn("bad-address", data["data"])
        self.assertFalse(data["data"]["bad-address"]["is_address_valid"])
        self.assertEqual(data["err_msg"], result["error"])

    def test_mixed_case_addresses_share_decimal_key(self):
        first = format_result(self.result)[1]
        self.result["address"] = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
        second = format_result(self.result)[1]
        self.assertEqual(list(first["data"]), [str(int(ADDRESS, 16))])
        self.assertEqual(list(first["data"]), list(second["data"]))

    def test_optional_details_use_native_number_exact_wei_and_unix_seconds(self):
        self.result.update(
            balance_wei="1234567890123456789", balance_eth="1.234567890123456789",
            transaction_count=12, transaction_count_scope="normal_transactions",
            first_seen={"timestamp": "2025-01-01T00:00:00Z", "transaction_hash": "0x123", "block_number": 23},
            first_seen_scope=["normal_transactions"], nonce="7", code_present=False,
        )
        stats = self.record(format_result(self.result)[1])["stats"]
        self.assertIsInstance(stats["balance"], float)
        self.assertEqual(stats["balance_wei"], "1234567890123456789")
        self.assertEqual(stats["txns_total"], 12)
        self.assertEqual(stats["txns_scope"], "normal_transactions")
        self.assertEqual(stats["first_txn"], 1735689600)
        self.assertIsInstance(stats["first_txn"], int)
        self.assertEqual(stats["first_txn_hash"], "0x123")
        self.assertEqual(stats["first_txn_block"], 23)
        self.assertEqual(stats["nonce"], "7")
        self.assertFalse(stats["code_present"])

    def test_unavailable_optional_fields_stay_null(self):
        self.result.update(balance_wei=None, balance_eth=None, first_seen=None)
        stats = self.record(format_result(self.result)[1])["stats"]
        for name in ["balance", "balance_wei", "first_txn", "first_txn_hash", "first_txn_block", "txns_total"]:
            self.assertIsNone(stats[name], name)

    def test_basic_response_omits_optional_details(self):
        stats = self.record(format_result(self.result)[1])["stats"]
        for name in ["balance", "balance_wei", "first_txn", "txns_total", "nonce", "code_present"]:
            self.assertNotIn(name, stats)

    def test_cache_expiry_and_verdict_time_are_preserved(self):
        original = copy.deepcopy(self.result)
        data = format_result(self.result)[1]
        self.assertEqual(data["meta"]["cache"], self.result["cache"])
        self.assertEqual(data["verdict_time"], self.result["checked_at"])
        self.assertEqual(self.result, original)
        self.assertNotIn("SECRET_TOKEN", json.dumps(data))
        self.assertNotIn("source_attempts", data)
        self.assertEqual(data["meta"]["sources"], {"history": "blockscout", "rpc": "alchemy"})

    def test_malformed_optional_time_and_nonfinite_balance_are_not_json_errors(self):
        self.result.update(balance_wei="0", balance_eth="inf", first_seen={"timestamp": "bad"})
        data = format_result(self.result)[1]
        stats = self.record(data)["stats"]
        self.assertIsNone(stats["balance"])
        self.assertIsNone(stats["first_txn"])
        json.dumps(data, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
