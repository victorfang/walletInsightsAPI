"""Optional paid RPC endpoints, with the public fallbacks always retained."""

from __future__ import annotations

from collections.abc import Mapping
import os
from urllib.parse import quote, urlsplit

from .rpc import DEFAULT_RPC_ENDPOINTS


def _value(environ: Mapping[str, str], name: str) -> str:
    return environ.get(name, "").strip()


def _https_url(value: str, name: str) -> str:
    # These URLs may contain API keys. Never include them in exceptions.
    message = f"{name} must be an HTTPS URL with a host, without user information or a fragment."
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https"
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and "#" not in value
            and "\\" not in value
            and not any(character.isspace() or ord(character) < 32 for character in value)
        )
        # Accessing port also validates malformed or out-of-range port values.
        parts.port
    except ValueError:
        raise ValueError(message) from None
    if not valid:
        raise ValueError(message)
    return value


def rpc_endpoints(
    environ: Mapping[str, str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Prefer configured Alchemy, then QuickNode, then three public RPCs.

    Full Alchemy URLs override API-key construction. QuickNode endpoints must
    be supplied in full because their hostname and path are account-specific.
    No credentials are required when using the public defaults.
    """
    environ = os.environ if environ is None else environ
    endpoints = []
    alchemy_url = _value(environ, "ALCHEMY_RPC_URL")
    if alchemy_url:
        endpoints.append(("alchemy", _https_url(alchemy_url, "ALCHEMY_RPC_URL")))
    else:
        alchemy_key = _value(environ, "ALCHEMY_API_KEY")
        if alchemy_key:
            endpoints.append((
                "alchemy",
                "https://eth-mainnet.g.alchemy.com/v2/" + quote(alchemy_key, safe=""),
            ))
    quicknode_url = _value(environ, "QUICKNODE_RPC_URL")
    if quicknode_url:
        endpoints.append(("quicknode", _https_url(quicknode_url, "QUICKNODE_RPC_URL")))
    return tuple(endpoints) + DEFAULT_RPC_ENDPOINTS
