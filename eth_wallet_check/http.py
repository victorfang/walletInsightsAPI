"""Small bounded JSON HTTP client; no credentials or third-party packages."""

from contextlib import contextmanager
from http.client import HTTPException
import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authenticated provider calls must never forward keys to a redirect.
        return None


def urlopen(request, timeout):
    """A non-redirecting opener, including keys embedded in URLs/query strings."""
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


class SourceError(Exception):
    """An upstream source did not return usable evidence."""


class JSONHTTP:
    def __init__(self, timeout=5.0, max_bytes=2_000_000, headers_by_url=None):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._request_context = threading.local()
        self._headers_by_url = dict(headers_by_url or {})

    @contextmanager
    def deadline_at(self, deadline, cancelled=None):
        """Apply a monotonic deadline to HTTP calls in this worker thread."""
        previous = getattr(self._request_context, "deadline", None)
        previous_cancelled = getattr(self._request_context, "cancelled", None)
        self._request_context.deadline = deadline
        self._request_context.cancelled = cancelled
        try:
            yield
        finally:
            self._request_context.deadline = previous
            self._request_context.cancelled = previous_cancelled

    def _timeout(self):
        cancelled = getattr(self._request_context, "cancelled", None)
        if cancelled is not None and cancelled.is_set():
            raise SourceError("Request cancelled")
        deadline = getattr(self._request_context, "deadline", None)
        if deadline is None:
            return self.timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError("Request deadline exceeded")
        return min(self.timeout, remaining)

    def __call__(self, url, payload=None):
        return self.request(url, payload, headers=self._headers_by_url.get(url))

    def request(self, url, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(url, data=data, headers={
            "Accept": "application/json",
            "User-Agent": "eth-wallet-check/1.0",
            **({"Content-Type": "application/json"} if data is not None else {}),
            **dict(headers or {}),
        })
        try:
            # RPC URLs and explorer query strings can contain credentials even
            # when no authentication header is present. Reject every redirect.
            opening = urlopen
            with opening(request, timeout=self._timeout()) as response:
                body = response.read(self.max_bytes + 1)
        except HTTPError as exc:
            raise SourceError(f"HTTP {exc.code}") from None
        except (HTTPException, URLError, TimeoutError, OSError) as exc:
            raise SourceError(f"Network failure ({type(exc).__name__})") from None
        if len(body) > self.max_bytes:
            raise SourceError("Response exceeded size limit")
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise SourceError("Response was not valid JSON") from None
