"""Authenticated AnChain label reads with explicit unknowns and provenance.

This adapter does not request or infer a risk score, a safe verdict, or ownership.
The injected HTTP transport must provide request(url, headers=...) and preserve
its caller's deadline. Authenticated redirects must not be followed.
"""

from datetime import datetime, timezone
import math
import re
import time
from urllib.parse import urlencode, urlsplit

from eth_wallet_check.http import SourceError


_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}", re.ASCII)
_BASE = "https://api.anchainai.com"


def _stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise SourceError("Invalid AnChain label text")
    return value


def _strings(value):
    if isinstance(value, str):
        return [_text(value)] if value else []
    if not isinstance(value, list) or len(value) > 128:
        raise SourceError("Invalid AnChain label list")
    return [_text(item) for item in value]


def _safe_error(exc):
    if isinstance(exc, SourceError):
        message = str(exc)
        if message.startswith("HTTP ") and message[5:].isdigit():
            return "http_" + message[5:]
        if "deadline" in message.lower():
            return "deadline"
    # Provider text and configured credentials never enter response metadata.
    return type(exc).__name__


def _base_url(value):
    message = "ANCHAIN_DATA_API_BASE_URL must be the official HTTPS api.anchainai.com origin."
    if not isinstance(value, str):
        raise ValueError(message)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname == "api.anchainai.com"
                 and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
                 and "\\" not in value and not any(ch.isspace() or ord(ch) < 32 for ch in value))
    except ValueError:
        raise ValueError(message) from None
    if not valid:
        raise ValueError(message)
    return value.rstrip("/")


def _record(data, address):
    if not isinstance(data, dict):
        raise SourceError("Invalid AnChain label data")
    decimal = str(int(address, 16))
    candidates = [value for key, value in data.items()
                  if isinstance(key, str) and (key.lower() == address or key == decimal)]
    if len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise SourceError("AnChain address record missing or ambiguous")
    result = candidates[0]
    if result.get("is_address_valid") is not True:
        raise SourceError("AnChain did not validate address")
    if "address" in result and (not isinstance(result["address"], str) or result["address"].lower() != address):
        raise SourceError("AnChain address record mismatch")
    return result


def _normalize(record):
    attribution = record.get("self")
    if not isinstance(attribution, dict) or not any(
            key in attribution for key in ("category", "detail", "information")):
        raise SourceError("Invalid AnChain attribution")
    attribution = {key: _strings(attribution.get(key, []))
                   for key in ("category", "detail", "information")}
    labels = [{"kind": "attribution", "category": category, "source": "anchain"}
              for category in attribution["category"]]
    osint = record.get("osint", [])
    if not isinstance(osint, list) or len(osint) > 128:
        raise SourceError("Invalid AnChain OSINT labels")
    for item in osint:
        if not isinstance(item, dict) or not isinstance(item.get("category"), str):
            raise SourceError("Invalid AnChain OSINT label")
        label = {"kind": "osint", "category": _text(item["category"]),
                 "source": _text(item.get("source", "anchain"))}
        for key in ("detail", "information", "link"):
            if key in item:
                value = item[key]
                label[key] = _strings(value) if isinstance(value, list) else _text(value)
        labels.append(label)
    return attribution, labels


class AnChainLabels:
    def __init__(self, http, base_url=_BASE, api_key=None, clock=time.time):
        self.http = http
        self.base_url = _base_url(base_url)
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError("AnChain credential must be a string")
        if api_key and any(ord(ch) < 32 or ch.isspace() for ch in api_key):
            raise ValueError("AnChain credential contains invalid whitespace")
        self._api_key = api_key
        self.clock = clock

    def inspect(self, address):
        result = {"status": "unavailable", "labels": None, "attribution": None,
                  "source": "anchain", "checked_at": _stamp(self.clock()),
                  "provider_verdict_time": None}
        if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
            result["error"] = "invalid_address"
            return result
        if not self._api_key:
            result.update(status="not_configured", error="not_configured")
            return result
        address = address.lower()
        url = self.base_url + "/api/intel/address/label?" + urlencode({"proto": "eth", "address": address})
        try:
            payload = self.http.request(url, headers={"x-api-key": self._api_key})
            if not isinstance(payload, dict) or payload.get("status") not in (200, "200"):
                raise SourceError("AnChain rejected label request")
            error = payload.get("err_msg")
            if error not in (None, ""):
                raise SourceError("AnChain rejected label request")
            record = _record(payload.get("data"), address)
            attribution, labels = _normalize(record)
            result.update(status="available", labels=labels, attribution=attribution)
            verdict = payload.get("verdict_time", record.get("verdict_time"))
            if ((isinstance(verdict, str) and len(verdict) <= 4096)
                    or type(verdict) is int
                    or (type(verdict) is float and math.isfinite(verdict))):
                result["provider_verdict_time"] = verdict
        except Exception as exc:
            result["error"] = _safe_error(exc)
        result["checked_at"] = _stamp(self.clock())
        return result
