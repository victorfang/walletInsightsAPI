import copy
import unittest
from urllib.parse import parse_qs, urlparse

from eth_wallet_check.fast_history import FastHistory
from eth_wallet_check.http import SourceError


ADDRESS = "0x" + "1" * 40
OTHER = "0x" + "2" * 40
TX = "0x" + "a" * 64
BLOCK = "0x" + "b" * 64
FINAL = "0x" + "c" * 64
SECONDS = 1500000000
ENDPOINTS = (("test-rpc", "https://rpc.example/secret-key"),)


def rpc(identifier, value):
    return {"jsonrpc": "2.0", "id": identifier, "result": value}


class FastHTTP:
    def __init__(self):
        self.calls = []
        self.normal = {"status": "1", "message": "OK", "result": [{
            "blockNumber": "5", "timeStamp": str(SECONDS), "hash": TX,
            "blockHash": BLOCK, "from": OTHER, "to": ADDRESS,
        }]}
        self.creation = {"status": "1", "message": "OK", "result": [{
            "contractAddress": ADDRESS, "txHash": TX,
        }]}
        self.receipt = {"transactionHash": TX, "blockNumber": "0x5", "blockHash": BLOCK,
                        "from": OTHER, "to": ADDRESS, "contractAddress": None,
                        "status": "0x1", "logs": []}
        self.block = {"number": "0x5", "hash": BLOCK, "timestamp": hex(SECONDS), "transactions": [TX]}
        self.finalized = rpc(4, {"number": "0xa", "hash": FINAL})
        self.info = {"hash": ADDRESS, "coin_balance": None, "creation_transaction_hash": None,
                     **{key: False for key in ("has_token_transfers", "has_logs", "has_beacon_chain_withdrawals",
                                              "has_validated_blocks", "has_tokens", "is_contract")}}
        self.tabs = {key: 0 for key in ("transactions_count", "internal_transactions_count", "token_transfers_count",
                                      "withdrawals_count", "validations_count", "beacon_deposits_count",
                                      "logs_count", "token_balances_count")}
        self.fail_rpc = False

    def __call__(self, url, payload=None):
        self.calls.append((url, payload))
        if payload is not None:
            if self.fail_rpc:
                raise RuntimeError("Provider request failed at https://rpc.example/secret-key")
            if isinstance(payload, list):
                return copy.deepcopy([self.finalized, rpc(3, self.block), rpc(2, "0x1")])
            return rpc(1, copy.deepcopy(self.receipt))
        action = parse_qs(urlparse(url).query).get("action", [None])[0]
        if action == "txlist":
            return copy.deepcopy(self.normal)
        if action == "getcontractcreation":
            return copy.deepcopy(self.creation)
        if url.endswith("/tabs-counters"):
            return copy.deepcopy(self.tabs)
        if "blockscout.com" in url:
            return copy.deepcopy(self.info)
        if "blockcypher.com" in url:
            return {"address": ADDRESS, "balance": 0, "n_tx": 0}
        raise AssertionError("Unexpected HTTP request")


