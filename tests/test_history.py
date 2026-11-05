import copy
import unittest
from urllib.parse import parse_qs, urlparse

from eth_wallet_check.history import PublicHistory
from eth_wallet_check.http import SourceError


ADDRESS = "0xb6a9e65da42731fb83c0a2ceecb64275fa2d39d8"
OTHER = "0x" + "1" * 40
HASH = "0x" + "a" * 64
ACTIONS = ("txlist", "txlistinternal", "tokentx", "tokennfttx", "token1155tx")


def transaction(block=5, stamp=1500000000):
    return {"blockNumber": str(block), "timeStamp": str(stamp), "hash": HASH,
            "from": OTHER, "to": ADDRESS}


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.fail = {}
        self.info = {"hash": ADDRESS, "coin_balance": None,
                     "creation_transaction_hash": None,
                     **{key: False for key in ("has_token_transfers", "has_logs",
                         "has_beacon_chain_withdrawals", "has_validated_blocks",
                         "has_tokens", "is_contract")}}
        self.tabs = {key: 0 for key in ("transactions_count", "internal_transactions_count",
                     "token_transfers_count", "withdrawals_count", "validations_count",
                     "beacon_deposits_count", "logs_count", "token_balances_count")}
        self.counts = {"transactions_count": "0"}
        self.blockcypher = {"address": ADDRESS, "balance": 0, "n_tx": 0}
        self.histories = {source: {action: {"status": "1", "message": "OK", "result": []}
                                  for action in ACTIONS}
                          for source in ("blockscout", "routescan")}

    def __call__(self, url, payload=None):
        self.calls.append(url)
        parsed = urlparse(url)
        source = "blockscout" if "blockscout" in parsed.netloc else (
            "routescan" if "routescan" in parsed.netloc else "blockcypher")
        action = parse_qs(parsed.query).get("action", [None])[0]
        key = (source, action or parsed.path.rsplit("/", 1)[-1])
        if source in self.fail:
            raise SourceError(self.fail[source])
        if key in self.fail:
            raise SourceError(self.fail[key])
        if source == "blockcypher":
            value = self.blockcypher
        elif action:
            value = self.histories[source][action]
        elif parsed.path.endswith("/tabs-counters"):
            value = self.tabs
        elif parsed.path.endswith("/counters"):
            value = self.counts
        else:
            value = self.info
        return copy.deepcopy(value)


