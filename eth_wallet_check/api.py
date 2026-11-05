"""Format wallet checks using the AnChain Data API address conventions."""

from datetime import datetime, timezone
import math
import time

from .protocols import address_key, canonical_proto, protocol_metadata


def api_error(status: int, message: str) -> dict:
    """Return the common envelope for request/authentication/server errors."""
    return {"status": status, "err_msg": message, "data": {}}


def _unix_seconds(timestamp):
    if not isinstance(timestamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def _native_balance(balance):
    if balance is None:
        return None
    try:
        amount = float(balance)
        return amount if math.isfinite(amount) else None
    except (ValueError, TypeError, OverflowError):
        return None


def format_result(result: dict, clock=time.time) -> tuple[int, dict]:
    """Map internal evidence to the public API without exposing transport details."""
    check_status = result.get("status", "unknown")
    status = 400 if check_status == "invalid_address" else (
        200 if check_status in {"seen", "no_observed_activity"} else 503
    )
    address = result.get("address", "")
    proto = canonical_proto(result.get("proto", "eth"))
    protocol = protocol_metadata(proto)
    is_valid = result.get("valid_format", False)
    key = address_key(proto, address, is_valid)
    stats = {
        "err": 0 if status == 200 else 1,
        "new_address": result.get("is_new_wallet"),
        "has_appeared": result.get("has_appeared"),
        "check_status": check_status,
    }
    if "age_status" in result:
        first = result.get("first_seen") or {}
        epoch = _unix_seconds(first.get("timestamp"))
        as_of = int(clock())
        stats.update({"age_days": max(0, (as_of - epoch) // 86400) if epoch is not None else None,
                      "age_as_of": as_of, "age_status": result["age_status"],
                      "first_txn": epoch, "first_txn_hash": first.get("transaction_hash"),
                      "first_txn_block": first.get("block_number"),
                      "first_txn_scope": result.get("first_seen_scope", [])})
    if "balance_raw" in result or "balance_wei" in result:
        first = result.get("first_seen") or {}
        raw_balance = result.get("balance_raw", result.get("balance_wei") if proto == "eth" else None)
        native_balance = result.get("balance_native", result.get("balance_eth") if proto == "eth" else None)
        stats.update({
            "balance": _native_balance(native_balance),
            "balance_raw": raw_balance,
            "balance_decimals": result.get("balance_decimals", protocol["balance_decimals"]),
            "balance_symbol": result.get("balance_symbol", protocol["balance_symbol"]),
            "txns_total": result.get("transaction_count"),
            "txns_scope": result.get("transaction_count_scope"),
            "first_txn": _unix_seconds(first.get("timestamp")),
            "first_txn_hash": first.get("transaction_hash"),
            "first_txn_block": first.get("block_number"),
            "first_txn_scope": result.get("first_seen_scope", []),
        })
        if proto == "eth":
            # Preserve the existing ETH response alongside chain-neutral units.
            stats.update({"balance_wei": raw_balance, "nonce": result.get("nonce"),
                          "code_present": result.get("code_present")})

    err_msg = ""
    if status == 400:
        err_msg = result.get("error") or "Invalid address format."
    elif status == 503:
        err_msg = "Unable to determine address activity from available sources."
    envelope = {
        "status": status,
        "err_msg": err_msg,
        "data": {
            key: {"address": address, "proto": proto,
                  "network": result.get("network", protocol["network"]),
                  "is_address_valid": is_valid, "stats": stats}
        },
        "meta": {
            "cache": result.get("cache"),
            "history_scope": result.get("history_scope", []),
            "sources": {"history": result.get("history_source"), "rpc": result.get("rpc_source")},
            "warnings": result.get("warnings", []),
            "limitations": result.get("limitations", []),
        },
    }
    if "checked_at" in result:
        envelope["verdict_time"] = result["checked_at"]
    if "age_cache" in result:
        envelope["meta"]["age_cache"] = result["age_cache"]
        envelope["meta"]["deadline"] = result.get("deadline")
    return status, envelope
