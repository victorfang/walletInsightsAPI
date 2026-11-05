import unittest
from unittest.mock import Mock, patch

from eth_wallet_check import factory


class FactoryTests(unittest.TestCase):
    def test_only_ethereum_has_an_executable_factory(self):
        self.assertEqual(set(factory.CHECKER_FACTORIES), {"eth"})
        self.assertIs(factory.CHECKER_FACTORIES["eth"], factory.build_ethereum_checker)

    def test_ethereum_wiring_uses_configured_endpoints_and_cost_control(self):
        http, cache = Mock(), Mock(path=":memory:")
        endpoints = (("alchemy", "https://alchemy.example/PRIVATE"),
                     ("quicknode", "https://quicknode.example/PRIVATE"))
        with patch.object(factory, "rpc_endpoints", return_value=endpoints), \
                patch.object(factory, "FastHistory") as history, \
                patch.object(factory, "PublicRPC") as rpc, \
                patch.object(factory, "AgeStore") as age, \
                patch.object(factory, "FastWalletChecker") as checker:
            result = factory.build_ethereum_checker(http, cache)
        history.assert_called_once_with(http, endpoints)
        rpc.assert_called_once_with(http, endpoints)
        checker.assert_called_once_with(history.return_value, rpc.return_value, cache, age.return_value, http)
        self.assertIs(result, checker.return_value)


if __name__ == "__main__":
    unittest.main()
