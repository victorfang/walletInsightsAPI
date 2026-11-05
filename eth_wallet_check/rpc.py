"""Read current Ethereum account state with independent public RPC fallbacks.

Nonce is deliberately returned as ``nonce``, never as a transaction count.
Empty account state is not proof that the address has never appeared on chain.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .http import SourceError


DEFAULT_RPC_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("publicnode", "https://ethereum-rpc.publicnode.com"),
    ("1rpc", "https://public.1rpc.io/eth"),
    ("blockpi", "https://ethereum.public.blockpi.network/v1/rpc/public"),
)

_QUANTITY = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)")
_CODE = re.compile(r"0x(?:[0-9a-fA-F]{2})*")


class RPCResponseError(ValueError):
    """A malformed or unsuccessful RPC response; messages contain no URLs."""


def _request(identifier: int, method: str, params: list[Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}


def _result(response: Any, identifier: int) -> Any:
    if not isinstance(response, dict):
        raise RPCResponseError("Expected a JSON-RPC object")
    if response.get("jsonrpc") != "2.0":
        raise RPCResponseError("Invalid JSON-RPC version")
    if type(response.get("id")) is not int or response["id"] != identifier:
        raise RPCResponseError("Unexpected JSON-RPC response ID")
    if "error" in response:
        # Provider messages can contain request URLs or other sensitive details.
        raise RPCResponseError("JSON-RPC returned an error")
    if "result" not in response:
        raise RPCResponseError("Missing JSON-RPC result")
    return response["result"]


def _quantity(value: Any) -> int:
    if not isinstance(value, str) or _QUANTITY.fullmatch(value) is None:
        raise RPCResponseError("Invalid hexadecimal quantity")
    return int(value, 16)


def _safe_error(error: Exception) -> str:
    if isinstance(error, (RPCResponseError, SourceError)):
        return str(error)
    # Do not expose transport exception text: configured URLs may contain keys.
    code = getattr(error, "code", None)
    if type(code) is int:
        return f"HTTP {code}"
    return type(error).__name__


class PublicRPC:
    """Inspect balance, nonce and code at one block, falling back on failure."""

    def __init__(
        self,
        http: Callable[..., Any],
        endpoints: tuple[tuple[str, str], ...] = DEFAULT_RPC_ENDPOINTS,
    ) -> None:
        self.http = http
        self.endpoints = endpoints

    def inspect(self, address: str) -> dict[str, Any]:
        attempts: list[dict[str, str]] = []
        for name, url in self.endpoints:
            try:
                chain = self.http(url, _request(1, "eth_chainId", []))
                if _quantity(_result(chain, 1)) != 1:
                    raise RPCResponseError("RPC is not Ethereum mainnet")

                head = self.http(url, _request(2, "eth_blockNumber", []))
                block_number = _quantity(_result(head, 2))
                block_tag = hex(block_number)
                batch = self.http(
                    url,
                    [
                        _request(3, "eth_getBalance", [address, block_tag]),
                        _request(4, "eth_getTransactionCount", [address, block_tag]),
                        _request(5, "eth_getCode", [address, block_tag]),
                    ],
                )
                if not isinstance(batch, list) or len(batch) != 3:
                    raise RPCResponseError("Invalid JSON-RPC batch response")
                by_id: dict[int, Any] = {}
                for item in batch:
                    if not isinstance(item, dict) or type(item.get("id")) is not int:
                        raise RPCResponseError("Invalid JSON-RPC batch response ID")
                    identifier = item["id"]
                    if identifier not in (3, 4, 5) or identifier in by_id:
                        raise RPCResponseError("Duplicate or unexpected batch response ID")
                    by_id[identifier] = _result(item, identifier)

                balance = _quantity(by_id[3])
                nonce = _quantity(by_id[4])
                code = by_id[5]
                if not isinstance(code, str) or _CODE.fullmatch(code) is None:
                    raise RPCResponseError("Invalid account code")
                code_present = code != "0x"
                evidence = []
                if balance:
                    evidence.append("nonzero_native_balance")
                if nonce:
                    evidence.append("nonzero_nonce")
                if code_present:
                    evidence.append("account_code")
                attempts.append({"provider": name, "status": "ok"})
                return {
                    "source": name,
                    "block_number": block_number,
                    "balance_wei": str(balance),
                    "nonce": str(nonce),
                    "code_present": code_present,
                    "evidence": evidence,
                    "attempts": attempts,
                }
            except Exception as error:
                attempts.append(
                    {"provider": name, "status": "error", "error": _safe_error(error)}
                )
        return {"source": None, "attempts": attempts, "evidence": []}
