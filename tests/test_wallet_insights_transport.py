import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from eth_wallet_check.http import JSONHTTP, SourceError, _NoRedirect


class WalletInsightsTransportTests(unittest.TestCase):
    def test_authenticated_requests_disable_redirects_and_suppress_error_urls(self):
        http = JSONHTTP()
        opener = Mock()
        opener.open.side_effect = HTTPError('https://host/PRIVATE', 302, 'redirect', {}, None)
        with patch('eth_wallet_check.http.build_opener', return_value=opener) as building:
            with self.assertRaises(SourceError) as result:
                http.request('https://api.anchainai.com/api/intel/address/label', headers={'x-api-key': 'PRIVATE'})
        self.assertEqual(str(result.exception), 'HTTP 302')
        self.assertIsInstance(building.call_args.args[0], _NoRedirect)
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, None, None, 'https://other.example'))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('X-api-key'), 'PRIVATE')

    def test_quicknode_token_is_bound_to_its_exact_rpc_endpoint(self):
        endpoint = 'https://test.quiknode.pro/private/'
        http = JSONHTTP(headers_by_url={endpoint: {'x-token': 'PRIVATE'}})
        with patch.object(http, 'request', return_value={}) as sending:
            http(endpoint, {})
            http('https://ethereum-rpc.publicnode.com', {})
        self.assertEqual(sending.call_args_list[0].kwargs['headers'], {'x-token': 'PRIVATE'})
        self.assertIsNone(sending.call_args_list[1].kwargs['headers'])

    def test_query_string_credentials_also_disable_redirects(self):
        http = JSONHTTP()
        opener = Mock()
        opener.open.side_effect = HTTPError('https://api.etherscan.io/v2/api?apikey=TEST_SECRET',
                                           302, 'redirect', {}, None)
        with patch('eth_wallet_check.http.build_opener', return_value=opener) as building:
            with self.assertRaises(SourceError) as result:
                http('https://api.etherscan.io/v2/api?apikey=TEST_SECRET')
        self.assertEqual(str(result.exception), 'HTTP 302')
        self.assertIsInstance(building.call_args.args[0], _NoRedirect)

    def test_cancelled_losing_job_cannot_start_another_network_call(self):
        import threading
        cancelled = threading.Event()
        cancelled.set()
        http = JSONHTTP()
        with http.deadline_at(9999999999, cancelled=cancelled), \
             patch('eth_wallet_check.http.urlopen') as opening:
            with self.assertRaisesRegex(SourceError, 'Request cancelled'):
                http('https://ethereum-rpc.publicnode.com', {})
        opening.assert_not_called()


if __name__ == '__main__':
    unittest.main()
