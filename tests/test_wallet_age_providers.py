import copy
import unittest
from urllib.parse import parse_qs, urlsplit

from wallet_age_days.providers import AppearanceSources, Observation
from eth_wallet_check.http import SourceError


ADDRESS = "0x" + "1" * 40
OTHER = "0x" + "2" * 40
TX = "0x" + "a" * 64
BLOCK = "0x" + "b" * 64
FINAL = "0x" + "c" * 64
EPOCH = 1_500_000_000
RPC = (("publicnode", "https://rpc.example/PRIVATE"),)


def response(identifier, result):
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


class HTTP:
    def __init__(self):
        self.calls = []
        self.index = {"status": "1", "message": "OK", "result": [{
            "hash": TX, "blockNumber": "5", "timeStamp": str(EPOCH), "from": OTHER, "to": ADDRESS}]}
        self.receipt = {"transactionHash": TX, "blockNumber": "0x5", "blockHash": BLOCK,
                        "from": OTHER, "to": ADDRESS, "contractAddress": None, "logs": [], "status": "0x1"}
        self.block = {"number": "0x5", "hash": BLOCK, "timestamp": hex(EPOCH), "transactions": [TX]}
        self.head = {"number": "0xa", "hash": FINAL}
        self.chain = "0x1"
        self.fail_rpc = False
        self.alchemy = {"transfers": [{"hash": TX, "blockNum": "0x5", "from": ADDRESS,
                                      "to": ADDRESS, "category": "external",
                                      "metadata": {"blockTimestamp": "2017-07-14T02:40:00Z"}}]}
        self.quicknode = {"address": ADDRESS, "totalPages": 1, "totalItems": 1,
                          "paginatedItems": [{"transactionHash": TX, "blockNumber": "5",
                                              "blockTimestamp": "2017-07-14T02:40:00Z",
                                              "fromAddress": OTHER, "toAddress": ADDRESS}]}
        self.blockcypher = {"address": ADDRESS, "n_tx": 1, "hasMore": False,
                            "txrefs": [{"tx_hash": TX[2:], "block_height": 5,
                                        "confirmed": "2017-07-14T02:40:00Z"}]}

    def __call__(self, url, payload=None):
        self.calls.append((url, payload))
        if payload is not None:
            if self.fail_rpc:
                raise RuntimeError("https://rpc.example/PRIVATE")
            if isinstance(payload, list):
                return copy.deepcopy([response(4, self.head), response(3, self.block), response(2, self.chain)])
            method = payload["method"]
            if method == "eth_getTransactionReceipt":
                return response(1, copy.deepcopy(self.receipt))
            if method == "alchemy_getAssetTransfers":
                return response(1, copy.deepcopy(self.alchemy))
            if method == "qn_getTransactionsByAddress":
                return response(1, copy.deepcopy(self.quicknode))
            raise AssertionError(method)
        if "blockcypher" in url:
            return copy.deepcopy(self.blockcypher)
        return copy.deepcopy(self.index)