class FastHistoryTests(unittest.TestCase):
    def test_presence_only_uses_two_blockscout_calls_even_with_details(self):
        http = FastHTTP()
        result = FastHistory(http).inspect_source("blockscout", ADDRESS, details=True)
        self.assertIs(result["seen"], False)
        self.assertEqual(len(http.calls), 2)
        self.assertNotIn("first_seen", result)
        self.assertNotIn("transaction_count", result)  # tabs counters are capped

    def test_empty_normal_history_does_not_prove_unused(self):
        http = FastHTTP()
        http.normal = {"status": "0", "message": "No transactions found", "result": []}
        result = FastHistory(http, ENDPOINTS).inspect_source("routescan", ADDRESS)
        self.assertIsNone(result["seen"])
        self.assertEqual(len(http.calls), 1)
        self.assertNotIn("first_seen", result)
        self.assertEqual(result["scope"], ["normal_transactions"])

    def test_normal_observation_is_finalized_but_never_global_earliest(self):
        http = FastHTTP()
        result = FastHistory(http, ENDPOINTS).inspect_source("routescan", ADDRESS)
        self.assertIs(result["seen"], True)
        self.assertIs(result["first_seen_complete"], False)
        self.assertIs(result["first_seen_finalized"], True)
        self.assertEqual(result["first_seen"]["epoch"], SECONDS)
        self.assertEqual(result["first_seen"]["block_hash"], BLOCK)
        self.assertEqual(len(http.calls), 3)
        action = parse_qs(urlparse(http.calls[0][0]).query)
        self.assertEqual(action["offset"], ["1"])
        self.assertEqual(action["sort"], ["asc"])

    def test_rpc_failure_retains_observation_without_permanent_cache_qualification(self):
        http = FastHTTP()
        http.fail_rpc = True
        result = FastHistory(http, ENDPOINTS).inspect_source("routescan", ADDRESS)
        self.assertIs(result["seen"], True)
        self.assertIs(result["first_seen_finalized"], False)
        self.assertEqual(result["verification_attempts"][0]["error"], "RuntimeError")
        self.assertNotIn("secret-key", str(result))

    def test_unrelated_receipt_cannot_finalize_an_indexed_candidate(self):
        http = FastHTTP()
        http.receipt["to"] = OTHER
        result = FastHistory(http, ENDPOINTS).inspect_source("routescan", ADDRESS)
        self.assertIs(result["first_seen_finalized"], False)
        self.assertEqual(len(http.calls), 2)

    def test_unsupported_finalized_tag_does_not_invent_finality(self):
        http = FastHTTP()
        http.finalized = {"jsonrpc": "2.0", "id": 4, "error": {"code": -32602, "message": "unsupported"}}
        result = FastHistory(http, ENDPOINTS).inspect_source("routescan", ADDRESS)
        self.assertIs(result["first_seen_finalized"], False)
        self.assertEqual(result["first_seen"]["block_hash"], BLOCK)

    def test_canonical_receipt_and_block_verify_factory_creation(self):
        http = FastHTTP()
        http.receipt["to"] = OTHER
        http.receipt["logs"] = [{"address": ADDRESS}]
        result = FastHistory(http, ENDPOINTS).inspect_source("contract_creation", ADDRESS)
        self.assertIs(result["first_seen_finalized"], True)
        self.assertIs(result["first_seen_complete"], False)
        self.assertEqual(result["first_seen"]["kind"], "contract_deployment")
        self.assertEqual(result["first_seen_scope"], ["contract_deployment"])
        self.assertFalse(any("receipt alone does not prove" in warning for warning in result["warnings"]))

    def test_noncanonical_deployment_does_not_supply_age(self):
        http = FastHTTP()
        http.receipt["contractAddress"] = ADDRESS
        http.block["hash"] = FINAL
        result = FastHistory(http, ENDPOINTS).inspect_source("contract_creation", ADDRESS)
        self.assertIs(result["seen"], True)  # the index is still positive presence
        self.assertNotIn("first_seen", result)

    def test_upstream_error_never_becomes_empty_history(self):
        http = FastHTTP()
        http.normal = {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
        result = FastHistory(http, ENDPOINTS).inspect_source("routescan", ADDRESS)
        self.assertIsNone(result["seen"])
        self.assertEqual(result["attempts"][0]["status"], "error")

    def test_three_keyless_providers_are_available_as_fallbacks(self):
        http = FastHTTP()

        def fallback(url, payload=None):
            if "routescan.io" in url or "blockscout.com" in url:
                raise SourceError("HTTP 429")
            data = http(url, payload)
            data["n_tx"] = 3
            return data

        result = FastHistory(fallback, ()).inspect(ADDRESS, details=True)
        self.assertIs(result["seen"], True)
        self.assertEqual(result["transaction_count"], 3)
        self.assertEqual([attempt["source"] for attempt in result["attempts"]],
                         ["routescan", "blockscout", "blockcypher"])


if __name__ == "__main__":
    unittest.main()
