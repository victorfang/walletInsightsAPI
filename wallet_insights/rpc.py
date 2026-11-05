"""Pinned Ethereum account snapshots from ordinary JSON-RPC providers.

This adapter creates no threads, implements no retry loop and owns no cache.
The caller supplies a shared HTTP deadline and races configured endpoints.
Each returned snapshot belongs to one verified Ethereum block on one provider.
"""

import re
import time

from eth_wallet_check.rpc import RPCResponseError, _quantity, _request, _result


_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}", re.ASCII)
_HASH = re.compile(r"0x[0-9a-fA-F]{64}", re.ASCII)
_CODE = re.compile(r"0x(?:[0-9a-fA-F]{2})*", re.ASCII)
_ABI_WORD = re.compile(r"0x[0-9a-fA-F]{64}", re.ASCII)
_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,15}", re.ASCII)


def _batch(response, identifiers, *, allow_missing=False):
    if not isinstance(response, list):
        raise RPCResponseError("Expected a JSON-RPC batch")
    by_id = {}
    for item in response:
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            raise RPCResponseError("Invalid JSON-RPC response ID")
        identifier = item["id"]
        if identifier not in identifiers or identifier in by_id:
            raise RPCResponseError("Duplicate or unexpected JSON-RPC response ID")
        by_id[identifier] = item
    if not allow_missing and set(by_id) != set(identifiers):
        raise RPCResponseError("Incomplete JSON-RPC batch")
    return by_id


def _assets(assets):
    if not isinstance(assets, (tuple, list)) or len(assets) > 16:
        raise ValueError("assets must contain at most sixteen configured tokens")
    normalized, seen = [], set()
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("Invalid configured token")
        symbol, address, decimals = (asset.get(field) for field in ("symbol", "address", "decimals"))
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ValueError("Invalid configured token symbol")
        if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
            raise ValueError("Invalid configured token address")
        if type(decimals) is not int or not 0 <= decimals <= 255:
            raise ValueError("Invalid configured token decimals")
        address = address.lower()
        if address in seen:
            raise ValueError("Duplicate configured token address")
        seen.add(address)
        normalized.append({"symbol": symbol, "address": address, "decimals": decimals})
    return normalized


def code_profile(code):
    """Describe code; empty code is not proof of an EOA or account ownership."""
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise RPCResponseError("Invalid account code")
    code = code.lower()
    size = (len(code) - 2) // 2
    kind, target = "contract_code", None
    if size == 0:
        kind = "empty_code"
    elif size == 23 and code.startswith("0xef0100"):
        kind, target = "delegated", "0x" + code[8:]
    return {"kind": kind, "delegation_target": target, "code_size": size}


class SnapshotRPC:
    """Read native balance, raw nonce, code and configured ERC-20 balances.

    ``endpoints`` is kept for caller inspection; ``inspect_endpoint`` performs
    exactly one provider attempt. Every state query is pinned to the head
    obtained in the first batch, never to independent moving ``latest`` tags.
    The raw nonce is deliberately not named a transaction count: EIP-7702
    authorizations and contract creation can also increment account nonces.
    """

    def __init__(self, http, endpoints, *, clock=time.time, max_head_age_seconds=300):
        self.http, self.endpoints = http, tuple(endpoints)
        self.clock, self.max_head_age_seconds = clock, max_head_age_seconds

    def inspect_endpoint(self, name, url, address, assets):
        if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
            raise ValueError("Invalid Ethereum address")
        address = address.lower()
        assets = _assets(assets)
        first = _batch(self.http(url, [
            _request(1, "eth_chainId", []),
            _request(2, "eth_getBlockByNumber", ["latest", False]),
        ]), {1, 2})
        if _quantity(_result(first[1], 1)) != 1:
            raise RPCResponseError("RPC is not Ethereum mainnet")
        block = _result(first[2], 2)
        if not isinstance(block, dict):
            raise RPCResponseError("Invalid Ethereum head block")
        number, timestamp = _quantity(block.get("number")), _quantity(block.get("timestamp"))
        block_hash = block.get("hash")
        if number <= 0 or not isinstance(block_hash, str) or not _HASH.fullmatch(block_hash):
            raise RPCResponseError("Invalid Ethereum head identity")
        now = self.clock()
        if timestamp <= 0 or timestamp > now + 60 or now - timestamp > self.max_head_age_seconds:
            raise RPCResponseError("Ethereum head is stale or has an invalid timestamp")

        tag = hex(number)
        requests = [_request(3, "eth_getBalance", [address, tag]),
                    _request(4, "eth_getTransactionCount", [address, tag]),
                    _request(5, "eth_getCode", [address, tag])]
        data = "0x70a08231" + address[2:].rjust(64, "0")
        for identifier, asset in enumerate(assets, 6):
            requests.append(_request(identifier, "eth_call", [
                {"to": asset["address"], "data": data}, tag]))
        states = _batch(self.http(url, requests), {item["id"] for item in requests}, allow_missing=True)
        warnings = []

        def value(identifier, decoder, label):
            try:
                if identifier not in states:
                    raise RPCResponseError("Missing JSON-RPC field")
                return decoder(_result(states[identifier], identifier))
            except (RPCResponseError, ValueError, TypeError):
                warnings.append(f"{label} unavailable from this snapshot.")
                return None

        balance = value(3, lambda raw: str(_quantity(raw)), "Native balance")
        nonce = value(4, lambda raw: str(_quantity(raw)), "Account nonce")
        profile = value(5, code_profile, "Code profile")

        def token_balance(raw):
            if not isinstance(raw, str) or not _ABI_WORD.fullmatch(raw):
                raise RPCResponseError("Invalid ERC-20 balance word")
            return str(int(raw, 16))

        tokens = []
        for identifier, asset in enumerate(assets, 6):
            raw = value(identifier, token_balance, asset["symbol"] + " balance")
            tokens.append({**asset, "balance_raw": raw,
                           "status": "available" if raw is not None else "unavailable"})
        return {
            "source": name,
            "block": {"number": number, "hash": block_hash.lower(), "timestamp": timestamp},
            "native_balance_wei": balance,
            "account_nonce": nonce,
            "code_profile": profile,
            "token_balances": tokens,
            "complete": balance is not None and nonce is not None and profile is not None and
                        all(token["status"] == "available" for token in tokens),
            "warnings": warnings,
        }
