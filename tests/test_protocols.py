import unittest

from eth_wallet_check.protocols import (
    COMING_SOON_MESSAGE,
    PROTOCOLS,
    address_key,
    canonical_proto,
    protocol_metadata,
)


class ProtocolTests(unittest.TestCase):
    def test_coming_soon_message_matches_public_contract(self):
        self.assertEqual(COMING_SOON_MESSAGE, "coming soon contact info@anchain.ai")

    def test_symbols_and_aliases_are_case_insensitive(self):
        for value, expected in {
            "eth": "eth", "ETH": "eth", "Ethereum": "eth",
            "BITCOIN": "btc", "btc": "btc", "Solana": "sol",
            "SOL": "sol", "TRON": "trx", "TrX": "trx",
        }.items():
            with self.subTest(value=value):
                self.assertEqual(canonical_proto(value), expected)

    def test_well_formed_future_identifiers_remain_available_to_dispatch(self):
        for value in ["doge", "NEW_CHAIN", "layer-2", "a" + "1" * 31]:
            with self.subTest(value=value):
                self.assertEqual(canonical_proto(value), value.lower())

    def test_malformed_identifiers_are_rejected_without_echoing_them(self):
        message = "proto is required and must be a protocol identifier"
        for value in [None, 1, [], "", " eth", "eth ", "eth\n", "1eth", "éth",
                      "ＥＴＨ", "eth/mainnet", "a" * 33, "https://example/SECRET"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as error:
                    canonical_proto(value)
                self.assertEqual(str(error.exception), message)

    def test_only_eth_is_enabled_and_units_are_chain_specific(self):
        self.assertEqual([key for key, value in PROTOCOLS.items() if value["available"]], ["eth"])
        for proto, symbol, decimals in [("eth", "ETH", 18), ("btc", "BTC", 8),
                                         ("sol", "SOL", 9), ("trx", "TRX", 6)]:
            with self.subTest(proto=proto):
                metadata = protocol_metadata(proto)
                self.assertEqual(metadata["balance_symbol"], symbol)
                self.assertEqual(metadata["balance_decimals"], decimals)
                self.assertEqual(metadata["network"], "mainnet")

    def test_registry_and_nested_entries_are_immutable(self):
        with self.assertRaises(TypeError):
            PROTOCOLS["btc"] = {}
        with self.assertRaises(TypeError):
            PROTOCOLS["eth"]["available"] = False
        detached = protocol_metadata("ethereum")
        detached["available"] = False
        self.assertTrue(protocol_metadata("eth")["available"])

    def test_unknown_metadata_does_not_invent_units_or_readiness(self):
        metadata = protocol_metadata("NEW_CHAIN")
        self.assertEqual(metadata["proto"], "new_chain")
        self.assertFalse(metadata["available"])
        self.assertIsNone(metadata["name"])
        self.assertIsNone(metadata["balance_symbol"])
        self.assertIsNone(metadata["balance_decimals"])

    def test_eth_key_keeps_existing_decimal_convention(self):
        address = "0x" + "aB" * 20
        self.assertEqual(address_key("Ethereum", address, True), str(int(address, 16)))
        self.assertEqual(address_key("ETH", "0x" + "0" * 40, True), "0")

    def test_invalid_eth_keeps_original_string(self):
        for address in ["bad", "0x123", "0x" + "a" * 40 + "\n"]:
            with self.subTest(address=address):
                self.assertEqual(address_key("eth", address, False), address)
        valid_string = "0x" + "a" * 40
        self.assertEqual(address_key("eth", valid_string, False), valid_string)

    def test_non_eth_addresses_preserve_case_and_never_use_eth_key_conversion(self):
        address = "0x" + "aB" * 20
        for proto in ["btc", "sol", "trx", "future"]:
            for value in [address, "SoLAnA123CaseSensitive", "TTrONCaseSensitive"]:
                with self.subTest(proto=proto, address=value):
                    self.assertEqual(address_key(proto, value, True), value)


if __name__ == "__main__":
    unittest.main()
