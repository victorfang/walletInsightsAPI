"""Exercise version dispatch and the existing wire contract without upstream I/O."""

import asyncio
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta
import io
import gzip
import json
import sqlite3
import tempfile
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer

from v2.app import DEMO_KEY, VERSIONS, create_app, load_config
from v2.service import AgeError


ADDRESS = "0x" + "a" * 40
INPUT = {"blockchain": "ETH", "address": ADDRESS}
KEY = {"x-api-key": "test-key"}


class V2HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.log = self.enterContext(redirect_stdout(io.StringIO()))
        self.service = SimpleNamespace(
            coverage="ethereum", close=AsyncMock(),
            check=AsyncMock(return_value=SimpleNamespace(
                days=0, epoch=1700000000, cache="hit", finalized=True,
                evidence="cached-canonical", positive_sources=0)),
        )
        self.legacy_age = Mock(coverage="ethereum")
        self.legacy_age.check.return_value = SimpleNamespace(
            days=365, epoch=1600000000, cache="miss", finalized=True,
            evidence="single-source", positive_sources=1,
        )
        self.insights_body = {
            "status": "partial", "address": ADDRESS, "chainId": 1,
            "riskLabels": {"status": "unavailable", "labels": None},
            "cache": {"snapshot": {"hit": True}, "riskLabels": {"hit": False}},
        }
        self.insights = Mock()
        self.insights.check.return_value = (200, self.insights_body)
        self.config = {
            "server": {"api_key": "test-key"}, "api": {"latest": "v2"},
            "_env": {"WALLET_CLIENT_KEY_ACME": "acme-test-secret", "ALCHEMY_API_KEY": "provider-test-secret",
                     "WALLET_CLIENT_KEY_BETA": "beta-test-secret",
                     "RAILWAY_ENVIRONMENT_ID": "test-environment"},
            "legacy": {"enabled": True}, "wallet_age": {}, "chains": {},
            "feeds": {"alchemy": {"key_env": "ALCHEMY_API_KEY"}},
        }
        app = await create_app(self.config, service=self.service, legacy={
            "walletAgeDays": self.legacy_age, "WalletInsights": self.insights,
        })
        self.client = TestClient(TestServer(app, handler_cancellation=True))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def call(self, path, *, method="GET", auth=True, **kwargs):
        headers = dict(KEY if auth else {})
        headers.update(kwargs.pop("headers", {}))
        async with self.client.request(method, path, headers=headers, **kwargs) as response:
            return response.status, await response.json(), response.headers.copy()

    def assert_no_checks(self):
        self.service.check.assert_not_awaited()
        self.legacy_age.check.assert_not_called()
        self.insights.check.assert_not_called()

    async def test_client_identity_ip_wallet_and_time_for_get_and_post(self):
        for path, method, key, identity in [
            ("/walletAgeDays", "GET", "acme-test-secret", "acme"),
            ("/v2/walletAgeDays", "POST", "beta-test-secret", "beta"),
            ("/v1/walletAgeDays", "POST", "test-key", "legacy"),
            ("/latest/WalletInsights", "GET", "acme-test-secret", "acme"),
        ]:
            values = {**INPUT, "address": "0x" + "A" * 40}
            headers = {"x-api-key": key, "X-Real-IP": "2001:db8::2",
                       "X-Forwarded-For": "192.0.2.44", "X-Railway-Request-Id": "req-example"}
            status, _, _ = await self.call(path, method=method, headers=headers,
                                          **({"params": values} if method == "GET" else {"json": values}))
            self.assertEqual(status, 200)
            row = json.loads(self.log.getvalue().splitlines()[-1])
            self.assertEqual((row["clientId"], row["clientIp"], row["endpoint"], row["method"],
                              row["blockchain"], row["wallet"], row["requestId"], row["status"]),
                             (identity, "2001:db8::2", path, method, "eth", ADDRESS, "req-example", 200))
            self.assertEqual(datetime.fromisoformat(row["timestamp"]).utcoffset(), timedelta(0))
            self.assertEqual(row["ipSource"], "railway")
            self.assertNotIn(key, self.log.getvalue())
        self.assertEqual(len(self.log.getvalue().splitlines()), 4)

    async def test_access_errors_do_not_echo_credentials_or_raw_inputs(self):
        secret = "acme-test-secret"
        self.service.check.side_effect = AgeError(400, "invalid")
        for path, options, expected in [
            ("/walletAgeDays", {"params": INPUT, "headers": {"x-api-key": "bad-token"}}, 401),
            ("/walletAgeDays", {"params": {**INPUT, "address": secret}}, 400),
            ("/v2/" + secret, {}, 404),
            ("/walletAgeDays", {"params": {**INPUT, "extra": secret}}, 400),
        ]:
            self.assertEqual((await self.call(path, **options))[0], expected)
        # Even a syntactically valid correlation ID containing a credential is omitted.
        await self.call("/health", headers={"X-Railway-Request-Id": "id-" + secret})
        await self.call("/health", headers={"X-Railway-Request-Id": "id-provider-test-secret"})
        rows = list(map(json.loads, self.log.getvalue().splitlines()))
        self.assertEqual(rows[0]["clientId"], "unauthenticated")
        self.assertIsNone(rows[0]["wallet"])
        self.assertIsNone(rows[1]["wallet"])
        self.assertEqual(rows[2]["endpoint"], "unknown")
        self.assertIsNone(rows[-1]["requestId"])
        for value in (secret, "bad-token", "test-key", "beta-test-secret", "provider-test-secret"):
            self.assertNotIn(value, self.log.getvalue())

    async def test_named_only_requires_key_and_no_keys_preserves_open_access(self):
        for env, expected in [({"WALLET_CLIENT_KEY_ACME": "named-only"}, 401), ({}, 200)]:
            config = {**self.config, "_env": env, "server": {}}
            app = await create_app(config, service=self.service)
            async with TestClient(TestServer(app)) as client:
                async with client.get("/walletAgeDays", params=INPUT,
                                      headers={"X-Real-IP": "203.0.113.8"}) as response:
                    self.assertEqual(response.status, expected)
                row = json.loads(self.log.getvalue().splitlines()[-1])
                self.assertEqual(row["clientIp"], "127.0.0.1")
                self.assertEqual(row["ipSource"], "peer")
                self.assertEqual(row["clientId"], "anonymous" if expected == 200 else "unauthenticated")
                if env:
                    async with client.get("/walletAgeDays", params=INPUT,
                                          headers={"x-api-key": "named-only"}) as response:
                        self.assertEqual(response.status, 200)

    async def test_ambiguous_client_configuration_fails_without_exposing_values(self):
        for name, key in [("OTHER", "test-key"), ("SECOND", "first-example"), ("LEGACY", "key-example"),
                          ("", "key-example"), ("ACME", ""), ("acme", "key-example")]:
            env = {"WALLET_CLIENT_KEY_ACME": "first-example", "WALLET_CLIENT_KEY_" + name: key}
            with self.assertRaises(ValueError) as caught:
                await create_app({**self.config, "_env": env}, service=self.service)
            for value in ("test-key", "key-example", "first-example"):
                self.assertNotIn(value, str(caught.exception))

    async def test_railway_denies_data_when_keys_are_missing_or_only_public_demo(self):
        for shared in ("", DEMO_KEY):
            config = {**self.config, "server": {"api_key": shared},
                      "_env": {"RAILWAY_ENVIRONMENT_ID": "fixture"}}
            app = await create_app(config, service=self.service, legacy={"WalletInsights": self.insights})
            async with TestClient(TestServer(app)) as client:
                for path in ("/walletAgeDays", "/v1/walletAgeDays", "/v1/WalletInsights"):
                    for headers in ({}, {"x-api-key": DEMO_KEY}):
                        async with client.get(path, params=INPUT, headers=headers) as response:
                            self.assertEqual((response.status, await response.json()), (401, "unauthorized"))
                async with client.get("/health") as response:
                    self.assertEqual(response.status, 200)
        self.assert_no_checks()

    async def test_quota_follows_client_across_versions_and_spoofed_ips(self):
        config = {**self.config, "server": {**self.config["server"], "requests_per_minute": 2}}
        app = await create_app(config, service=self.service, legacy={"WalletInsights": self.insights})
        async with TestClient(TestServer(app)) as client:
            with patch("v2.app.monotonic", return_value=100) as clock:
                for path in ("/walletAgeDays", "/v1/WalletInsights"):
                    async with client.get(path, params=INPUT, headers=KEY) as response:
                        self.assertEqual(response.status, 200)
                clock.return_value = 111.2
                async with client.post("/latest/walletAgeDays", json=INPUT,
                                       headers={**KEY, "X-Real-IP": "203.0.113.2"}) as response:
                    self.assertEqual((response.status, await response.json()), (429, "rate limited"))
                    self.assertEqual(response.headers["Retry-After"], "49")
                self.service.check.assert_awaited_once()
                async with client.get("/walletAgeDays", params=INPUT, headers={"x-api-key": "beta-test-secret"}) as response:
                    self.assertEqual(response.status, 200)
                for token, status in (("wrong-one", 401), ("wrong-two", 401), ("wrong-three", 429)):
                    async with client.get("/walletAgeDays", params=INPUT, headers={"x-api-key": token}) as response:
                        self.assertEqual(response.status, status)
                clock.return_value = 160
                async with client.get("/walletAgeDays", params=INPUT, headers=KEY) as response:
                    self.assertEqual(response.status, 200)

    async def test_encoded_bodies_body_on_get_and_oversize_query_rejected_before_dispatch(self):
        status, body, _ = await self.call("/walletAgeDays", method="POST",
            data=gzip.compress(json.dumps(INPUT).encode()), headers={
                "Content-Encoding": "gzip", "Content-Type": "application/json"})
        self.assertEqual((status, body), (400, "invalid request"))
        for options in ({"data": b"ignored", "params": INPUT},
                        {"params": {**INPUT, "address": "a" * 2048}}):
            self.assertEqual((await self.call("/walletAgeDays", **options))[:2], (400, "invalid request"))
        self.assert_no_checks()

    async def test_injection_payloads_cannot_reach_provider_or_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)
            config = load_config(environ={"WALLET_CLIENT_KEY_TEST": "fixture-key",
                "WALLET_AGE_DB": str(db/"v1.sqlite3"), "WALLET_INSIGHTS_DB": str(db/"insights.sqlite3")})
            config["wallet_age"]["database"] = str(db/"v2.sqlite3")
            with patch("v2.feeds.Feeds.jobs") as feeds, patch("eth_wallet_check.http.JSONHTTP.request") as http:
                async with TestClient(TestServer(await create_app(config))) as client:
                    for payload in ("' OR 1=1; DROP TABLE first_seen;--", "$(id)", "../../etc/passwd",
                                    "http://169.254.169.254/", "0x" + "a"*40 + "\nforged-line"):
                        for path in ("/walletAgeDays", "/v1/walletAgeDays", "/WalletInsights"):
                            for method in ("GET", "POST"):
                                values = {**INPUT, "address": payload}
                                args = {"params": values} if method == "GET" else {"json": values}
                                async with client.request(method, path, headers={"x-api-key":"fixture-key"}, **args) as r:
                                    self.assertEqual((r.status, await r.json()), (400, "invalid"))
                    feeds.assert_not_called()
                    http.assert_not_called()
            with closing(sqlite3.connect(db/"v2.sqlite3")) as store:
                self.assertEqual(store.execute("SELECT COUNT(*) FROM first_seen").fetchone(), (0,))
        self.assertNotIn("forged-line", self.log.getvalue())

    async def test_default_latest_and_pinned_v2_use_same_integer_contract(self):
        for prefix in ("", "/latest", "/v2"):
            for method in ("GET", "POST"):
                options = {"params": INPUT} if method == "GET" else {"json": INPUT}
                status, body, headers = await self.call(prefix + "/walletAgeDays", method=method, **options)
                self.assertEqual((status, type(body), body), (200, int, 0))
                self.assertEqual(headers["X-API-Version"], "v2")
                self.assertEqual(headers["Cache-Control"], "no-store")
                expected = {
                    "Coverage": "ethereum", "Cache": "hit", "Basis": "first-observed-activity",
                    "Quality": "lower-bound", "Finalized": "true",
                    "Evidence": "cached-canonical", "Sources": "0",
                }
                for suffix, value in expected.items():
                    self.assertEqual(headers["X-Wallet-Age-" + suffix], value)
                self.assertNotIn("X-Wallet-Insights-Cache", headers)
        self.assertEqual(self.service.check.await_count, 6)
        self.legacy_age.check.assert_not_called()
        self.insights.check.assert_not_called()

    async def test_pinned_v1_uses_unchanged_legacy_checker(self):
        for method in ("GET", "POST"):
            options = {"params": INPUT} if method == "GET" else {"json": INPUT}
            status, body, headers = await self.call("/v1/walletAgeDays", method=method, **options)
            self.assertEqual((status, type(body), body), (200, int, 365))
            self.assertEqual(headers["X-API-Version"], "v1")
            self.assertEqual(headers["X-Wallet-Age-Sources"], "1")
        self.assertEqual(self.legacy_age.check.call_count, 2)
        self.service.check.assert_not_awaited()

    async def test_legacy_insights_calls_keep_their_object_and_headers(self):
        for prefix in ("", "/latest", "/v1"):
            status, body, headers = await self.call(prefix + "/WalletInsights", params=INPUT)
            self.assertEqual((status, body), (200, self.insights_body))
            self.assertEqual(headers["X-API-Version"], "v1")
            self.assertEqual(headers["X-Wallet-Insights-Coverage"], "ethereum")
            self.assertEqual(headers["X-Wallet-Insights-Cache"], "mixed")
            self.assertNotIn("X-Wallet-Age-Quality", headers)
        self.service.check.assert_not_awaited()
        self.legacy_age.check.assert_not_called()

    async def test_v2_does_not_silently_implement_legacy_insights(self):
        status, body, headers = await self.call("/v2/WalletInsights", params=INPUT)
        self.assertEqual((status, body), (501, "coming soon contact info@anchain.ai"))
        self.assertEqual(headers["X-API-Version"], "v2")
        self.assert_no_checks()

    async def test_authentication_precedes_checking(self):
        for path in ("/walletAgeDays", "/v1/walletAgeDays", "/WalletInsights"):
            for auth_headers in ({}, {"x-api-key": "wrong"}):
                status, body, headers = await self.call(path, params=INPUT, auth=False, headers=auth_headers)
                self.assertEqual((status, body), (401, "unauthorized"))
                self.assertEqual(headers["Cache-Control"], "no-store")
        self.assert_no_checks()

    async def test_public_health_and_specs_need_no_key(self):
        for prefix in ("", "/latest", "/v1", "/v2"):
            self.assertEqual((await self.call(prefix + "/health", auth=False))[:2], (200, "ok"))
            status, body, _ = await self.call(prefix + "/openapi.json", auth=False)
            self.assertEqual(status, 200)
            self.assertEqual(body["openapi"], "3.1.0")
            self.assertIn("/walletAgeDays", body["paths"])
        self.assert_no_checks()

    async def test_latest_setting_resolves_to_newest_registered_version(self):
        config = {**self.config, "api": {"latest": "latest"}}
        app = await create_app(config, service=self.service, legacy={
            "walletAgeDays": self.legacy_age, "WalletInsights": self.insights,
        })
        async with TestClient(TestServer(app)) as client:
            for path in ("/walletAgeDays", "/latest/walletAgeDays"):
                async with client.get(path, params=INPUT, headers=KEY) as response:
                    self.assertEqual((response.status, await response.json()), (200, 0))
                    self.assertEqual(response.headers["X-API-Version"], VERSIONS[-1])
        self.legacy_age.check.assert_not_called()

    async def test_pinned_specs_use_their_version_base_and_v2_excludes_insights(self):
        for version in ("v1", "v2"):
            status, spec, headers = await self.call(f"/{version}/openapi.json", auth=False)
            self.assertEqual(status, 200)
            self.assertEqual(spec["servers"], [{"url": f"/{version}"}])
            self.assertEqual(headers["X-API-Version"], version)
            self.assertEqual("/WalletInsights" in spec["paths"], version == "v1")
        _, spec, _ = await self.call("/openapi.json", auth=False)
        operation_ids = [operation["operationId"] for path in spec["paths"].values()
                         for operation in path.values()
                         if isinstance(operation, dict) and "operationId" in operation]
        self.assertTrue(operation_ids)
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        self.assert_no_checks()

    async def test_trailing_slash_cannot_fall_back_to_legacy_insights(self):
        self.assertEqual((await self.call("/v2/WalletInsights/", params=INPUT))[:2],
                         (404, "not_found"))
        self.assert_no_checks()

    async def test_missing_extra_and_duplicate_query_inputs_are_invalid(self):
        paths = [
            "/walletAgeDays", "/walletAgeDays?blockchain=ETH",
            f"/walletAgeDays?blockchain=ETH&address={ADDRESS}&details=true",
            f"/walletAgeDays?blockchain=ETH&blockchain=ETH&address={ADDRESS}",
            f"/walletAgeDays?blockchain=ETH&address={ADDRESS}&address={ADDRESS}",
        ]
        for path in paths:
            self.assertEqual((await self.call(path))[:2], (400, "invalid request"))
        self.assert_no_checks()

    async def test_post_rejects_duplicate_keys_wrong_type_and_large_body(self):
        bad_bodies = [
            "", "{", "[]", "null", "{}",
            '{"blockchain":"ETH","blockchain":"EVM","address":"' + ADDRESS + '"}',
            '{"blockchain":"ETH","address":"' + ADDRESS + '","details":true}',
            " " * 1025,
        ]
        for body in bad_bodies:
            self.assertEqual((await self.call("/walletAgeDays", method="POST", data=body,
                headers={"Content-Type": "application/json"}))[:2], (400, "invalid request"))
        self.assert_no_checks()

    async def test_post_rejects_query_and_wrong_content_type_but_accepts_charset(self):
        self.assertEqual((await self.call("/walletAgeDays?extra=1", method="POST", json=INPUT))[:2],
                         (400, "invalid request"))
        self.assertEqual((await self.call("/walletAgeDays", method="POST", data="{}",
            headers={"Content-Type": "text/plain"}))[:2], (400, "invalid request"))
        self.assert_no_checks()
        status, body, _ = await self.call("/walletAgeDays", method="POST", json=INPUT,
            headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual((status, body), (200, 0))

    async def test_unsupported_methods_and_routes_return_json_errors(self):
        for method in ("PUT", "PATCH", "DELETE"):
            self.assertEqual((await self.call("/walletAgeDays", method=method))[:2],
                             (405, "method not allowed"))
        for path in ("/v99/walletAgeDays", "/missing"):
            self.assertEqual((await self.call(path))[:2], (404, "not_found"))
        self.assert_no_checks()

    async def test_service_errors_preserve_status_and_string_body(self):
        from v2.service import AgeError
        for status, message in ((400, "invalid"), (404, "not_found"),
                                (501, "coming soon contact info@anchain.ai"), (503, "unknown")):
            self.service.check.side_effect = AgeError(status, message)
            response_status, body, headers = await self.call("/walletAgeDays", params=INPUT)
            self.assertEqual((response_status, body), (status, message))
            self.assertEqual(headers["Cache-Control"], "no-store")
        self.legacy_age.check.assert_not_called()

    async def test_unexpected_errors_do_not_leak_upstream_configuration(self):
        for error in (RuntimeError, ValueError):
            self.service.check.side_effect = error("https://private.example/SECRET")
            status, body, _ = await self.call("/walletAgeDays", params=INPUT)
            self.assertEqual((status, body), (503, "unknown"))

    async def test_legacy_error_and_unavailable_insights_status_are_preserved(self):
        from wallet_age_days.service import WalletAgeError
        self.legacy_age.check.side_effect = WalletAgeError(400, "invalid")
        self.assertEqual((await self.call("/v1/walletAgeDays", params=INPUT))[:2], (400, "invalid"))
        self.insights.check.return_value = (503, self.insights_body)
        self.assertEqual((await self.call("/WalletInsights", params=INPUT))[:2], (503, self.insights_body))
        self.service.check.assert_not_awaited()

    async def test_shutdown_waits_for_legacy_thread_after_client_cancellation(self):
        started, release, completed = threading.Event(), threading.Event(), threading.Event()
        result = self.legacy_age.check.return_value

        def blocking_check(*_args):
            started.set()
            if not release.wait(2):
                raise RuntimeError("Test did not release the legacy worker")
            completed.set()
            return result

        self.legacy_age.check.side_effect = blocking_check
        request = asyncio.create_task(self.call("/v1/walletAgeDays", params=INPUT))
        cleanup = None
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            cleanup = asyncio.create_task(self.client.close())
            for _ in range(100):
                if self.service.close.await_count:
                    break
                await asyncio.sleep(.001)
            self.service.close.assert_awaited()
            self.assertFalse(cleanup.done())
            self.legacy_age.close.assert_not_called()
            release.set()
            await asyncio.wait_for(cleanup, 1)
            self.assertTrue(completed.is_set())
            self.legacy_age.close.assert_called_once()
            self.assertEqual(json.loads(self.log.getvalue().splitlines()[-1])["status"], 499)
        finally:
            release.set()
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
