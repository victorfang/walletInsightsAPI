from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from eth_wallet_check.http import JSONHTTP
from wallet_age_days.config import settings
from wallet_age_days.factory import build_service
from wallet_insights.config import settings as insights_settings


class WalletAgeConfigTests(unittest.TestCase):
    def config_file(self, contents):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        file = Path(directory.name) / "provider.env"
        file.write_text(contents)
        return file

    def test_explicit_file_loads_both_providers_etherscan_and_quicknode_auth(self):
        file = self.config_file("ALCHEMY_API_KEY=TEST_KEY\nETHERSCAN_API_KEY=TEST_SCAN\n"
            "QUICKNODE_ETH_URL=https://test.quiknode.pro/test/\nQUICKNODE_ETH_AUTH_TOKEN=TEST_TOKEN\n"
            "UNRELATED_SECRET=DO_NOT_LOAD\nWALLET_AGE_QUICKNODE_HISTORY=1\n")
        result = settings(file, {})
        self.assertEqual(result["etherscan_key"], "TEST_SCAN")
        self.assertEqual([name for name, _ in result["endpoints"]][:2], ["alchemy", "quicknode"])
        self.assertEqual(result["headers_by_url"][result["endpoints"][1][1]], {"x-token": "TEST_TOKEN"})
        self.assertTrue(result["quicknode_history_enabled"])
        self.assertNotIn("UNRELATED_SECRET", result)

    def test_placeholder_urls_do_not_override_real_keys_and_eth_url(self):
        file = self.config_file("ALCHEMY_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_ALCHEMY_KEY\n"
            "QUICKNODE_RPC_URL=https://YOUR_ENDPOINT.quiknode.pro/YOUR_TOKEN/\nALCHEMY_API_KEY=TEST_KEY\n"
            "QUICKNODE_ETH_URL=https://test.quiknode.pro/test/\nQUICKNODE_ETH_AUTH_TOKEN=TEST_TOKEN\n")
        for reader in (settings, insights_settings):
            result = reader(file, {})
            self.assertTrue(result["endpoints"][0][1].endswith("TEST_KEY"))
            self.assertEqual(result["endpoints"][1][1], "https://test.quiknode.pro/test/")

    def test_real_explicit_urls_and_shell_values_keep_precedence(self):
        file = self.config_file("ALCHEMY_API_KEY=FILE_KEY\nETHERSCAN_API_KEY=FILE_SCAN\n"
            "QUICKNODE_ETH_URL=https://test.quiknode.pro/file/\n")
        result = settings(file, {"ALCHEMY_RPC_URL":"https://alchemy.example/explicit",
            "QUICKNODE_RPC_URL":"https://quicknode.example/explicit", "ETHERSCAN_API_KEY":"ENV_SCAN"})
        self.assertEqual(result["endpoints"][:2], (("alchemy","https://alchemy.example/explicit"),
            ("quicknode","https://quicknode.example/explicit")))
        self.assertEqual(result["etherscan_key"], "ENV_SCAN")

    def test_factory_installs_endpoint_headers_and_configured_etherscan(self):
        http = JSONHTTP()
        result = build_service(http=http, store=Mock(), environ={
            "ALCHEMY_API_KEY":"TEST_KEY", "ETHERSCAN_API_KEY":"TEST_SCAN",
            "QUICKNODE_ETH_URL":"https://test.quiknode.pro/test/", "QUICKNODE_ETH_AUTH_TOKEN":"TEST_TOKEN"})
        self.assertEqual(result.sources.etherscan_key, "TEST_SCAN")
        self.assertEqual(http._headers_by_url["https://test.quiknode.pro/test/"], {"x-token":"TEST_TOKEN"})
        self.assertIn("etherscan_normal", [name for name, _ in result.sources.jobs("0x"+"1"*40)])

    def test_main_passes_explicit_config_file_to_both_services(self):
        from wallet_age_days.__main__ import main
        with patch("sys.argv", ["wallet_age_days", "--env-file", "test.env"]), \
             patch("wallet_age_days.__main__.build_service") as age, \
             patch("wallet_age_days.__main__.build_insights") as insights, \
             patch("wallet_age_days.__main__.WalletAgeHTTPServer") as server, \
             patch("builtins.print"):
            server.return_value.__enter__.return_value.serve_forever.side_effect = KeyboardInterrupt
            main()
        self.assertEqual(age.call_args.kwargs["env_file"], "test.env")
        self.assertEqual(insights.call_args.kwargs["env_file"], "test.env")


if __name__ == "__main__":
    unittest.main()
