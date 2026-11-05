import io
from http.client import BadStatusLine, IncompleteRead
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from examples.withdrawal_age_gate import evaluate, screen_destination


ADDRESS = "0x" + "a" * 40


class Response:
    def __init__(self, status=200, body=0, coverage="ethereum", finalized="true"):
        self.status = status
        self.headers = {"X-Wallet-Age-Coverage": coverage,
                        "X-Wallet-Age-Finalized": finalized}
        self.data = json.dumps(body).encode()

    def read(self, limit):
        return self.data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class WithdrawalAgeGateTests(unittest.TestCase):
    def check(self, status=200, body=0, **kwargs):
        values = {"coverage": "ethereum", "finalized": "true", **kwargs}
        return evaluate(status, body, **values)

    def test_zero_is_valid_recent_age_not_missing_data(self):
        result = self.check(body=0)
        self.assertEqual((result.action, result.reason, result.age_days),
                         ("step_up", "recent_observed_activity", 0))

    def test_threshold_and_older_ages_never_approve_withdrawal(self):
        self.assertEqual(self.check(body=6).action, "step_up")
        for age in (7, 1000):
            self.assertEqual(self.check(body=age).action, "continue_other_checks")

    def test_old_destination_new_to_customer_requires_verification(self):
        result = self.check(body=1000, new_beneficiary=True)
        self.assertEqual((result.action, result.reason),
                         ("step_up", "new_beneficiary_for_customer"))

    def test_account_risk_is_not_cleared_by_age(self):
        for age in (0, 1000):
            self.assertEqual(self.check(body=age, account_risk=True).action, "pending_review")

    def test_unobserved_and_unknown_have_distinct_actions(self):
        self.assertEqual(self.check(404, "not_found").reason, "no_observed_history")
        self.assertEqual(self.check(404, "not_found").action, "step_up")
        for status, body in [(503, "unknown"), (401, "unauthorized"), (500, "error"),
                             (501, "coming soon contact info@anchain.ai")]:
            self.assertEqual(self.check(status, body).action, "pending_review")

    def test_only_invalid_address_is_rejected_as_destination_format(self):
        self.assertEqual(self.check(400, "invalid").action, "reject")
        self.assertEqual(self.check(400, "invalid request").action, "pending_review")

    def test_invalid_integer_bodies_do_not_pass(self):
        for body in (True, False, -1, "0", None, {}, 0.0):
            self.assertEqual(self.check(body=body).action, "pending_review")

    def test_unverified_or_wrong_chain_data_does_not_pass(self):
        for kwargs in ({"finalized": "false"}, {"coverage": ""},
                       {"coverage": "polygon"}, {"coverage": "ethereum,polygon"},
                       {"blockchain": "Polygon"}):
            self.assertEqual(self.check(body=1000, **kwargs).action, "pending_review")

    def test_client_preserves_zero_and_uses_bounded_socket_timeout(self):
        with patch("examples.withdrawal_age_gate.urlopen", return_value=Response()) as opening:
            result = screen_destination("ETH", ADDRESS)
        self.assertEqual((result.action, result.age_days), ("step_up", 0))
        self.assertEqual(opening.call_args.kwargs["timeout"], 3.0)

    def test_client_handles_http_unknown_without_converting_it_to_zero(self):
        error = HTTPError("https://api.example/walletAgeDays", 503, "Unavailable",
                          {"X-Wallet-Age-Coverage": "ethereum"}, io.BytesIO(b'"unknown"'))
        with patch("examples.withdrawal_age_gate.urlopen", side_effect=error):
            result = screen_destination("ETH", ADDRESS)
        self.assertEqual(result.action, "pending_review")
        self.assertIsNone(result.age_days)

    def test_timeout_and_invalid_json_leave_withdrawal_pending(self):
        with patch("examples.withdrawal_age_gate.urlopen", side_effect=TimeoutError):
            self.assertEqual(screen_destination("ETH", ADDRESS).action, "pending_review")
        response = Response()
        response.data = b"not json"
        with patch("examples.withdrawal_age_gate.urlopen", return_value=response):
            self.assertEqual(screen_destination("ETH", ADDRESS).action, "pending_review")

    def test_polygon_never_uses_current_eth_only_result(self):
        with patch("examples.withdrawal_age_gate.urlopen") as opening:
            self.assertEqual(screen_destination("Polygon", ADDRESS).action, "pending_review")
        opening.assert_not_called()

    def test_protocol_errors_and_truncated_bodies_leave_withdrawal_pending(self):
        for error in (BadStatusLine("bad status"), IncompleteRead(b"partial", 10)):
            with self.subTest(error=type(error).__name__), patch(
                    "examples.withdrawal_age_gate.urlopen", side_effect=error):
                result = screen_destination("ETH", ADDRESS)
                self.assertEqual(result.action, "pending_review")
                self.assertIsNone(result.age_days)


if __name__ == "__main__":
    unittest.main()
