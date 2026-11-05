import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock

from wallet_age_days.server import WalletAgeHTTPServer
from wallet_age_days.service import AgeResult


ADDRESS = "0x" + "a" * 40


class WalletInsightsHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.age = Mock(coverage="ethereum")
        cls.age.check.return_value = AgeResult(0, 1700000000, "hit", True)
        cls.insights = Mock()
        cls.body = {"status": "partial", "address": ADDRESS, "chainId": 1,
                    "riskLabels": {"status": "unavailable", "labels": None},
                    "accountNonce": "0", "cache": {"snapshot": {"hit": True}, "riskLabels": {"hit": False}}}
        cls.insights.check.return_value = (200, cls.body)
        cls.server = WalletAgeHTTPServer(("127.0.0.1", 0), cls.age, insights=cls.insights)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def setUp(self):
        self.age.reset_mock()
        self.insights.reset_mock()
        self.insights.check.return_value = (200, self.body)

    def call(self, path, payload=None):
        request = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json"} if payload is not None else {})
        try:
            response = urlopen(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response), response.headers

    def test_insights_get_and_post_preserve_explicit_partial_risk(self):
        for path, body in [(f"/WalletInsights?blockchain=ETH&address={ADDRESS}", None),
                           ("/WalletInsights", {"blockchain": "ETH", "address": ADDRESS})]:
            status, result, headers = self.call(path, body)
            self.assertEqual(status, 200)
            self.assertIsNone(result["riskLabels"]["labels"])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(headers["X-Wallet-Insights-Cache"], "mixed")
            self.assertEqual(headers["X-Wallet-Insights-Coverage"], "ethereum")
            self.assertIsNone(headers.get("X-Wallet-Age-Quality"))
        self.assertEqual(self.insights.check.call_count, 2)
        self.age.check.assert_not_called()

    def test_age_remains_bare_integer_with_separate_checker(self):
        status, result, headers = self.call(f"/walletAgeDays?blockchain=ETH&address={ADDRESS}")
        self.assertEqual((status, result, type(result)), (200, 0, int))
        self.assertEqual(headers["X-Wallet-Age-Cache"], "hit")
        self.assertIsNone(headers.get("X-Wallet-Insights-Cache"))
        self.insights.check.assert_not_called()

    def test_bad_inputs_do_not_call_either_service(self):
        for path in ["/WalletInsights", "/WalletInsights?blockchain=ETH",
                     f"/WalletInsights?blockchain=ETH&address={ADDRESS}&details=true"]:
            self.assertEqual(self.call(path)[:2], (400, "invalid request"))
        self.age.check.assert_not_called()
        self.insights.check.assert_not_called()

    def test_unavailable_insights_status_is_preserved(self):
        self.insights.check.return_value = (503, {"status": "unavailable", "riskLabels": {"labels": None}})
        status, body, _ = self.call(f"/WalletInsights?blockchain=ETH&address={ADDRESS}")
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
