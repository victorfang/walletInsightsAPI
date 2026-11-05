"""Combine indexed history and chain state without treating outages as new wallets."""

from datetime import datetime, timezone
import re
import threading
import time


ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
CACHE_TTL_SECONDS = 24 * 60 * 60
LIMITATIONS = [
    "New means no activity observed in the reported sources and scope, not proof that keys were just created.",
    "Address format validation does not check EIP-55 checksum or prove ownership.",
    "Public indexes can lag or omit historical activity; arbitrary address mentions in calldata are not searched.",
]


def valid_address(address):
    return isinstance(address, str) and ADDRESS_PATTERN.fullmatch(address) is not None


def utc_timestamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def wei_to_eth(value):
    """Exact decimal formatting without floats or Decimal context rounding."""
    amount = int(value)
    whole, fraction = divmod(amount, 10**18)
    return str(whole) if not fraction else f"{whole}.{fraction:018d}".rstrip("0")


class WalletChecker:
    def __init__(self, history, rpc, cache, clock=time.time, rpc_first=False):
        self.history = history
        self.rpc = rpc
        self.cache = cache
        self.clock = clock
        self.ttl_seconds = cache.ttl_seconds
        self.rpc_first = rpc_first
        # Bounded locks avoid simultaneous cache misses for the same address.
        self._locks = [threading.Lock() for _ in range(64)]

    def check(self, address, details=False):
        if not valid_address(address):
            return {
                "address": address,
                "valid_format": False,
                "status": "invalid_address",
                "has_appeared": None,
                "is_new_wallet": None,
                "error": "Expected 0x followed by exactly 40 hexadecimal characters.",
            }

        address = address.lower()
        key = f"v1:ethereum:1:{address}:details={int(details)}"
        with self._locks[hash(key) % len(self._locks)]:
            cached = self.cache.get(key)
            if cached is not None:
                cached["cache"]["hit"] = True
                return cached

            # The CLI checks cheap chain state first. A positive result needs no
            # indexed history unless optional details were explicitly requested.
            rpc = self.rpc.inspect(address)
            rpc_available = rpc.get("source") is not None
            rpc_seen = rpc_available and (
                int(rpc["balance_wei"]) > 0
                or int(rpc["nonce"]) > 0
                or rpc["code_present"]
            )
            history_skipped = self.rpc_first and rpc_seen and not details
            if history_skipped:
                history = {"source": None, "seen": None, "scope": [], "evidence": [], "attempts": []}
            else:
                history = self.history.inspect(address, details=details)
            appeared = None
            if history.get("seen") is True or rpc_seen:
                appeared = True
            elif history.get("seen") is False and rpc_available:
                appeared = False

            status = (
                "seen" if appeared is True else
                "no_observed_activity" if appeared is False else "unknown"
            )
            now = self.clock()
            warnings = list(history.get("warnings", []))
            if not rpc_available:
                warnings.append("RPC sources were unavailable; current chain state could not be verified.")
            if history.get("seen") is None and not history_skipped:
                warnings.append("Historical coverage was incomplete; zero balance or nonce alone cannot establish an unused address.")
            if rpc_seen and history.get("seen") is False:
                warnings.append("Chain state shows activity despite empty indexed history; the address is treated as seen.")

            result = {
                "address": address,
                "chain": "ethereum-mainnet",
                "chain_id": 1,
                "valid_format": True,
                "status": status,
                "has_appeared": appeared,
                "is_new_wallet": None if appeared is None else not appeared,
                "checked_at": utc_timestamp(now),
                "history_source": history.get("source"),
                "history_skipped": bool(history_skipped),
                "rpc_source": rpc.get("source"),
                "history_scope": history.get("scope", []),
                "evidence": history.get("evidence", []) + rpc.get("evidence", []),
                "source_attempts": {
                    "history": history.get("attempts", []),
                    "rpc": rpc.get("attempts", []),
                },
                "warnings": warnings,
                "limitations": LIMITATIONS,
                "cache": {
                    "hit": False,
                    "stored": appeared is not None,
                    "ttl_seconds": self.ttl_seconds,
                    "expires_at": utc_timestamp(now + self.ttl_seconds) if appeared is not None else None,
                },
            }
            if details:
                balance = rpc.get("balance_wei") if rpc_available else history.get("balance_wei")
                result.update({
                    "balance_wei": balance,
                    "balance_eth": wei_to_eth(balance) if balance is not None else None,
                    "balance_source": rpc.get("source") if rpc_available else (
                        history.get("source") if balance is not None else None
                    ),
                    "nonce": rpc.get("nonce"),
                    "code_present": rpc.get("code_present"),
                    "state_block_number": rpc.get("block_number"),
                    "transaction_count": history.get("transaction_count"),
                    "transaction_count_scope": history.get("count_scope"),
                    "first_seen": history.get("first_seen"),
                    "first_seen_scope": history.get("first_seen_scope", []),
                })
            if appeared is not None:
                self.cache.set(key, result)
            return result
