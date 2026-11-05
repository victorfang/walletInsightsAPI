"""Public, keyless Ethereum history evidence with independent fallbacks."""

from datetime import datetime, timezone
import re
from urllib.parse import urlencode

from .http import SourceError


_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")
_HISTORIES = (
    ("txlist", "normal_transactions", "normal_transaction"),
    ("txlistinternal", "internal_transactions", "internal_transaction"),
    ("tokentx", "erc20_transfers", "erc20_transfer"),
    ("tokennfttx", "erc721_transfers", "erc721_transfer"),
    ("token1155tx", "erc1155_transfers", "erc1155_transfer"),
)
_FIRST_WARNING = (
    "First seen is the earliest observation in the successfully queried histories; "
    "it may omit genesis allocations, withdrawals, block rewards and contract creation."
)


def _integer(value, name):
    if isinstance(value, bool):
        raise SourceError(f"Invalid {name}")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value and value.isascii() and value.isdecimal():
        try:
            return int(value)
        except ValueError:
            pass
    raise SourceError(f"Invalid {name}")


def _object(value, name):
    if not isinstance(value, dict):
        raise SourceError(f"Invalid {name} response")
    return value


def _address_matches(data, address, field):
    value = data.get(field)
    if not isinstance(value, str) or value.lower() != address.lower():
        raise SourceError("Response address did not match request")


def _result(source, seen=None):
    return {"source": source, "seen": seen, "scope": [], "evidence": [], "warnings": []}


def _rate_limited(error):
    return str(error).startswith("HTTP 429")


