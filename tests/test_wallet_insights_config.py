import tempfile
from pathlib import Path
import unittest

from wallet_insights.config import settings


class WalletInsightsConfigTests(unittest.TestCase):
    def test_explicit_file_maps_existing_provider_names_and_shell_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "provider.env"
            file.write_text("ALCHEMY_API_KEY=FILE_KEY\nQUICKNODE_ETH_URL=https://test.quiknode.pro/test/\n"
                            "QUICKNODE_ETH_AUTH_TOKEN=TEST_TOKEN\nANCHAIN_API_KEY=TEST_API\n"
                            "UNRELATED_SECRET=DO_NOT_LOAD\n")
            result = settings(file, {"ALCHEMY_API_KEY": "ENV_KEY"})
        self.assertTrue(result["endpoints"][0][1].endswith("ENV_KEY"))
        self.assertEqual(result["headers_by_url"][result["endpoints"][1][1]], {"x-token": "TEST_TOKEN"})
        self.assertEqual(result["anchain_key"], "TEST_API")
        self.assertNotIn("UNRELATED_SECRET", result)

    def test_empty_settings_retain_three_public_rpc_sources_and_no_fake_risk(self):
        result = settings(environ={})
        self.assertEqual([name for name, _ in result["endpoints"]], ["publicnode", "1rpc", "blockpi"])
        self.assertEqual(result["anchain_key"], "")


if __name__ == "__main__":
    unittest.main()
