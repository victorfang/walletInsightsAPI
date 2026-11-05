import unittest
from unittest.mock import Mock

from eth_wallet_check.dispatch import AddressAgeDispatcher


ETH_ADDRESS = "0x" + "a" * 40
MESSAGE = "coming soon contact info@anchain.ai"


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.eth = Mock()
        self.eth.check.return_value = {"address": ETH_ADDRESS, "valid_format": True,
                                       "status": "seen", "has_appeared": True, "is_new_wallet": False}
        self.dispatch = AddressAgeDispatcher({"eth": self.eth})

    def test_every_well_formed_non_eth_protocol_is_coming_soon_without_adapter_work(self):
        for proto in ["btc", "sol", "trx", "tron", "bitcoin", "solana", "BTC", "doge", "future_chain"]:
            with self.subTest(proto=proto):
                status, body = self.dispatch.check(proto, "NOT_AN_ETH_ADDRESS", details=True)
                self.assertEqual(status, 501)
                self.assertEqual(body, {"status": 501, "err_msg": MESSAGE, "data": {}})
        self.eth.check.assert_not_called()

    def test_missing_or_invalid_protocol_is_a_request_error(self):
        for proto in [None, "", "eth mainnet", "eth\n", "https://example.org", 1]:
            with self.subTest(proto=proto):
                self.assertEqual(self.dispatch.check(proto, ETH_ADDRESS)[0], 400)
        self.eth.check.assert_not_called()

    def test_eth_alias_dispatches_without_normalizing_address_in_router(self):
        mixed = "0x" + "Ab" * 20
        status, _ = self.dispatch.check("Ethereum", mixed, details=True)
        self.assertEqual(status, 200)
        self.eth.check.assert_called_once_with(mixed, details=True)

    def test_empty_address_remains_bad_request_for_every_protocol(self):
        for proto in ["eth", "btc", "sol", "trx"]:
            self.assertEqual(self.dispatch.check(proto, "")[0], 400)
        self.eth.check.assert_not_called()

    def test_future_chain_adapter_preserves_native_case_and_uses_native_units(self):
        address = "SoLCaseSensitiveAddress"
        future = Mock()
        future.check.return_value = {
            "address": address, "valid_format": True, "status": "seen",
            "has_appeared": True, "is_new_wallet": False,
            "balance_native": "1.234567891", "balance_raw": "1234567891",
        }
        dispatch = AddressAgeDispatcher({"eth": self.eth, "sol": future})
        status, body = dispatch.check("solana", address, details=True)
        self.assertEqual(status, 200)
        future.check.assert_called_once_with(address, details=True)
        self.eth.check.assert_not_called()
        record = body["data"][address]
        self.assertEqual(record["proto"], "sol")
        self.assertEqual(record["stats"]["balance_raw"], "1234567891")
        self.assertEqual(record["stats"]["balance_decimals"], 9)
        self.assertEqual(record["stats"]["balance_symbol"], "SOL")
        self.assertNotIn("balance_wei", record["stats"])
        self.assertNotIn("nonce", record["stats"])

    def test_duplicate_alias_adapter_registration_is_rejected(self):
        with self.assertRaises(ValueError):
            AddressAgeDispatcher({"eth": self.eth, "ethereum": Mock()})


if __name__ == "__main__":
    unittest.main()
