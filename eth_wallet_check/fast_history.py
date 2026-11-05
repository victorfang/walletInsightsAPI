"""Small indexed lookups for the latency-bounded request path.

Each source can run as a separate job in the caller's bounded executor.  This
module deliberately creates no threads and owns no timeout policy.  The injected
HTTP client must enforce the caller's shared request deadline.
"""

from datetime import datetime, timezone
from urllib.parse import urlencode

from .history import PublicHistory, _HASH, _address_matches, _integer, _object, _result
from .http import SourceError
from .rpc import (DEFAULT_RPC_ENDPOINTS, RPCResponseError, _quantity, _request,
                  _result as _rpc_result, _safe_error)


_ROUTESCAN = "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan"
_FIRST_WARNING = (
    "First seen is an observed on-chain event, not proof of the address's globally "
    "earliest activity; earlier token, internal, deployment or genesis activity may exist."
)


def _stamp(seconds):
    if seconds <= 0:
        raise SourceError("Invalid transaction timestamp")
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        raise SourceError("Invalid transaction timestamp") from None


def _hash(value, label):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise SourceError(f"Invalid {label}")
    return value.lower()


def _observation(result, first, scope, finalized=False):
    result["seen"] = True
    result["first_seen"] = first
    result["first_seen_scope"] = scope
    result["first_seen_complete"] = False
    result["first_seen_finalized"] = finalized
    result["warnings"].append(_FIRST_WARNING)
    return result


