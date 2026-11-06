from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import Mock, patch

from wallet_age_days.cli import main
from wallet_age_days.service import WalletAgeError

ADDRESS = '0x' + 'a' * 40


class CLITests(unittest.TestCase):
    def test_success_is_only_integer_and_service_is_closed(self):
        output, service = io.StringIO(), Mock()
        service.walletAgeDays.return_value = 123
        with patch('wallet_age_days.cli.build_service', return_value=service), redirect_stdout(output):
            self.assertEqual(main(['ETH', ADDRESS]), 0)
        self.assertIs(type(json.loads(output.getvalue())), int)
        self.assertEqual(json.loads(output.getvalue()), 123)
        service.walletAgeDays.assert_called_once_with('ETH', ADDRESS)
        service.close.assert_called_once()

    def test_invalid_precedes_cache_configuration_and_network(self):
        output = io.StringIO()
        with patch('wallet_age_days.cli.build_service') as build, redirect_stdout(output):
            self.assertEqual(main(['ETH', 'invalid']), 2)
        build.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), 'invalid')

    def test_unsupported_chain_is_not_sent_to_ethereum(self):
        output = io.StringIO()
        with patch('wallet_age_days.cli.build_service') as build, redirect_stdout(output):
            self.assertEqual(main(['btc', 'bitcoin-address']), 5)
        build.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), 'coming soon contact info@anchain.ai')

    def test_unknown_and_not_found_have_distinct_errors(self):
        for status, message, code in [(404, 'not_found', 4), (503, 'unknown', 3)]:
            output, service = io.StringIO(), Mock()
            service.walletAgeDays.side_effect = WalletAgeError(status, message)
            with patch('wallet_age_days.cli.build_service', return_value=service), redirect_stdout(output):
                self.assertEqual(main(['evm', ADDRESS]), code)
            self.assertEqual(json.loads(output.getvalue()), message)
            service.close.assert_called_once()

    def test_unexpected_errors_never_print_credentials(self):
        output = io.StringIO()
        with patch('wallet_age_days.cli.build_service', side_effect=RuntimeError('SECRET')), redirect_stdout(output):
            self.assertEqual(main(['ETH', ADDRESS]), 3)
        self.assertEqual(json.loads(output.getvalue()), 'unknown')

    def test_explicit_provider_file_and_timeout_reach_the_shared_factory(self):
        output, service = io.StringIO(), Mock()
        service.walletAgeDays.return_value = 123
        with patch('wallet_age_days.cli.build_service', return_value=service) as build, redirect_stdout(output):
            self.assertEqual(main(['ETH', ADDRESS, '--env-file', 'provider.env', '--timeout', '1.5']), 0)
        self.assertEqual(build.call_args.kwargs['env_file'], 'provider.env')
        self.assertEqual(build.call_args.kwargs['http'].timeout, 1.5)


if __name__ == '__main__':
    unittest.main()
