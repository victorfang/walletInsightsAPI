import copy
import unittest

from eth_wallet_check.rpc import RPCResponseError
from wallet_insights.rpc import SnapshotRPC, code_profile


ADDRESS = "0x" + "AB" * 20
TOKEN = {"symbol": "USDC", "address": "0x" + "12" * 20, "decimals": 6}
NOW = 1_700_000_000
BLOCK = {"number": "0x123", "hash": "0x" + "cd" * 32, "timestamp": hex(NOW - 12)}
ENDPOINTS = (("alchemy", "https://rpc.example/PRIVATE_KEY"),)


def response(identifier, result):
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def error(identifier):
    return {"jsonrpc": "2.0", "id": identifier, "error": {"message": "PRIVATE_KEY"}}


class FakeHTTP:
    def __init__(self, *, head=None, states=None):
        self.calls = []
        self.head = head if head is not None else [response(2, copy.deepcopy(BLOCK)), response(1, "0x1")]
        self.states = states if states is not None else [
            response(6, "0x" + f"{123456789012345678901234567890:064x}"),
            response(5, "0x"), response(3, "0x100000000000000000000000000000000"), response(4, "0x5")]

    def __call__(self, url, payload=None):
        self.calls.append((url, copy.deepcopy(payload)))
        return copy.deepcopy(self.head if len(self.calls) == 1 else self.states)


class WalletInsightsRPCTests(unittest.TestCase):
    def inspect(self, http, assets=(TOKEN,)):
        return SnapshotRPC(http, ENDPOINTS, clock=lambda: NOW).inspect_endpoint(
            "alchemy", ENDPOINTS[0][1], ADDRESS, assets)

    def test_two_reordered_batches_pin_all_reads_and_preserve_exact_raw_balances(self):
        http = FakeHTTP()
        value = self.inspect(http)
        self.assertEqual(len(http.calls), 2)
        self.assertEqual([r["method"] for r in http.calls[0][1]], ["eth_chainId", "eth_getBlockByNumber"])
        for request in http.calls[1][1]:
            self.assertEqual(request["params"][-1], "0x123")
        self.assertEqual(http.calls[1][1][3]["params"][0], {
            "to": TOKEN["address"], "data": "0x70a08231" + ADDRESS[2:].lower().rjust(64, "0")})
        self.assertEqual(value["native_balance_wei"], str(2**128))
        self.assertEqual(value["token_balances"][0]["balance_raw"], "123456789012345678901234567890")
        self.assertEqual(value["account_nonce"], "5")
        self.assertNotIn("transaction_count", value)
        self.assertTrue(value["complete"])
        self.assertNotIn("PRIVATE_KEY", str(value))

    def test_zero_is_available_but_missing_or_failed_fields_are_null(self):
        http = FakeHTTP(states=[response(3, "0x0"), error(4), response(6, "0x" + "0" * 64)])
        value = self.inspect(http)
        self.assertEqual(value["native_balance_wei"], "0")
        self.assertIsNone(value["account_nonce"])
        self.assertIsNone(value["code_profile"])
        self.assertEqual(value["token_balances"][0]["balance_raw"], "0")
        self.assertEqual(value["token_balances"][0]["status"], "available")
        self.assertFalse(value["complete"])
        self.assertNotIn("PRIVATE_KEY", str(value))

    def test_failed_token_call_does_not_erase_valid_native_snapshot(self):
        http = FakeHTTP(states=[response(3, "0x0"), response(4, "0x0"), response(5, "0x6000"), error(6)])
        value = self.inspect(http)
        self.assertEqual(value["native_balance_wei"], "0")
        self.assertEqual(value["code_profile"]["kind"], "contract_code")
        self.assertIsNone(value["token_balances"][0]["balance_raw"])
        self.assertEqual(value["token_balances"][0]["status"], "unavailable")
        self.assertFalse(value["complete"])

    def test_empty_or_malformed_erc20_return_is_not_zero(self):
        for raw in ("0x", "0x0", "0x" + "a" * 63, "0x" + "a" * 66, None, True):
            with self.subTest(raw=raw):
                http = FakeHTTP()
                http.states[0] = response(6, raw)
                self.assertIsNone(self.inspect(http)["token_balances"][0]["balance_raw"])

    def test_wrong_chain_and_invalid_head_never_trigger_state_reads(self):
        fixtures = [
            [response(1, "0x89"), response(2, BLOCK)],
            [response(1, "0x1"), response(2, None)],
            [response(1, "0x1"), response(2, {**BLOCK, "hash": "0x1"})],
            [response(1, "0x1"), response(2, {**BLOCK, "number": "0x0"})],
            [response(1, "0x1"), response(2, {**BLOCK, "timestamp": hex(NOW - 301)})],
            [response(1, "0x1"), response(2, {**BLOCK, "timestamp": hex(NOW + 61)})],
            [response(1, "0x1"), error(2)],
        ]
        for head in fixtures:
            with self.subTest(head=head):
                http = FakeHTTP(head=head)
                with self.assertRaises(RPCResponseError):
                    self.inspect(http)
                self.assertEqual(len(http.calls), 1)

    def test_duplicate_unexpected_and_noninteger_ids_are_rejected(self):
        for states in ([response(3, "0x0"), response(3, "0x0")],
                       [response(99, "0x0")], [response("3", "0x0")],
                       [response(True, "0x0")], {"result": []}):
            with self.subTest(states=states), self.assertRaises(RPCResponseError):
                self.inspect(FakeHTTP(states=states))

    def test_malformed_quantity_or_code_only_invalidates_its_field(self):
        http = FakeHTTP(states=[response(3, "0x00"), response(4, "0x1"), response(5, "0x1")])
        value = self.inspect(http, ())
        self.assertIsNone(value["native_balance_wei"])
        self.assertEqual(value["account_nonce"], "1")
        self.assertIsNone(value["code_profile"])
        self.assertFalse(value["complete"])

    def test_code_profile_distinguishes_delegation_without_calling_it_an_eoa(self):
        self.assertEqual(code_profile("0x"), {"kind": "empty_code", "delegation_target": None, "code_size": 0})
        target = "ab" * 20
        self.assertEqual(code_profile("0xEF0100" + target.upper()),
                         {"kind": "delegated", "delegation_target": "0x" + target, "code_size": 23})
        self.assertEqual(code_profile("0xef0100")["kind"], "contract_code")
        self.assertEqual(code_profile("0x60006000")["kind"], "contract_code")
        self.assertNotIn("code_hash", code_profile("0x6000"))

    def test_configured_tokens_are_bounded_validated_and_deduplicated_before_network(self):
        bad = [None, [None], [{**TOKEN, "decimals": True}], [{**TOKEN, "address": "bad"}],
               [TOKEN, TOKEN], [TOKEN] * 17]
        for assets in bad:
            with self.subTest(assets=assets):
                http = FakeHTTP()
                with self.assertRaises(ValueError):
                    self.inspect(http, assets)
                self.assertEqual(http.calls, [])

    def test_invalid_wallet_address_never_calls_rpc(self):
        http = FakeHTTP()
        with self.assertRaises(ValueError):
            SnapshotRPC(http, ENDPOINTS).inspect_endpoint("alchemy", ENDPOINTS[0][1], "bad", ())
        self.assertFalse(http.calls)


if __name__ == "__main__":
    unittest.main()