class FastHistory:
    """Four independent jobs, with three keyless indexed provider fallbacks.

    ``inspect_source`` returns one normalized result, including a safe attempt
    record on failure.  ``inspect`` provides a sequential convenience fallback;
    services should race these source jobs with their shared request deadline.
    An empty limited history is never represented as proof of nonappearance.
    """

    sources = ("blockscout", "routescan", "blockcypher", "contract_creation")

    def __init__(self, http, rpc_endpoints=DEFAULT_RPC_ENDPOINTS):
        self.http = http
        self.rpc_endpoints = tuple(rpc_endpoints)
        self._public = PublicHistory(http)

    def inspect_source(self, name, address, details=False):
        if name not in self.sources:
            raise ValueError("Unknown history source")
        try:
            if name == "blockscout":
                # Presence counters only: never invoke the old five-category scan.
                result = self._public._blockscout(address, False)
            elif name == "blockcypher":
                result = self._public._blockcypher(address, details)
            elif name == "routescan":
                result = self._normal(address)
            else:
                result = self._creation(address)
        except Exception as exc:
            result = _result(name)
            result["attempts"] = [{"source": name, "status": "error", "error": _safe_error(exc)}]
            return result
        result["attempts"] = [{"source": name,
                               "status": "success" if result["seen"] is not None else "partial"}]
        return result

    def inspect(self, address, details=False):
        attempts, partial = [], None
        # Ask the one-call oldest-normal index first so an established EOA can
        # return its age without waiting for broader activity counters.
        for name in ("routescan", "blockscout", "blockcypher", "contract_creation"):
            result = self.inspect_source(name, address, details)
            attempts.extend(result["attempts"])
            if result["seen"] is not None:
                result["attempts"] = attempts
                return result
            if partial is None or len(result["scope"]) > len(partial["scope"]):
                partial = result
        result = partial or _result(None)
        result["attempts"] = attempts
        result["warnings"].append("No source returned conclusive appearance evidence within its supported scope.")
        return result

    def _normal(self, address):
        query = urlencode({"module": "account", "action": "txlist", "address": address,
                           "startblock": 0, "endblock": 99999999,
                           "page": 1, "offset": 1, "sort": "asc"})
        data = _object(self.http(f"{_ROUTESCAN}?{query}"), "normal transactions")
        rows = data.get("result")
        status, message = data.get("status"), data.get("message")
        if not isinstance(rows, list) or status not in ("0", "1"):
            raise SourceError("Invalid normal transactions result")
        if status == "0" and (rows or not isinstance(message, str) or message.lower() not in
                              {"no transactions found", "no records found"}):
            raise SourceError("Normal history returned an upstream error")
        result = _result("routescan")
        result["scope"] = ["normal_transactions"]
        if not rows:
            result["warnings"].append("Empty normal transaction history does not exclude other address activity.")
            return result
        row = _object(rows[0], "normal transaction")
        if not any(isinstance(row.get(key), str) and row[key].lower() == address.lower()
                   for key in ("from", "to", "contractAddress")):
            raise SourceError("Normal history returned an unrelated transaction")
        block = _integer(row.get("blockNumber"), "block number")
        if block <= 0:
            raise SourceError("Invalid confirmed transaction block")
        seconds = _integer(row.get("timeStamp"), "transaction timestamp")
        first = {"block_number": block, "timestamp": _stamp(seconds),
                 "transaction_hash": _hash(row.get("hash") or row.get("transactionHash"), "transaction hash"),
                 "kind": "normal_transaction", "source": "routescan"}
        if row.get("blockHash"):
            first["block_hash"] = _hash(row["blockHash"], "block hash")
        result["evidence"] = ["The indexed normal transaction history contains the address."]
        failures = []
        for name, endpoint in self.rpc_endpoints:
            try:
                verified, finalized, _ = self._verify_event(
                    endpoint, first["transaction_hash"], address, deployment=False, expected=first)
            except Exception as exc:
                failures.append({"source": name, "status": "error", "error": _safe_error(exc)})
                continue
            result["verification_attempts"] = failures + [{"source": name, "status": "success"}]
            return _observation(result, verified, ["normal_transactions"], finalized)
        result["verification_attempts"] = failures
        result["warnings"].append("The indexed first observation has not been verified as a finalized canonical transaction.")
        return _observation(result, first, ["normal_transactions"])

    def _creation(self, address):
        query = urlencode({"module": "contract", "action": "getcontractcreation", "contractaddresses": address})
        data = _object(self.http(f"{_ROUTESCAN}?{query}"), "contract creation")
        rows, status = data.get("result"), data.get("status")
        if not isinstance(rows, list) or status not in ("0", "1"):
            raise SourceError("Invalid contract creation result")
        if status == "0" and (rows or str(data.get("message", "")).lower() not in
                              {"no data found", "no records found", "no transactions found"}):
            raise SourceError("Contract creation returned an upstream error")
        result = _result("contract_creation")
        result["scope"] = ["contract_deployment"]
        if not rows:
            return result
        row = _object(rows[0], "contract creation")
        _address_matches(row, address, "contractAddress")
        tx_hash = _hash(row.get("txHash") or row.get("transactionHash"), "creation transaction hash")
        failures = []
        for name, endpoint in self.rpc_endpoints:
            try:
                first, finalized, direct = self._verify_event(endpoint, tx_hash, address, deployment=True)
            except Exception as exc:
                failures.append({"source": name, "status": "error", "error": _safe_error(exc)})
                continue
            result["evidence"] = ["A contract creation index record links the address to a canonical transaction receipt."]
            if not direct:
                result["warnings"].append(
                    "Factory deployment address attribution comes from the creation index; the receipt alone does not prove it."
                )
            result["verification_attempts"] = failures + [{"source": name, "status": "success"}]
            return _observation(result, first, ["contract_deployment"], finalized)
        result["verification_attempts"] = failures
        result["warnings"].append("Contract deployment was indexed but its canonical receipt could not be verified.")
        # An index hit still establishes provider-scoped presence; never expose an
        # unverified timestamp as an age or permanent-cache candidate.
        result["seen"] = True
        result["evidence"] = ["The contract creation index contains the address."]
        return result

    def _verify_event(self, endpoint, tx_hash, address, deployment, expected=None):
        receipt = _object(_rpc_result(self.http(endpoint, _request(
            1, "eth_getTransactionReceipt", [tx_hash])), 1), "transaction receipt")
        if _hash(receipt.get("transactionHash"), "receipt transaction hash") != tx_hash:
            raise SourceError("Receipt transaction did not match request")
        block_number = _quantity(receipt.get("blockNumber"))
        if not block_number:
            raise SourceError("Invalid deployment block")
        block_hash = _hash(receipt.get("blockHash"), "receipt block hash")
        if "status" in receipt and _quantity(receipt["status"]) != 1:
            # Failed ordinary transactions still establish address appearance;
            # failed deployments cannot create an account.
            if deployment:
                raise SourceError("Deployment transaction did not succeed")
        direct = isinstance(receipt.get("contractAddress"), str) and receipt["contractAddress"].lower() == address.lower()
        # A nonnull different direct deployment address is inconsistent.  A
        # null address is expected for factory-created contracts.
        if deployment and receipt.get("contractAddress") is not None and not direct:
            raise SourceError("Receipt deployment address did not match request")
        receipt_involves = any(isinstance(receipt.get(key), str) and receipt[key].lower() == address.lower()
                               for key in ("from", "to", "contractAddress"))
        logs = receipt.get("logs", [])
        if not isinstance(logs, list):
            raise SourceError("Invalid receipt logs")
        log_involves = any(isinstance(log, dict) and isinstance(log.get("address"), str)
                           and log["address"].lower() == address.lower() for log in logs)
        if not deployment and not (receipt_involves or log_involves):
            raise SourceError("Receipt does not contain the requested address")
        if expected and (expected["block_number"] != block_number or
                         (expected.get("block_hash") and expected["block_hash"] != block_hash)):
            raise SourceError("Indexed observation did not match its receipt")
        batch = self.http(endpoint, [
            _request(2, "eth_chainId", []),
            _request(3, "eth_getBlockByNumber", [hex(block_number), False]),
            _request(4, "eth_getBlockByNumber", ["finalized", False]),
        ])
        if not isinstance(batch, list) or len(batch) != 3:
            raise RPCResponseError("Invalid JSON-RPC batch response")
        by_id = {}
        for item in batch:
            if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] in by_id:
                raise RPCResponseError("Invalid JSON-RPC batch response ID")
            by_id[item["id"]] = item
        if set(by_id) != {2, 3, 4} or _quantity(_rpc_result(by_id[2], 2)) != 1:
            raise RPCResponseError("RPC is not Ethereum mainnet")
        block = _object(_rpc_result(by_id[3], 3), "deployment block")
        if _quantity(block.get("number")) != block_number or _hash(block.get("hash"), "block hash") != block_hash:
            raise SourceError("Receipt block did not match the canonical block")
        transactions = block.get("transactions")
        if not isinstance(transactions, list) or tx_hash not in [value.lower() for value in transactions if isinstance(value, str)]:
            raise SourceError("Canonical block does not contain the deployment transaction")
        finalized = False
        try:
            head = _object(_rpc_result(by_id[4], 4), "finalized block")
            _hash(head.get("hash"), "finalized block hash")
            finalized = _quantity(head.get("number")) >= block_number
        except (SourceError, RPCResponseError):
            # Some public RPCs lack the finalized tag; canonical verification
            # remains useful but cannot be promoted to a finalized cache entry.
            pass
        seconds = _quantity(block.get("timestamp"))
        timestamp = _stamp(seconds)
        if expected and expected["timestamp"] != timestamp:
            raise SourceError("Indexed timestamp did not match the canonical block")
        return {"block_number": block_number, "block_hash": block_hash,
                "timestamp": timestamp, "epoch": seconds, "transaction_hash": tx_hash,
                "kind": "contract_deployment" if deployment else "normal_transaction",
                "source": "routescan"}, finalized, direct or log_involves