class PublicHistory:
    """Check indexed activity, never confusing missing evidence with key creation."""

    def __init__(self, http):
        self.http = http

    def inspect(self, address, details=False):
        attempts = []
        partial = None
        for name, adapter in (("blockscout", self._blockscout),
                              ("routescan", self._routescan),
                              ("blockcypher", self._blockcypher)):
            try:
                result = adapter(address, details)
            except SourceError as exc:
                attempts.append({"source": name, "status": "error", "error": str(exc)})
                continue
            status = "success" if result["seen"] is not None else "partial"
            attempts.append({"source": name, "status": status})
            if result["seen"] is not None:
                result["attempts"] = attempts
                return result
            if partial is None or len(result["scope"]) > len(partial["scope"]):
                partial = result
        result = partial or _result(None)
        result["attempts"] = attempts
        result["warnings"].append("No public index returned conclusive appearance evidence for its supported scope.")
        return result

    def _blockscout(self, address, details):
        base = "https://eth.blockscout.com"
        info = _object(self.http(f"{base}/api/v2/addresses/{address}"), "address")
        _address_matches(info, address, "hash")
        flag_fields = ("has_token_transfers", "has_logs", "has_beacon_chain_withdrawals",
                       "has_validated_blocks", "has_tokens", "is_contract")
        for key in flag_fields:
            if not isinstance(info.get(key), bool):
                raise SourceError(f"Invalid {key}")
        if "coin_balance" not in info:
            raise SourceError("Missing coin balance")
        balance = None if info["coin_balance"] is None else _integer(info["coin_balance"], "coin balance")
        creation = info.get("creation_transaction_hash")
        if creation is not None and (not isinstance(creation, str) or not _HASH.fullmatch(creation)):
            raise SourceError("Invalid creation transaction hash")
        evidence = [key for key in flag_fields if info[key]]
        if balance is not None and balance > 0:
            evidence.append("positive native balance")
        if creation:
            evidence.append("contract creation transaction")
        counter_warning = None
        rate_limited = False
        try:
            tabs = _object(self.http(f"{base}/api/v2/addresses/{address}/tabs-counters"), "activity counters")
            fields = ("transactions_count", "internal_transactions_count", "token_transfers_count",
                      "withdrawals_count", "validations_count", "beacon_deposits_count",
                      "logs_count", "token_balances_count")
            counters = {key: _integer(tabs.get(key), key) for key in fields}
            evidence.extend(f"{key} > 0" for key, count in counters.items() if count > 0)
        except SourceError as exc:
            if not evidence:
                raise
            counter_warning = f"Activity counters unavailable: {exc}"
            rate_limited = _rate_limited(exc)
        result = _result("blockscout", bool(evidence))
        result["scope"] = [item[1] for item in _HISTORIES] + [
            "contract_logs", "beacon_withdrawals", "beacon_deposits", "block_validation",
            "current_token_balances", "current_native_balance", "current_contract_code",
        ]
        result["evidence"] = evidence or ["All indexed activity counters are zero and address activity flags are false."]
        if counter_warning:
            result["warnings"].append(counter_warning)
            result["scope"] = ["address_activity_flags", "current_native_balance", "current_contract_code"]
        if balance is not None:
            result["balance_wei"] = str(balance)
        if details:
            if not rate_limited:
                try:
                    counts = _object(self.http(f"{base}/api/v2/addresses/{address}/counters"), "exact counters")
                    result["transaction_count"] = _integer(counts.get("transactions_count"), "transaction count")
                    result["count_scope"] = "Blockscout indexed normal transactions involving the address"
                except SourceError as exc:
                    result["warnings"].append(f"Exact transaction count unavailable: {exc}")
                    rate_limited = _rate_limited(exc)
            if rate_limited:
                observed, scope = [], []
                warnings = ["Blockscout rate limit reached; remaining history queries were skipped."]
            else:
                observed, scope, warnings = self._histories(f"{base}/api", address)
            if not observed or len(scope) < len(_HISTORIES):
                fallback_rows, fallback_scope, fallback_warnings = self._histories(
                    "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan", address,
                )
                observed.extend(fallback_rows)
                scope = list(dict.fromkeys(scope + fallback_scope))
                warnings.append("Routescan was queried to supplement unavailable or incomplete Blockscout first-occurrence history.")
                warnings.extend(fallback_warnings)
            self._add_first(result, observed, scope, warnings)
        return result

    def _histories(self, base, address, stop_on_positive=False):
        observed, scope, warnings = [], [], []
        source = "blockscout" if "blockscout.com" in base else "routescan"
        for action, coverage, kind in _HISTORIES:
            query = urlencode({"module": "account", "action": action, "address": address,
                               "startblock": 0, "endblock": 99999999,
                               "page": 1, "offset": 1, "sort": "asc"})
            try:
                data = _object(self.http(f"{base}?{query}"), action)
                rows = data.get("result")
                status, message = data.get("status"), data.get("message")
                partial = ("blockscout.com" in base and action == "txlistinternal" and
                           status == "2" and message ==
                           "Some internal transactions within this block range have not yet been processed")
                if not isinstance(rows, list) or (status not in ("0", "1") and not partial):
                    raise SourceError(f"Invalid {action} result")
                empty_messages = {"no transactions found", "no internal transactions found",
                                  "no token transfers found", "no records found"}
                if status == "0" and (rows or not isinstance(message, str) or
                                       message.lower() not in empty_messages):
                    raise SourceError(f"{action} returned an upstream error")
                if rows:
                    row = _object(rows[0], action)
                    involved = any(isinstance(row.get(key), str) and row[key].lower() == address.lower()
                                   for key in ("from", "to", "contractAddress"))
                    if not involved:
                        raise SourceError(f"{action} returned an unrelated transaction")
                    block = _integer(row.get("blockNumber"), "block number")
                    seconds = _integer(row.get("timeStamp"), "transaction timestamp")
                    tx_hash = row.get("hash") or row.get("transactionHash")
                    if not isinstance(tx_hash, str) or not _HASH.fullmatch(tx_hash):
                        raise SourceError("Invalid transaction hash")
                    try:
                        stamp = datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
                    except (ValueError, OverflowError, OSError):
                        raise SourceError("Invalid transaction timestamp") from None
                    observed.append({"block_number": block, "timestamp": stamp,
                                     "transaction_hash": tx_hash, "kind": kind, "source": source})
                if partial:
                    warnings.append(f"{coverage} history is incompletely indexed; any returned row is an observation only.")
                else:
                    scope.append(coverage)
                if observed and stop_on_positive:
                    break
            except SourceError as exc:
                warnings.append(f"{coverage} history unavailable: {exc}")
                if _rate_limited(exc):
                    warnings.append(f"{source.capitalize()} rate limit reached; remaining history queries were skipped.")
                    break
        return observed, scope, warnings

    @staticmethod
    def _add_first(result, observed, scope, warnings):
        result["first_seen_scope"] = scope
        result["warnings"].extend(warnings)
        result["warnings"].append(_FIRST_WARNING)
        if observed:
            result["first_seen"] = min(observed, key=lambda row: (row["block_number"], row["timestamp"]))
            result["seen"] = True
            result["evidence"].append("A transaction or transfer history contains the address.")

    def _routescan(self, address, details):
        observed, scope, warnings = self._histories(
            "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan", address,
            stop_on_positive=not details,
        )
        if not scope and not observed:
            raise SourceError("; ".join(warnings) or "No usable history response")
        seen = True if observed else (False if len(scope) == len(_HISTORIES) else None)
        result = _result("routescan", seen)
        result["scope"] = scope
        result["warnings"] = warnings
        if observed:
            result["evidence"] = ["A transaction or transfer history contains the address."]
        elif seen is False:
            result["evidence"] = ["All five queried transaction and transfer histories are empty."]
        if details:
            self._add_first(result, observed, scope, [])
        return result

    def _blockcypher(self, address, details):
        data = _object(self.http(f"https://api.blockcypher.com/v1/eth/main/addrs/{address}/balance"), "address balance")
        if "address" in data:
            _address_matches(data, address, "address")
        count = _integer(data.get("n_tx"), "transaction count")
        balance = _integer(data.get("balance"), "native balance")
        nonce = _integer(data["nonce"], "nonce") if "nonce" in data else 0
        evidence = []
        if count:
            evidence.append("BlockCypher indexed confirmed transactions")
        if balance:
            evidence.append("positive native balance")
        if nonce:
            evidence.append("positive account nonce")
        result = _result("blockcypher", True if evidence else None)
        result["scope"] = ["blockcypher_confirmed_transactions", "current_native_balance"]
        if "nonce" in data:
            result["scope"].append("account_nonce")
        result["evidence"] = evidence
        result["balance_wei"] = str(balance)
        if details:
            result["transaction_count"] = count
            result["count_scope"] = "BlockCypher confirmed address transactions; provider-specific coverage"
        if not evidence:
            result["warnings"].append(
                "Zero native balance and transaction count do not exclude token-only or other indexed activity."
            )
        return result
