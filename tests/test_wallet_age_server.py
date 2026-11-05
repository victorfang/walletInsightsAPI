from contextlib import contextmanager
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock

from wallet_age_days.server import WalletAgeHTTPServer
from wallet_age_days.service import AgeResult, WalletAgeError

ADDRESS = '0x' + 'a' * 40


class WalletAgeHTTPTests(unittest.TestCase):
    @contextmanager
    def server(self, api_key=None):
        checker = Mock(coverage='ethereum')
        checker.check.return_value = AgeResult(365, 1600000000, 'hit', True)
        with WalletAgeHTTPServer(('127.0.0.1', 0), checker, api_key) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f'http://127.0.0.1:{server.server_port}', checker
            finally:
                server.shutdown()
                thread.join()

    def request(self, base, path, *, payload=None, method=None, headers=None):
        req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      method=method, headers=headers or {})
        try:
            response = urlopen(req, timeout=4)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response), response.headers

    def test_get_and_post_return_integer_and_coverage(self):
        with self.server() as (base, checker):
            for kwargs in ({}, {'payload': {'blockchain': 'Polygon', 'address': ADDRESS},
                               'headers': {'Content-Type': 'application/json'}}):
                path = '/walletAgeDays' if kwargs else f'/walletAgeDays?blockchain=ETH&address={ADDRESS}'
                status, body, headers = self.request(base, path, **kwargs)
                self.assertEqual((status, type(body), body), (200, int, 365))
                self.assertEqual(headers['X-Wallet-Age-Coverage'], 'ethereum')
                self.assertEqual(headers['X-Wallet-Age-Cache'], 'hit')
                self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(checker.check.call_count, 2)

    def test_missing_duplicate_unknown_inputs_are_invalid(self):
        paths = ['/walletAgeDays', '/walletAgeDays?blockchain=ETH',
                 f'/walletAgeDays?blockchain=ETH&address={ADDRESS}&details=true',
                 '/walletAgeDays?blockchain=ETH&blockchain=ETH']
        with self.server() as (base, checker):
            for path in paths:
                self.assertEqual(self.request(base, path)[:2], (400, 'invalid request'))
            checker.check.assert_not_called()

    def test_old_routes_are_retired(self):
        with self.server() as (base, checker):
            status, body, _ = self.request(base, '/api/analytics/address/check?proto=eth&address=' + ADDRESS)
            self.assertEqual((status, body), (404, 'not_found'))
            checker.check.assert_not_called()

    def test_error_statuses_and_authentication(self):
        with self.server('secret') as (base, checker):
            path = f'/walletAgeDays?blockchain=ETH&address={ADDRESS}'
            self.assertEqual(self.request(base, path)[:2], (401, 'unauthorized'))
            checker.check.assert_not_called()
            for status, message in [(400, 'invalid'), (404, 'not_found'), (503, 'unknown')]:
                checker.check.side_effect = WalletAgeError(status, message)
                self.assertEqual(self.request(base, path, headers={'x-api-key': 'secret'})[:2], (status, message))

    def test_bad_json_and_non_json_are_rejected(self):
        with self.server() as (base, checker):
            self.assertEqual(self.request(base, '/walletAgeDays', payload={},
                headers={'Content-Type': 'application/json'})[:2], (400, 'invalid request'))
            self.assertEqual(self.request(base, '/walletAgeDays', payload={'blockchain': 'eth', 'address': ADDRESS},
                headers={'Content-Type': 'text/plain'})[:2], (400, 'invalid request'))
            req = Request(base + '/walletAgeDays', data=b'{"blockchain":"eth","blockchain":"evm","address":"x"}',
                          headers={'Content-Type': 'application/json'})
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=4)
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()
            checker.check.assert_not_called()


if __name__ == '__main__':
    unittest.main()
