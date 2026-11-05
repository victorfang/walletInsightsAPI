import unittest
from unittest.mock import patch

from eth_wallet_check.config import rpc_endpoints
from eth_wallet_check.rpc import DEFAULT_RPC_ENDPOINTS


class ConfigTests(unittest.TestCase):
    def test_public_only_needs_no_configuration(self):
        self.assertEqual(rpc_endpoints({}), DEFAULT_RPC_ENDPOINTS)
        self.assertGreaterEqual(len(DEFAULT_RPC_ENDPOINTS), 3)
        self.assertEqual(len({url for _, url in DEFAULT_RPC_ENDPOINTS}), len(DEFAULT_RPC_ENDPOINTS))

    def test_blank_values_do_not_create_endpoints(self):
        self.assertEqual(rpc_endpoints({
            "ALCHEMY_RPC_URL": " \t ",
            "ALCHEMY_API_KEY": "\n",
            "QUICKNODE_RPC_URL": " ",
        }), DEFAULT_RPC_ENDPOINTS)

    def test_paid_endpoints_precede_public_fallbacks(self):
        result = rpc_endpoints({
            "ALCHEMY_RPC_URL": "https://eth-mainnet.g.alchemy.com/v2/example",
            "QUICKNODE_RPC_URL": "https://example.quiknode.pro/token/",
        })
        self.assertEqual([name for name, _ in result[:2]], ["alchemy", "quicknode"])
        self.assertEqual(result[2:], DEFAULT_RPC_ENDPOINTS)
        self.assertTrue(result[1][1].endswith("/token/"))

    def test_full_alchemy_url_overrides_unused_key(self):
        endpoint = "https://custom.alchemy.example/rpc?apiKey=example"
        result = rpc_endpoints({"ALCHEMY_RPC_URL": endpoint, "ALCHEMY_API_KEY": "unused / key\nvalue"})
        self.assertEqual(result[0], ("alchemy", endpoint))
        self.assertEqual(result[1:], DEFAULT_RPC_ENDPOINTS)

    def test_alchemy_key_is_encoded_as_one_path_component(self):
        result = rpc_endpoints({"ALCHEMY_API_KEY": "a/b?c#d e+f%"})
        self.assertEqual(result[0], (
            "alchemy", "https://eth-mainnet.g.alchemy.com/v2/a%2Fb%3Fc%23d%20e%2Bf%25",
        ))

    def test_whitespace_around_configured_values_is_trimmed(self):
        self.assertEqual(rpc_endpoints({"ALCHEMY_API_KEY": " example \n"})[0][1],
                         "https://eth-mainnet.g.alchemy.com/v2/example")
        self.assertEqual(rpc_endpoints({"QUICKNODE_RPC_URL": " https://example.test/token/ \t"})[0],
                         ("quicknode", "https://example.test/token/"))

    def test_https_query_tokens_and_explicit_port_are_allowed(self):
        endpoint = "https://example.test:443/rpc?token=example&network=eth"
        self.assertEqual(rpc_endpoints({"QUICKNODE_RPC_URL": endpoint})[0][1], endpoint)

    def test_invalid_urls_fail_without_exposing_credentials(self):
        urls = [
            "http://example.test/secret-token",
            "https:///secret-token",
            "https://secret-token@example.test/",
            "https://user:secret-token@example.test/",
            "https://example.test/secret-token#fragment",
            "https://example.test/secret-token#",
            "https://example.test:bad/secret-token",
            "https://example.test:70000/secret-token",
            "https://[broken/secret-token",
            "https://example.test/se\ncret-token",
            "https://example.test/secret-token\\other",
        ]
        for name in ("ALCHEMY_RPC_URL", "QUICKNODE_RPC_URL"):
            for url in urls:
                with self.subTest(name=name, url=url):
                    with self.assertRaises(ValueError) as caught:
                        rpc_endpoints({name: url})
                    self.assertIn(name, str(caught.exception))
                    self.assertNotIn("secret-token", str(caught.exception))
                    self.assertNotIn("example.test", str(caught.exception))

    def test_invalid_explicit_alchemy_url_does_not_fall_back_to_key(self):
        with self.assertRaises(ValueError):
            rpc_endpoints({"ALCHEMY_RPC_URL": "http://example.test/", "ALCHEMY_API_KEY": "valid-key"})

    def test_none_uses_process_configuration_without_logging_values(self):
        with patch("eth_wallet_check.config.os.environ", {"ALCHEMY_API_KEY": "example"}):
            self.assertEqual(rpc_endpoints()[0], (
                "alchemy", "https://eth-mainnet.g.alchemy.com/v2/example",
            ))


if __name__ == "__main__":
    unittest.main()
