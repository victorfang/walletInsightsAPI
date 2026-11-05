"""Chain-neutral API dispatch; adapters own validation, lookup and caching."""

from .api import api_error, format_result
from .protocols import COMING_SOON_MESSAGE, canonical_proto


class AddressAgeDispatcher:
    """Register a checker per implemented protocol without changing the route.

    Each checker exposes check(address, details=False). Addresses are passed
    unchanged: case normalization and validation belong to the chain adapter.
    """

    def __init__(self, adapters):
        self.adapters = {}
        for proto, adapter in adapters.items():
            normalized = canonical_proto(proto)
            if normalized in self.adapters:
                raise ValueError("Duplicate protocol adapter")
            self.adapters[normalized] = adapter

    def check(self, proto, address, details=False):
        try:
            normalized = canonical_proto(proto)
        except ValueError as exc:
            return 400, api_error(400, str(exc))
        if not isinstance(address, str) or not address:
            return 400, api_error(400, "address is required")
        adapter = self.adapters.get(normalized)
        if adapter is None:
            return 501, api_error(501, COMING_SOON_MESSAGE)
        result = dict(adapter.check(address, details=details))
        result["proto"] = normalized
        return format_result(result)
