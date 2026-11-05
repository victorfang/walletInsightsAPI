"""Standalone read-only HTTP API using the Python standard library."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .api import api_error
from .dispatch import AddressAgeDispatcher


LOGGER = logging.getLogger(__name__)
ADDRESS_ROUTE = "/v1/ethereum/addresses/"
API_ROUTE = "/api/analytics/address/check"


class WalletHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, checker, api_key=None, *, adapters=None):
        self.checker = checker
        self.dispatcher = AddressAgeDispatcher({"eth": checker} if adapters is None else adapters)
        self.api_key = api_key
        super().__init__(address, WalletHandler)


class WalletHandler(BaseHTTPRequestHandler):
    server_version = "AddressAgeCheck/1.0"

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if path.path == "/openapi.json":
            with (Path(__file__).resolve().parent.parent / "openapi.json").open() as source:
                self._json(200, json.load(source))
            return
        canonical = path.path == API_ROUTE
        legacy = path.path.startswith(ADDRESS_ROUTE)
        if not canonical and not legacy:
            self._json(404, api_error(404, "Not found"))
            return
        expected = self.server.api_key
        supplied = self.headers.get("x-api-key", "")
        if expected and not hmac.compare_digest(expected.encode(), supplied.encode()):
            self._json(401, api_error(401, "Unauthorized - invalid API key"))
            return
        try:
            query = parse_qs(path.query, keep_blank_values=True, max_num_fields=20)
        except ValueError:
            self._json(400, api_error(400, "Too many query parameters"))
            return
        allowed = {"proto", "address", "details"} if canonical else {"details"}
        if set(query) - allowed or any(len(values) != 1 for values in query.values()):
            self._json(400, api_error(400, "Unsupported or duplicate query parameters"))
            return
        if canonical:
            proto = query.get("proto", [None])[0]
            address = query.get("address", [""])[0]
            if not address:
                self._json(400, api_error(400, "address is required"))
                return
        else:
            proto = "eth"
            address = unquote(path.path[len(ADDRESS_ROUTE):])
        details_value = query.get("details", ["false"])[0]
        if details_value not in {"true", "false"}:
            self._json(400, api_error(400, "details must be true or false"))
            return
        try:
            status, body = self.server.dispatcher.check(proto, address, details=details_value == "true")
        except Exception:
            LOGGER.exception("Wallet check failed")
            self._json(500, api_error(500, "Wallet check failed"))
            return
        self._json(status, body)

    def do_POST(self):
        self._json(405, api_error(405, "Method not allowed - use GET"), allow="GET")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def _json(self, status, body, allow=None):
        encoded = json.dumps(body, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        # Disk cache owns freshness; intermediaries must not reset its 24h lifetime.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if allow is not None:
            self.send_header("Allow", allow)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        LOGGER.info(fmt, *args)
