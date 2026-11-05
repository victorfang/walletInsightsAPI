import copy
import unittest

from eth_wallet_check.rpc import DEFAULT_RPC_ENDPOINTS, PublicRPC


ADDRESS = "0x" + "12" * 20
ENDPOINTS = (("first", "https://first.test"), ("second", "https://second.test"),
             ("third", "https://third.test"))


def response(identifier, result):
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def fixture_http(overrides=None, balance="0x0", nonce="0x0", code="0x"):
    overrides = overrides or {}
    calls = []

    def http(url, payload=None):
        calls.append((url, payload))
        if url in overrides:
            value = overrides[url]
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(payload)
            return copy.deepcopy(value)
        if isinstance(payload, list):
            # JSON-RPC servers may reorder batch responses.
            return [response(5, code), response(3, balance), response(4, nonce)]
        if payload["method"] == "eth_chainId":
            return response(payload["id"], "0x1")
        return response(payload["id"], "0x123")

    return http, calls


class PublicRPCTests(unittest.TestCase):
    def test_three_independent_defaults(self):
        self.assertGreaterEqual(len(DEFAULT_RPC_ENDPOINTS), 3)
        self.assertEqual(len({url for _, url in DEFAULT_RPC_ENDPOINTS}),
                         len(DEFAULT_RPC_ENDPOINTS))

    def test_zero_state_reordered_batch_and_pinned_block(self):
        http, calls = fixture_http()
        result = PublicRPC(http, ENDPOINTS).inspect(ADDRESS)
        self.assertEqual(result["source"], "first")
        self.assertEqual(result["block_number"], 0x123)
        self.assertEqual(result["balance_wei"], "0")
        self.assertEqual(result["nonce"], "0")
        self.assertFalse(result["code_present"])
        self.assertEqual(result["evidence"], [])
        self.assertNotIn("is_new_wallet", result)
        self.assertNotIn("transaction_count", result)
        for request in calls[2][1]:
            self.assertEqual(request["params"], [ADDRESS, "0x123"])

    def test_positive_evidence(self):
        http, _ = fixture_http(balance="0x100", nonce="0x2", code="0xef0100")
        result = PublicRPC(http, ENDPOINTS).inspect(ADDRESS)
        self.assertEqual(result["balance_wei"], "256")
        self.assertEqual(result["nonce"], "2")
        self.assertTrue(result["code_present"])
        self.assertEqual(result["evidence"], ["nonzero_native_balance", "nonzero_nonce", "account_code"])

    def test_falls_back_to_third_after_failure_and_wrong_chain(self):
        def wrong_chain(payload):
            return response(payload["id"], "0xa")
        http, calls = fixture_http({ENDPOINTS[0][1]: TimeoutError("secret-url"),
                                   ENDPOINTS[1][1]: wrong_chain})
        result = PublicRPC(http, ENDPOINTS).inspect(ADDRESS)
        self.assertEqual(result["source"], "third")
        self.assertEqual([a["status"] for a in result["attempts"]], ["error", "error", "ok"])
        self.assertEqual(result["attempts"][0]["error"], "TimeoutError")
        self.assertNotIn("secret-url", str(result))

    def test_bad_batches_fall_back(self):
        invalid_batches = [
            {},
            [],
            [response(3, "0x0"), response(4, "0x0")],
            [response(3, "0x0"), response(3, "0x0"), response(5, "0x")],
            [response(3, "0x0"), response(4, "0x0"), response(6, "0x")],
            [response(3, "0x0"), response(4, "0x0"), response("5", "0x")],
            [response(3, "not hex"), response(4, "0x0"), response(5, "0x")],
            [response(3, "0x0"), response(4, "0x00"), response(5, "0x")],
            [response(3, "0x0"), response(4, "0x0"), response(5, "0x0")],
            [response(3, "0x0"), response(4, "0x0"), {"id": 5, "jsonrpc": "2.0", "error": {"message": "secret"}}],
            [response(3, "0x0"), response(4, "0x0"), {"id": 5, "jsonrpc": "2.0"}],
        ]
        for batch in invalid_batches:
            with self.subTest(batch=batch):
                def first(payload):
                    if isinstance(payload, list):
                        return batch
                    return response(payload["id"], "0x1")
                http, _ = fixture_http({ENDPOINTS[0][1]: first})
                result = PublicRPC(http, ENDPOINTS).inspect(ADDRESS)
                self.assertEqual(result["source"], "second")
                self.assertNotIn("secret", str(result))

    def test_malformed_single_responses_fall_back(self):
        for malformed in (None, [], {"jsonrpc": "2.0", "id": 99, "result": "0x1"},
                          {"jsonrpc": "1.0", "id": 1, "result": "0x1"},
                          {"jsonrpc": "2.0", "id": 1, "result": True}):
            with self.subTest(response=malformed):
                http, _ = fixture_http({ENDPOINTS[0][1]: lambda _: malformed})
                self.assertEqual(PublicRPC(http, ENDPOINTS).inspect(ADDRESS)["source"], "second")

    def test_all_fail_preserves_unknown(self):
        http, _ = fixture_http({url: OSError("do-not-leak") for _, url in ENDPOINTS})
        result = PublicRPC(http, ENDPOINTS).inspect(ADDRESS)
        self.assertIsNone(result["source"])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(len(result["attempts"]), 3)
        self.assertNotIn("balance_wei", result)
        self.assertNotIn("nonce", result)
        self.assertNotIn("do-not-leak", str(result))


if __name__ == "__main__":
    unittest.main()
