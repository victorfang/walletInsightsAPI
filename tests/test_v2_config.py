"""Provider configuration compatibility using dummy values and isolated files."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v2.app import ROOT, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings" / "config.toml"
        self.path.parent.mkdir()
        self.source = (ROOT / "config.toml").read_text()
        self.path.write_text(self.source)
        self.env = Path(self.temp.name) / "providers.env"

    def load(self, environ=None, contents=None):
        if contents is not None:
            self.env.write_text(contents)
        return load_config(self.path, self.env if contents is not None else None,
                           environ={} if environ is None else environ)

    def test_missing_paid_credentials_preserves_public_feeds(self):
        config = self.load()
        for name in ("alchemy_eth", "etherscan_eth", "quicknode_eth"):
            self.assertEqual(config["feeds"][name]["url"], "")
        for name in ("routescan_eth", "blockscout_eth", "blockpi_eth", "publicnode_eth"):
            self.assertTrue(config["feeds"][name]["url"].startswith("https://"))
        self.assertEqual(config["wallet_age"]["max_entries"], 1_000_000)

    def test_alchemy_explicit_url_wins_with_or_without_key(self):
        explicit = "https://alchemy.example.invalid/explicit-path"
        for values in ({"ALCHEMY_RPC_URL": explicit},
                       {"ALCHEMY_RPC_URL": explicit, "ALCHEMY_API_KEY": "dummy-key"}):
            with self.subTest(values=values):
                self.assertEqual(self.load(values)["feeds"]["alchemy_eth"]["url"], explicit)

    def test_alchemy_key_template_quotes_reserved_characters(self):
        config = self.load({"ALCHEMY_API_KEY": "dummy/key+value?"})
        self.assertEqual(config["feeds"]["alchemy_eth"]["url"],
                         "https://eth-mainnet.g.alchemy.com/v2/dummy%2Fkey%2Bvalue%3F")

    def test_placeholder_urls_do_not_mask_real_alchemy_key(self):
        for placeholder in ("YOUR_ALCHEMY_KEY", "your-key", "replace_me", "replace-me", "<key>"):
            with self.subTest(placeholder=placeholder):
                config = self.load({"ALCHEMY_API_KEY": "dummy-valid-key",
                    "ALCHEMY_RPC_URL": "https://alchemy.example.invalid/" + placeholder})
                self.assertTrue(config["feeds"]["alchemy_eth"]["url"].endswith("/dummy-valid-key"))

    def test_quicknode_current_and_legacy_url_names_with_auth(self):
        for name in ("QUICKNODE_ETH_URL", "QUICKNODE_RPC_URL"):
            with self.subTest(name=name):
                config = self.load({name: "https://quicknode.example.invalid/test/",
                                   "QUICKNODE_ETH_AUTH_TOKEN": "dummy-token"})
                feed = config["feeds"]["quicknode_eth"]
                self.assertEqual(feed["url"], "https://quicknode.example.invalid/test/")
                self.assertEqual(feed["headers"], {"x-token": "dummy-token"})
                for other in ("alchemy_eth", "blockpi_eth", "publicnode_eth"):
                    self.assertNotIn("x-token", config["feeds"][other].get("headers", {}))

    def test_quicknode_shell_alias_overrides_other_file_alias(self):
        for shell_name, file_name in (("QUICKNODE_RPC_URL", "QUICKNODE_ETH_URL"),
                                     ("QUICKNODE_ETH_URL", "QUICKNODE_RPC_URL")):
            with self.subTest(shell_name=shell_name):
                config = self.load({shell_name: "https://shell.example.invalid/rpc"},
                                   f"{file_name}=https://file.example.invalid/rpc\n")
                self.assertEqual(config["feeds"]["quicknode_eth"]["url"],
                                 "https://shell.example.invalid/rpc")

    def test_etherscan_retains_chain_id_and_adds_api_key(self):
        config = self.load({"ETHERSCAN_API_KEY": "dummy-scan-key"})
        feed = config["feeds"]["etherscan_eth"]
        self.assertEqual(feed["params"], {"chainid": "1", "apikey": "dummy-scan-key"})
        self.assertEqual(feed["url"], "https://api.etherscan.io/v2/api")

    def test_process_environment_overrides_file_without_mutation(self):
        self.env.write_text("export ALCHEMY_API_KEY='file-key'\n"
                            "ETHERSCAN_API_KEY=only-in-file\n"
                            "WALLET_CHECK_API_KEY=file-client-key\n")
        with patch.dict(os.environ, {"ALCHEMY_API_KEY": "shell-key",
                                    "WALLET_CHECK_API_KEY": "shell-client-key"}, clear=True):
            before = dict(os.environ)
            config = load_config(self.path, self.env)
            self.assertEqual(dict(os.environ), before)
        self.assertTrue(config["feeds"]["alchemy_eth"]["url"].endswith("/shell-key"))
        self.assertEqual(config["feeds"]["etherscan_eth"]["params"]["apikey"], "only-in-file")
        self.assertEqual(config["server"]["api_key"], "shell-client-key")

    def test_database_path_resolves_against_config_without_creating_database(self):
        config = self.load()
        expected = self.path.parent / "data" / "wallet-age.sqlite3"
        self.assertEqual(config["wallet_age"]["database"], str(expected.resolve()))
        self.assertFalse(expected.exists())

    def test_invalid_urls_raise_sanitized_errors(self):
        for url in ("http://private.example.invalid/dummy-SECRET",
                    "https://dummy-SECRET:password@example.invalid/",
                    "https://dummy-SECRET＠example.invalid/path",
                    "https://[dummy-SECRET"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    self.load({"ALCHEMY_RPC_URL": url})
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertNotIn(url, str(caught.exception))

    def test_env_values_are_data_and_never_executed(self):
        marker = Path(self.temp.name) / "must-not-exist"
        expression = f"$(touch {marker})"
        config = self.load(contents=f"ALCHEMY_API_KEY='{expression}'\n"
                           f"UNRELATED=__import__('pathlib').Path('{marker}').touch()\n")
        self.assertEqual(config["_env"]["ALCHEMY_API_KEY"], expression)
        self.assertFalse(marker.exists())

    def test_explicitly_disabled_feed_stays_disabled_with_credentials(self):
        self.path.write_text(self.source.replace("[feeds.alchemy_eth]\n",
                                                "[feeds.alchemy_eth]\nenabled = false\n"))
        self.assertEqual(self.load({"ALCHEMY_API_KEY": "dummy-key"})["feeds"]["alchemy_eth"]["url"], "")

    def test_unknown_adapter_and_missing_feed_references_fail_at_startup(self):
        for source in (self.source.replace('adapter = "alchemy"', 'adapter = "unknown"'),
                       self.source.replace('history = ["alchemy_eth",', 'history = ["missing",')):
            with self.subTest(source=source[:30]):
                self.path.write_text(source)
                with self.assertRaises(ValueError):
                    self.load()
