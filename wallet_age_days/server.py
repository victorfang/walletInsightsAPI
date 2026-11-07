"""Minimal HTTP contract: success is a bare JSON integer."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .service import WalletAgeError


MAX_BODY = 1024
INSIGHTS_PATHS = {"/walletInsights", "/WalletInsights"}
DATA_PATHS = {"/walletAgeDays"} | INSIGHTS_PATHS


class WalletAgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service, api_key=None, *, insights=None):
        self.service, self.api_key = service, api_key
        self.insights = insights
        super().__init__(address, WalletAgeHandler)


class WalletAgeHandler(BaseHTTPRequestHandler):
    server_version = "WalletAgeDays/2.0"

    def _json(self, status, value, result=None):
        payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if urlsplit(self.path).path in INSIGHTS_PATHS:
            self.send_header("X-Wallet-Insights-Coverage", "ethereum")
            if isinstance(value, dict) and "cache" in value:
                hits = [entry["hit"] for entry in value["cache"].values()]
                self.send_header("X-Wallet-Insights-Cache", "hit" if all(hits) else "mixed" if any(hits) else "miss")
        else:
            self.send_header("X-Wallet-Age-Coverage", self.server.service.coverage)
            self.send_header("X-Wallet-Age-Basis", "first-observed-activity")
            self.send_header("X-Wallet-Age-Quality", "lower-bound")
            if result is not None:
                self.send_header("X-Wallet-Age-Cache", result.cache)
                self.send_header("X-Wallet-Age-Finalized", "true" if result.finalized else "false")
                self.send_header("X-Wallet-Age-Evidence", result.evidence)
                self.send_header("X-Wallet-Age-Sources", str(result.positive_sources))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self):
        expected = self.server.api_key
        if expected and not hmac.compare_digest(expected.encode(), self.headers.get("x-api-key", "").encode()):
            self._json(401, "unauthorized")
            return False
        return True

    def _check(self, values):
        if not isinstance(values, dict) or set(values) != {"blockchain", "address"}:
            self._json(400, "invalid request")
            return
        try:
            if urlsplit(self.path).path in INSIGHTS_PATHS:
                if self.server.insights is None:
                    self._json(503, "unknown")
                    return
                status, body = self.server.insights.check(values["blockchain"], values["address"])
                self._json(status, body)
                return
            result = self.server.service.check(values["blockchain"], values["address"])
        except WalletAgeError as exc:
            self._json(exc.status, exc.message)
        except Exception:
            self._json(503, "unknown")
        else:
            self._json(200, result.days, result)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            self._json(200, "ok")
            return
        if url.path == "/openapi.json":
            self._json(200, json.loads((Path(__file__).resolve().parent.parent / "openapi.json").read_text()))
            return
        if url.path not in DATA_PATHS:
            self._json(404, "not_found")
            return
        if not self._authorized():
            return
        try:
            query = parse_qs(url.query, keep_blank_values=True, max_num_fields=2)
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("Duplicate input")
        except ValueError:
            self._json(400, "invalid request")
            return
        self._check({key: values[0] for key, values in query.items()})

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path not in DATA_PATHS:
            self._json(404, "not_found")
            return
        if not self._authorized():
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if url.query or not 0 < length <= MAX_BODY or self.headers.get_content_type() != "application/json":
                raise ValueError("Invalid body")
            # A slow or truncated upload must not leave a request thread blocked.
            self.connection.settimeout(0.2)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Truncated body")
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate input")
                    result[key] = value
                return result
            values = json.loads(raw, object_pairs_hook=unique)
        except (ValueError, OSError):
            self.close_connection = True
            self._json(400, "invalid request")
            return
        self._check(values)

    def do_PUT(self):
        self._json(405, "method not allowed")

    do_DELETE = do_PATCH = do_PUT

    def log_message(self, _format, *_args):
        # Addresses and configured credentials are not useful access-log data.
        pass