class AppearanceSourceTests(unittest.TestCase):
    def test_public_jobs_have_three_independent_indexed_providers(self):
        names = [name for name, _fn in AppearanceSources(HTTP(), RPC).jobs(ADDRESS)]
        self.assertEqual(names, ["routescan_normal", "routescan_creation", "blockscout_normal",
                                 "blockscout_creation", "blockcypher"])

    def test_configured_alchemy_directions_are_separate_jobs_and_qn_addon_is_opt_in(self):
        endpoints = (("alchemy", "https://alchemy.example/PRIVATE"),
                     ("quicknode", "https://quicknode.example/PRIVATE")) + RPC
        sources = AppearanceSources(HTTP(), endpoints)
        names = [name for name, _fn in sources.jobs(ADDRESS)]
        self.assertEqual(names[:2], ["alchemy_from", "alchemy_to"])
        self.assertNotIn("quicknode", names)
        sources = AppearanceSources(HTTP(), endpoints, quicknode_history_enabled=True)
        self.assertIn("quicknode", [name for name, _fn in sources.jobs(ADDRESS)])

    def test_oldest_normal_request_uses_one_row_and_canonical_finality_proof(self):
        http = HTTP()
        result = AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "normal")
        self.assertEqual(result, Observation(EPOCH, True, "routescan"))
        params = parse_qs(urlsplit(http.calls[0][0]).query)
        self.assertEqual(params["sort"], ["asc"])
        self.assertEqual(params["offset"], ["1"])
        methods = [p["method"] for _u, p in http.calls if isinstance(p, dict)]
        self.assertNotIn("eth_getBalance", methods)
        self.assertEqual(len(http.calls), 3)

    def test_etherscan_key_enables_v2_index_jobs_and_verified_candidate(self):
        http = HTTP()
        sources = AppearanceSources(http, RPC, etherscan_key="TEST_SCAN_KEY")
        self.assertIn("etherscan_normal", [name for name, _ in sources.jobs(ADDRESS)])
        self.assertIn("etherscan_creation", [name for name, _ in sources.jobs(ADDRESS)])
        result = sources._indexed("etherscan", ADDRESS, "normal")
        self.assertEqual(result, Observation(EPOCH, True, "etherscan"))
        url = urlsplit(http.calls[0][0])
        self.assertEqual((url.hostname, url.path), ("api.etherscan.io", "/v2/api"))
        params = parse_qs(url.query)
        self.assertEqual(params["chainid"], ["1"])
        self.assertEqual(params["apikey"], ["TEST_SCAN_KEY"])
        self.assertEqual(params["sort"], ["asc"])
        self.assertEqual(params["offset"], ["1"])

    def test_empty_oldest_history_stays_unknown(self):
        http = HTTP()
        http.index = {"status": "0", "message": "No transactions found", "result": []}
        self.assertIsNone(AppearanceSources(http, RPC)._indexed("blockscout", ADDRESS, "normal"))
        self.assertEqual(len(http.calls), 1)

    def test_rate_limit_body_is_not_treated_as_empty(self):
        http = HTTP()
        http.index = {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
        with self.assertRaises(SourceError):
            AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "normal")

    def test_unrelated_indexed_transaction_is_rejected_before_rpc(self):
        http = HTTP()
        http.index["result"][0].update({"from": OTHER, "to": OTHER})
        with self.assertRaises(SourceError):
            AppearanceSources(http, RPC)._indexed("blockscout", ADDRESS, "normal")
        self.assertEqual(len(http.calls), 1)

    def test_wrong_chain_or_noncanonical_event_is_never_finalized(self):
        for field in ("chain", "block"):
            http = HTTP()
            if field == "chain":
                http.chain = "0x89"
            else:
                http.block["hash"] = FINAL
            result = AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "normal")
            self.assertEqual(result, Observation(EPOCH, False, "routescan"))

    def test_index_candidate_after_rpc_failure_remains_provisional(self):
        http = HTTP()
        http.fail_rpc = True
        result = AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "normal")
        self.assertEqual(result, Observation(EPOCH, False, "routescan"))
        self.assertNotIn("PRIVATE", repr(result))

    def test_recent_nonfinalized_block_is_not_permanent_candidate(self):
        http = HTTP()
        http.head["number"] = "0x4"
        result = AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "normal")
        self.assertFalse(result.finalized)

    def test_contract_creation_verifies_receipt_and_uses_block_epoch(self):
        http = HTTP()
        http.index = {"status": "1", "result": [{"contractAddress": ADDRESS, "txHash": TX}]}
        http.receipt["contractAddress"] = ADDRESS
        result = AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "creation")
        self.assertEqual(result, Observation(EPOCH, True, "routescan"))

    def test_failed_or_unrelated_contract_creation_never_becomes_age(self):
        http = HTTP()
        http.index = {"status": "1", "result": [{"contractAddress": ADDRESS, "txHash": TX}]}
        http.receipt["contractAddress"] = OTHER
        with self.assertRaises(SourceError):
            AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "creation")

    def test_creation_index_cannot_attribute_a_null_contract_receipt(self):
        http = HTTP()
        http.index = {"status": "1", "result": [{"contractAddress": ADDRESS, "txHash": TX}]}
        http.receipt["contractAddress"] = None
        with self.assertRaises(SourceError):
            AppearanceSources(http, RPC)._indexed("routescan", ADDRESS, "creation")

    def test_alchemy_queries_ascending_zero_value_inclusive_first_transfer_only(self):
        http = HTTP()
        sources = AppearanceSources(http, RPC, alchemy_endpoint="https://alchemy.example/PRIVATE")
        result = sources._alchemy(ADDRESS, "to")
        self.assertEqual(result, Observation(EPOCH, True, "alchemy"))
        params = http.calls[0][1]["params"][0]
        self.assertEqual(params["toAddress"], ADDRESS)
        self.assertNotIn("fromAddress", params)
        self.assertEqual(params["maxCount"], "0x1")
        self.assertEqual(params["order"], "asc")
        self.assertIs(params["excludeZeroValue"], False)

    def test_alchemy_token_recipient_is_verified_in_receipt_topics(self):
        http = HTTP()
        http.receipt["from"] = OTHER
        http.receipt["to"] = OTHER
        http.receipt["logs"] = [{"address": OTHER, "topics": ["0x" + "0" * 24 + ADDRESS[2:]]}]
        http.alchemy["transfers"][0]["category"] = "erc20"
        result = AppearanceSources(http, RPC, alchemy_endpoint="https://alchemy.example")._alchemy(ADDRESS, "to")
        self.assertTrue(result.finalized)

    def test_alchemy_empty_history_never_returns_zero(self):
        http = HTTP()
        http.alchemy = {"transfers": []}
        self.assertIsNone(AppearanceSources(http, RPC, alchemy_endpoint="https://alchemy.example")._alchemy(ADDRESS, "from"))

    def test_quicknode_truncated_latest_page_cannot_be_first_appearance(self):
        http = HTTP()
        http.quicknode.update(totalPages=2, totalItems=101)
        endpoints = (("quicknode", "https://quicknode.example"),) + RPC
        with self.assertRaises(SourceError):
            AppearanceSources(http, endpoints)._quicknode(ADDRESS)
        self.assertEqual(len(http.calls), 1)

    def test_quicknode_complete_small_history_is_verified(self):
        http = HTTP()
        endpoints = (("quicknode", "https://quicknode.example"),) + RPC
        self.assertEqual(AppearanceSources(http, endpoints)._quicknode(ADDRESS), Observation(EPOCH, True, "quicknode"))
        self.assertEqual(http.calls[0][1]["method"], "qn_getTransactionsByAddress")

    def test_blockcypher_complete_small_history_is_verified(self):
        http = HTTP()
        self.assertEqual(AppearanceSources(http, RPC)._blockcypher(ADDRESS), Observation(EPOCH, True, "blockcypher"))
        self.assertTrue(http.calls[0][0].endswith("?limit=50"))

    def test_blockcypher_truncated_history_is_not_misreported_as_oldest(self):
        http = HTTP()
        http.blockcypher.update(n_tx=51, hasMore=True)
        with self.assertRaises(SourceError):
            AppearanceSources(http, RPC)._blockcypher(ADDRESS)
        self.assertEqual(len(http.calls), 1)


if __name__ == "__main__":
    unittest.main()