class PublicHistoryTests(unittest.TestCase):
    def test_never_observed_address_can_have_null_balance(self):
        http = FakeHTTP()
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIs(result["seen"], False)
        self.assertEqual(result["source"], "blockscout")
        self.assertNotIn("balance_wei", result)
        self.assertIn("internal_transactions", result["scope"])
        self.assertIn("beacon_withdrawals", result["scope"])
        self.assertEqual(len(http.calls), 2)

    def test_token_only_activity_is_seen_with_zero_nonce_and_balance(self):
        http = FakeHTTP()
        http.tabs["token_transfers_count"] = 1
        http.info["coin_balance"] = "0"
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["balance_wei"], "0")
        self.assertIn("token_transfers_count > 0", result["evidence"])

    def test_positive_balance_remains_evidence_when_activity_counters_fail(self):
        http = FakeHTTP()
        http.info["coin_balance"] = "1000000000000000000"
        http.fail[("blockscout", "tabs-counters")] = "HTTP 429"
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["source"], "blockscout")
        self.assertEqual(result["balance_wei"], "1000000000000000000")
        self.assertTrue(any("429" in item for item in result["warnings"]))

    def test_tabs_capped_count_is_never_reported_as_exact_count(self):
        http = FakeHTTP()
        http.tabs["transactions_count"] = 51
        http.counts["transactions_count"] = "78427"
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertEqual(result["transaction_count"], 78427)
        self.assertNotIn("first_seen", result)
        self.assertEqual(len(result["first_seen_scope"]), 5)

    def test_earliest_first_seen_survives_partial_optional_history_failure(self):
        http = FakeHTTP()
        http.tabs["transactions_count"] = 51
        http.histories["blockscout"]["txlist"]["result"] = [transaction(5)]
        http.histories["blockscout"]["txlistinternal"]["result"] = [transaction(2, 1400000000)]
        http.fail[("blockscout", "tokentx")] = "HTTP 429"
        http.fail[("routescan", "tokentx")] = "HTTP 429"
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["source"], "blockscout")
        self.assertEqual(result["first_seen"]["block_number"], 2)
        self.assertEqual(result["first_seen"]["kind"], "internal_transaction")
        self.assertEqual(result["first_seen"]["timestamp"], "2014-05-13T16:53:20Z")
        self.assertNotIn("erc20_transfers", result["first_seen_scope"])
        self.assertTrue(any("429" in warning for warning in result["warnings"]))

    def test_blockscout_partially_indexed_internal_row_is_observed_not_complete(self):
        http = FakeHTTP()
        row = transaction(2, 1400000000)
        row["transactionHash"] = row.pop("hash")
        http.histories["blockscout"]["txlistinternal"] = {
            "status": "2", "result": [row],
            "message": "Some internal transactions within this block range have not yet been processed"}
        http.fail[("routescan", "txlistinternal")] = "HTTP 503"
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["first_seen"]["transaction_hash"], HASH)
        self.assertEqual(result["first_seen"]["kind"], "internal_transaction")
        self.assertNotIn("internal_transactions", result["first_seen_scope"])
        self.assertTrue(any("incompletely indexed" in warning for warning in result["warnings"]))

    def test_optional_first_occurrence_uses_routescan_after_blockscout_rate_limit(self):
        http = FakeHTTP()
        http.tabs["transactions_count"] = 51
        http.counts["transactions_count"] = "78427"
        http.fail[("blockscout", "txlist")] = "HTTP 429"
        http.histories["routescan"]["txlist"]["result"] = [transaction(2, 1400000000)]
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["source"], "blockscout")
        self.assertEqual(result["transaction_count"], 78427)
        self.assertEqual(result["first_seen"]["block_number"], 2)
        self.assertEqual(result["first_seen"]["source"], "routescan")
        blockscout_actions = [parse_qs(urlparse(url).query)["action"][0] for url in http.calls
                             if "blockscout.com" in url and "action=" in url]
        self.assertEqual(blockscout_actions, ["txlist"])
        self.assertEqual(len(result["first_seen_scope"]), 5)
        self.assertTrue(any("429" in warning for warning in result["warnings"]))
        self.assertTrue(any("remaining history queries were skipped" in warning for warning in result["warnings"]))

    def test_partial_fallback_merges_best_candidates_and_warns_without_losing_seen(self):
        http = FakeHTTP()
        http.tabs["transactions_count"] = 1
        http.histories["blockscout"]["txlist"]["result"] = [transaction(5)]
        http.fail[("blockscout", "txlistinternal")] = "HTTP 429"
        http.histories["routescan"]["txlist"]["result"] = [transaction(2, 1400000000)]
        http.fail[("routescan", "tokentx")] = "HTTP 429"
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["first_seen"]["source"], "routescan")
        self.assertEqual(result["first_seen"]["block_number"], 2)
        self.assertEqual(result["first_seen_scope"], ["normal_transactions", "internal_transactions"])
        self.assertNotIn("erc20_transfers", result["first_seen_scope"])
        actions = [parse_qs(urlparse(url).query)["action"][0] for url in http.calls
                   if "routescan.io" in url]
        self.assertEqual(actions, ["txlist", "txlistinternal", "tokentx"])

    def test_count_rate_limit_skips_same_provider_history_requests(self):
        http = FakeHTTP()
        http.tabs["transactions_count"] = 1
        http.fail[("blockscout", "counters")] = "HTTP 429"
        http.histories["routescan"]["txlist"]["result"] = [transaction()]
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["first_seen"]["source"], "routescan")
        self.assertFalse(any("blockscout.com" in url and "action=" in url for url in http.calls))

    def test_malformed_first_source_fails_over_to_routescan_token_history(self):
        http = FakeHTTP()
        http.info["hash"] = OTHER
        http.histories["routescan"]["tokentx"]["result"] = [transaction()]
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["source"], "routescan")
        self.assertEqual([a["source"] for a in result["attempts"]], ["blockscout", "routescan"])
        self.assertEqual(result["attempts"][0]["status"], "error")
        self.assertNotIn("first_seen", result)

    def test_all_three_independent_sources_are_attempted_on_failures(self):
        http = FakeHTTP()
        http.fail["blockscout"] = "HTTP 429"
        http.fail["routescan"] = "HTTP 503"
        http.blockcypher.update({"balance": 12345678901234567890, "n_tx": 4})
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["source"], "blockcypher")
        self.assertEqual([a["source"] for a in result["attempts"]],
                         ["blockscout", "routescan", "blockcypher"])
        self.assertEqual(result["balance_wei"], "12345678901234567890")
        self.assertEqual(result["transaction_count"], 4)
        self.assertNotIn("first_seen", result)

    def test_routescan_all_empty_accepts_both_observed_empty_formats(self):
        http = FakeHTTP()
        http.fail["blockscout"] = "HTTP 503"
        http.histories["routescan"]["txlistinternal"] = {
            "status": "0", "message": "No transactions found", "result": []}
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIs(result["seen"], False)
        self.assertEqual(result["source"], "routescan")
        self.assertEqual(len(result["scope"]), 5)
        self.assertEqual(len(result["attempts"]), 2)

    def test_partial_empty_histories_and_zero_native_data_remain_unknown(self):
        http = FakeHTTP()
        http.fail["blockscout"] = "HTTP 503"
        http.fail[("routescan", "txlistinternal")] = "HTTP 429"
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIsNone(result["seen"])
        self.assertEqual(len(result["attempts"]), 3)
        self.assertEqual(result["attempts"][-1]["status"], "partial")

    def test_error_payload_cannot_be_misread_as_empty_history(self):
        http = FakeHTTP()
        http.fail["blockscout"] = "HTTP 503"
        http.histories["routescan"]["txlist"] = {
            "status": "0", "message": "NOTOK", "result": []}
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIsNone(result["seen"])

    def test_unrelated_transactions_cannot_prove_seen(self):
        http = FakeHTTP()
        http.fail["blockscout"] = "HTTP 503"
        row = transaction()
        row["to"] = OTHER
        http.histories["routescan"]["txlist"]["result"] = [row]
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIsNone(result["seen"])
        self.assertTrue(any("unrelated" in warning for warning in result["warnings"]))

    def test_malformed_blockcypher_count_is_a_failed_source(self):
        http = FakeHTTP()
        http.fail["blockscout"] = "HTTP 503"
        http.fail["routescan"] = "HTTP 429"
        http.blockcypher["n_tx"] = True
        result = PublicHistory(http).inspect(ADDRESS)
        self.assertIsNone(result["seen"])
        self.assertIsNone(result["source"])
        self.assertEqual(result["attempts"][-1]["status"], "error")

    def test_optional_exact_count_failure_preserves_seen(self):
        http = FakeHTTP()
        http.info["has_logs"] = True
        http.counts["transactions_count"] = "not a number"
        result = PublicHistory(http).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertNotIn("transaction_count", result)
        self.assertTrue(any("Exact transaction count" in item for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
