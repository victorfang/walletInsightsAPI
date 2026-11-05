import json
import threading
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from eth_wallet_check.server import WalletHTTPServer


ADDRESS = "0x" + "1" * 40
QUERY = "/api/analytics/address/check?proto=eth&address=" + ADDRESS


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = Mock()
        cls.server = WalletHTTPServer(("127.0.0.1", 0), cls.checker)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.checker.reset_mock()
        self.checker.check.return_value = {"address": ADDRESS, "valid_format": True,
                                          "status": "seen", "has_appeared": True, "is_new_wallet": False}

    def request(self, path, method="GET", headers=None):
        try:
            response = urlopen(Request(self.base + path, method=method, headers=headers or {}), timeout=2)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, json.load(response)

    def test_health_has_no_upstream_calls(self):
        status, _, data = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.checker.check.assert_not_called()

    def test_details_reaches_service_and_intermediary_cache_is_disabled(self):
        status, headers, data = self.request(QUERY + "&details=true")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(data["status"], 200)
        self.assertEqual(data["err_msg"], "")
        self.assertTrue(data["data"][str(int(ADDRESS, 16))]["is_address_valid"])
        self.checker.check.assert_called_once_with(ADDRESS, details=True)

    def test_unknown_uses_503(self):
        self.checker.check.return_value.update(status="unknown", has_appeared=None, is_new_wallet=None)
        code, _, data = self.request(QUERY)
        self.assertEqual(code, 503)
        self.assertEqual(data["status"], 503)
        self.assertIsNone(data["data"][str(int(ADDRESS, 16))]["stats"]["new_address"])

    def test_invalid_address_uses_400(self):
        self.checker.check.return_value.update(address="bad", status="invalid_address", valid_format=False)
        self.assertEqual(self.request("/api/analytics/address/check?proto=eth&address=bad")[0], 400)

    def test_bad_queries_rejected_before_service(self):
        for query in ["details=yes", "details=", "details=true&details=false", "other=true", "proto=eth", "address=" + ADDRESS]:
            with self.subTest(query=query):
                self.assertEqual(self.request(QUERY + "&" + query)[0], 400)
        self.checker.check.assert_not_called()

    def test_other_routes_are_404(self):
        code, _, body = self.request("/other")
        self.assertEqual(code, 404)
        self.assertEqual(body, {"status": 404, "err_msg": "Not found", "data": {}})

    def test_required_params_and_supported_protocol(self):
        for query in ["", "proto=eth", "address=" + ADDRESS, "proto=eth%20mainnet&address=" + ADDRESS, "proto=eth&address="]:
            with self.subTest(query=query):
                self.assertEqual(self.request("/api/analytics/address/check?" + query)[0], 400)
        self.checker.check.assert_not_called()

    def test_non_eth_protocols_return_exact_coming_soon_error_without_eth_work(self):
        for proto in ["btc", "sol", "trx", "tron", "bitcoin", "solana", "BTC", "doge", "future_chain"]:
            with self.subTest(proto=proto):
                code, headers, body = self.request("/api/analytics/address/check?proto=" + proto + "&address=NativeCaseSensitiveAddress&details=true")
                self.assertEqual(code, 501)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(body, {"status": 501, "err_msg": "coming soon contact info@anchain.ai", "data": {}})
        self.checker.check.assert_not_called()

    def test_optional_local_api_key_is_checked_before_provider_work(self):
        self.server.api_key = "local-test-key"
        try:
            for headers in [{}, {"x-api-key": "wrong"}]:
                code, _, body = self.request(QUERY, headers=headers)
                self.assertEqual(code, 401)
                self.assertEqual(body["status"], 401)
                self.assertEqual(body["data"], {})
            self.checker.check.assert_not_called()
            self.assertEqual(self.request(QUERY, headers={"X-API-KEY": "local-test-key"})[0], 200)
        finally:
            self.server.api_key = None

    def test_post_returns_json_method_error(self):
        code, headers, body = self.request(QUERY, method="POST")
        self.assertEqual(code, 405)
        self.assertEqual(headers["Allow"], "GET")
        self.assertEqual(body["status"], 405)
        self.checker.check.assert_not_called()

    def test_legacy_path_uses_same_response_envelope(self):
        code, _, body = self.request("/v1/ethereum/addresses/" + ADDRESS)
        self.assertEqual(code, 200)
        self.assertIn(str(int(ADDRESS, 16)), body["data"])

    def test_openapi_is_served_without_provider_requests(self):
        code, _, body = self.request("/openapi.json")
        self.assertEqual(code, 200)
        self.assertIn("/walletAgeDays", body["paths"])
        self.checker.check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
