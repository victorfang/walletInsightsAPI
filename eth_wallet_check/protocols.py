"""Protocol identities and display units, independent of chain implementations.

Only Ethereum is enabled. Recognizing a protocol name does not validate an
address or claim that its history adapter is implemented.
"""

import re
from types import MappingProxyType


COMING_SOON_MESSAGE = "coming soon contact info@anchain.ai"
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_ETH_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_INVALID_PROTO = "proto is required and must be a protocol identifier"
_ALIASES = MappingProxyType({
    "ethereum": "eth",
    "bitcoin": "btc",
    "solana": "sol",
    "tron": "trx",
})


def _entry(proto, name, symbol, decimals, available=False):
    return MappingProxyType({
        "proto": proto,
        "name": name,
        "available": available,
        "network": "mainnet",
        "balance_symbol": symbol,
        "balance_decimals": decimals,
    })


PROTOCOLS = MappingProxyType({
    "eth": _entry("eth", "Ethereum", "ETH", 18, available=True),
    "btc": _entry("btc", "Bitcoin", "BTC", 8),
    "sol": _entry("sol", "Solana", "SOL", 9),
    "trx": _entry("trx", "TRON", "TRX", 6),
})


def canonical_proto(value):
    """Normalize symbols/aliases while accepting future protocol identifiers.

    Unknown well-formed identifiers remain valid selectors for the dispatcher's
    coming-soon response. Malformed selectors are request errors instead.
    """
    if not isinstance(value, str) or not value.isascii():
        raise ValueError(_INVALID_PROTO)
    normalized = value.lower()
    if _IDENTIFIER.fullmatch(normalized) is None:
        raise ValueError(_INVALID_PROTO)
    return _ALIASES.get(normalized, normalized)


def protocol_metadata(proto):
    """Return a detached metadata dictionary; units never imply availability."""
    canonical = canonical_proto(proto)
    known = PROTOCOLS.get(canonical)
    if known is not None:
        return dict(known)
    return {
        "proto": canonical,
        "name": None,
        "available": False,
        "network": "mainnet",
        "balance_symbol": None,
        "balance_decimals": None,
    }


def address_key(proto, address, is_valid):
    """Keep legacy ETH decimal keys; preserve every other chain's address case."""
    canonical = canonical_proto(proto)
    if (canonical == "eth" and is_valid is True and isinstance(address, str)
            and _ETH_ADDRESS.fullmatch(address) is not None):
        return str(int(address, 16))
    return str(address)
