"""AnChain labels preserve source provenance and never turn failures into clear."""

import unittest
from unittest.mock import Mock

from eth_wallet_check.http import SourceError
from wallet_insights.anchain import AnChainLabels


ADDRESS = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
SECRET = "test-private-key"


def response(*, key=ADDRESS, category=None, osint=None):
    return {"status": 200, "err_msg": "", "data": {key: {
        "is_address_valid": True,
        "self": {"category": ["wallet"] if category is None else category,
                 "detail": [], "information": []},
        "osint": [] if osint is None else osint,
    }}}


class AnChainLabelsTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.http.request.return_value = response()
        self.adapter = AnChainLabels(self.http, api_key=SECRET, clock=lambda: 1700000000)

    def test_eth_label_request_uses_auth_header_and_no_key_in_url(self):
        result = self.adapter.inspect(ADDRESS)
        args, kwargs = self.http.request.call_args
        self.assertEqual(args[0], "https://api.anchainai.com/api/intel/address/label?proto=eth&address=" + ADDRESS)
        self.assertEqual(kwargs, {"headers": {"x-api-key": SECRET}})
        self.assertNotIn(SECRET, args[0])
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["labels"], [{"kind": "attribution", "category": "wallet", "source": "anchain"}])
        self.assertNotIn(SECRET, str(result))
        self.assertNotIn("risk_score", result)

    def test_decimal_and_mixed_case_address_keys_are_supported(self):
        for key in (str(int(ADDRESS, 16)), ADDRESS.upper().replace("0X", "0x")):
            self.http.request.return_value = response(key=key)
            self.assertEqual(self.adapter.inspect(ADDRESS)["status"], "available")

    def test_unrelated_single_record_is_not_used(self):
        self.http.request.return_value = response(key="0x" + "1" * 40)
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["labels"])

    def test_ambiguous_duplicate_representation_is_rejected(self):
        payload = response()
        payload["data"][str(int(ADDRESS, 16))] = payload["data"][ADDRESS]
        self.http.request.return_value = payload
        self.assertEqual(self.adapter.inspect(ADDRESS)["status"], "unavailable")

    def test_unaffiliated_attribution_does_not_become_low_risk(self):
        self.http.request.return_value = response(category=["unaffiliated"])
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["labels"][0]["category"], "unaffiliated")
        self.assertNotIn("safe", result)
        self.assertNotIn("risk_level", result)

    def test_osint_provenance_and_detail_are_preserved(self):
        self.http.request.return_value = response(osint=[{
            "category": "darknet", "detail": "source-specific report", "source": "source-name", "link": "https://example.com/report"}])
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["labels"][1], {
            "kind": "osint", "category": "darknet", "detail": "source-specific report",
            "source": "source-name", "link": "https://example.com/report"})

    def test_empty_successful_labels_are_distinct_from_provider_failure(self):
        self.http.request.return_value = response(category=[])
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["labels"], [])
        self.http.request.side_effect = SourceError("HTTP 429")
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error"], "http_429")
        self.assertIsNone(result["labels"])

    def test_missing_credentials_make_no_request(self):
        result = AnChainLabels(self.http).inspect(ADDRESS)
        self.assertEqual(result["status"], "not_configured")
        self.assertIsNone(result["labels"])
        self.http.request.assert_not_called()

    def test_invalid_input_makes_no_request(self):
        result = self.adapter.inspect("invalid")
        self.assertEqual(result["error"], "invalid_address")
        self.http.request.assert_not_called()

    def test_provider_status_rejections_and_malformed_data_stay_unavailable(self):
        cases = [None, {"status": 401, "data": {}}, {"status": 200, "err_msg": "denied", "data": {}},
                 {"status": 200, "data": []}, response(category=[False]),
                 {"status": 200, "data": {ADDRESS: {"is_address_valid": True, "self": {}}}}]
        bad_valid = response(); bad_valid["data"][ADDRESS]["is_address_valid"] = False
        bad_osint = response(); bad_osint["data"][ADDRESS]["osint"] = "not a list"
        cases.extend([bad_valid, bad_osint])
        for payload in cases:
            with self.subTest(payload=payload):
                self.http.request.return_value = payload
                self.assertEqual(self.adapter.inspect(ADDRESS)["status"], "unavailable")

    def test_exception_text_and_private_key_never_enter_result(self):
        self.http.request.side_effect = RuntimeError("https://example/" + SECRET)
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn(SECRET, str(result))

    def test_official_origin_only_accepts_secure_known_host(self):
        for url in ("http://api.anchainai.com", "https://api.anchainai.com.evil.example",
                    "https://evil.example", "https://user:password@api.anchainai.com",
                    "https://api.anchainai.com:444", "https://api.anchainai.com/path",
                    "https://api.anchainai.com?key=SECRET", "https://api.anchainai.com#SECRET"):
            with self.subTest(url=url), self.assertRaises(ValueError) as error:
                AnChainLabels(self.http, base_url=url, api_key=SECRET)
            self.assertNotIn("SECRET", str(error.exception))

    def test_provider_verdict_time_is_passed_without_inventing_freshness(self):
        payload = response(); payload["verdict_time"] = "2026-11-01T00:00:00Z"
        self.http.request.return_value = payload
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["provider_verdict_time"], payload["verdict_time"])
        self.assertEqual(result["checked_at"], "2023-11-14T22:13:20Z")

    def test_nonfinite_provider_time_is_not_put_into_json_response(self):
        payload = response(); payload["verdict_time"] = float("nan")
        self.http.request.return_value = payload
        result = self.adapter.inspect(ADDRESS)
        self.assertEqual(result["status"], "available")
        self.assertIsNone(result["provider_verdict_time"])


if __name__ == "__main__":
    unittest.main()
