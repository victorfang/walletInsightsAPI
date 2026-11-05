"""Small indexed first-observation lookups; no balance, nonce or count queries.

Jobs own no threads or cache writes. The caller races them under one HTTP
deadline. An empty, truncated, or unavailable history never means age zero.
All returned timestamps are observations within provider-specific coverage,
not proof of complete history across every chain or every kind of event.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
import re
from urllib.parse import urlencode

from eth_wallet_check.http import SourceError
from eth_wallet_check.rpc import _quantity, _request, _result


_HASH = re.compile(r"0x[0-9a-fA-F]{64}")
_BASES = {
    "routescan": "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan",
    "blockscout": "https://eth.blockscout.com/api",
}


@dataclass(frozen=True)
class Observation:
    epoch: int
    finalized: bool
    source: str


def _object(value):
    if not isinstance(value, dict):
        raise SourceError("Invalid provider response")
    return value


def _integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise SourceError("Invalid provider integer")


def _hash(value, prefix_required=True):
    if not prefix_required and isinstance(value, str) and not value.startswith("0x"):
        value = "0x" + value
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise SourceError("Invalid transaction or block hash")
    return value.lower()


def _epoch(value):
    if not isinstance(value, str):
        raise SourceError("Invalid provider timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        result = int(parsed.timestamp())
        if result <= 0:
            raise ValueError
        return result
    except (ValueError, OverflowError, OSError):
        raise SourceError("Invalid provider timestamp") from None


def _matches(row, address, fields):
    return any(isinstance(row.get(key), str) and row[key].lower() == address.lower()
               for key in fields)


class AppearanceSources:
    """Ethereum indexed providers plus independently configurable RPC proof.

    Alchemy Transfers is queried only on a configured Alchemy endpoint.
    QuickNode's optional Token/NFT v2 add-on is used only when its complete
    history fits one page. Standard QuickNode/public RPC verifies discovered
    events and is never presented as an arbitrary-address history index.
    """

    def __init__(self, http, rpc_endpoints, alchemy_endpoint=None, quicknode_history_enabled=False):
        self.http = http
        self.rpc_endpoints = tuple(rpc_endpoints)
        self.alchemy_endpoint = alchemy_endpoint or next(
            (url for name, url in self.rpc_endpoints if name == "alchemy"), None)
        self.quicknode_endpoint = next(
            (url for name, url in self.rpc_endpoints if name == "quicknode"), None)
        self.quicknode_history_enabled = quicknode_history_enabled

    def jobs(self, address):
        jobs = []
        if self.alchemy_endpoint:
            jobs.extend((f"alchemy_{direction}", partial(self._alchemy, address, direction))
                        for direction in ("from", "to"))
        if self.quicknode_endpoint and self.quicknode_history_enabled:
            jobs.append(("quicknode", partial(self._quicknode, address)))
        for provider in _BASES:
            jobs.extend((f"{provider}_{kind}", partial(self._indexed, provider, address, kind))
                        for kind in ("normal", "creation"))
        jobs.append(("blockcypher", partial(self._blockcypher, address)))
        return jobs

    def _indexed(self, provider, address, kind):
        params = ({"module": "contract", "action": "getcontractcreation", "contractaddresses": address}
                  if kind == "creation" else
                  {"module": "account", "action": "txlist", "address": address,
                   "startblock": 0, "endblock": 99999999, "page": 1, "offset": 1, "sort": "asc"})
        data = _object(self.http(_BASES[provider] + "?" + urlencode(params)))
        rows, status = data.get("result"), data.get("status")
        if not isinstance(rows, list) or status not in ("0", "1"):
            raise SourceError("Invalid indexed history result")
        if status == "0" and (rows or str(data.get("message", "")).lower() not in
                              {"no transactions found", "no records found", "no data found"}):
            raise SourceError("Indexed history unavailable")
        if not rows:
            return None
        row = _object(rows[0])
        if kind == "creation":
            if not _matches(row, address, ("contractAddress",)):
                raise SourceError("Unrelated contract creation result")
            candidate = {"hash": _hash(row.get("txHash") or row.get("transactionHash")),
                         "deployment": True}
        else:
            if not _matches(row, address, ("from", "to", "contractAddress")):
                raise SourceError("Unrelated indexed transaction")
            epoch = _integer(row.get("timeStamp"))
            if epoch <= 0:
                raise SourceError("Invalid indexed timestamp")
            candidate = {"hash": _hash(row.get("hash") or row.get("transactionHash")),
                         "block": _integer(row.get("blockNumber")), "epoch": epoch}
        return self._verify(candidate, address, provider)

    def _alchemy(self, address, direction):
        params = {"fromBlock": "0x0", "toBlock": "latest", f"{direction}Address": address,
                  "category": ["external", "internal", "erc20", "erc721", "erc1155", "specialnft"],
                  "order": "asc", "maxCount": "0x1", "excludeZeroValue": False, "withMetadata": True}
        result = _object(_result(self.http(self.alchemy_endpoint, _request(
            1, "alchemy_getAssetTransfers", [params])), 1))
        rows = result.get("transfers")
        if not isinstance(rows, list):
            raise SourceError("Invalid Alchemy transfer history")
        if not rows:
            return None
        row = _object(rows[0])
        if not _matches(row, address, (direction,)):
            raise SourceError("Unrelated Alchemy transfer")
        candidate = {"hash": _hash(row.get("hash")), "block": _quantity(row.get("blockNum")),
                     "index_attributed": row.get("category") == "internal"}
        metadata = row.get("metadata")
        if isinstance(metadata, dict) and metadata.get("blockTimestamp") is not None:
            candidate["epoch"] = _epoch(metadata["blockTimestamp"])
        return self._verify(candidate, address, "alchemy")

    def _quicknode(self, address):
        result = _object(_result(self.http(self.quicknode_endpoint, _request(
            1, "qn_getTransactionsByAddress", [{"address": address, "fromBlock": "0x0",
                                               "page": 1, "perPage": 100}])), 1))
        if not _matches(result, address, ("address",)):
            raise SourceError("Unrelated QuickNode history")
        rows = result.get("paginatedItems")
        total, pages = _integer(result.get("totalItems")), _integer(result.get("totalPages"))
        if not isinstance(rows, list):
            raise SourceError("Invalid QuickNode transaction history")
        # The official API documents pagination but no ascending-order option.
        # Never convert a newest-only page into a wallet's first appearance.
        if pages > 1 or total > len(rows):
            raise SourceError("QuickNode history is truncated; oldest transaction unavailable")
        if not rows:
            return None
        if total == 0 or pages == 0:
            raise SourceError("Inconsistent QuickNode history pagination")
        candidates = []
        for row in rows:
            row = _object(row)
            if not _matches(row, address, ("fromAddress", "toAddress", "contractAddress")):
                raise SourceError("Unrelated QuickNode transaction")
            candidates.append({"hash": _hash(row.get("transactionHash")),
                               "block": _integer(row.get("blockNumber")),
                               "epoch": _epoch(row.get("blockTimestamp"))})
        return self._verify(min(candidates, key=lambda item: item["epoch"]), address, "quicknode")

    def _blockcypher(self, address):
        result = _object(self.http("https://api.blockcypher.com/v1/eth/main/addrs/" + address + "?limit=50"))
        returned_address = result.get("address")
        if not isinstance(returned_address, str) or returned_address.lower().removeprefix("0x") != address.lower().removeprefix("0x"):
            raise SourceError("Unrelated BlockCypher history")
        rows = result.get("txrefs", [])
        count = _integer(result.get("n_tx"))
        if not isinstance(rows, list):
            raise SourceError("Invalid BlockCypher transaction history")
        # BlockCypher is descending-only. Only a full small history can identify
        # its oldest record without an unbounded pagination walk.
        if result.get("hasMore") is True or count > len(rows):
            raise SourceError("BlockCypher history is truncated; oldest transaction unavailable")
        if not rows:
            return None
        if count == 0:
            raise SourceError("Inconsistent BlockCypher history count")
        candidates = [{"hash": _hash(_object(row).get("tx_hash"), prefix_required=False),
                       "block": _integer(row.get("block_height")), "epoch": _epoch(row.get("confirmed"))}
                      for row in rows]
        return self._verify(min(candidates, key=lambda item: item["epoch"]), address, "blockcypher")

    def _verify(self, candidate, address, source):
        for _name, endpoint in self.rpc_endpoints:
            try:
                return self._verify_rpc(endpoint, candidate, address, source)
            except Exception:
                # Transport/provider errors may contain endpoint credentials.
                # The caller's shared deadline prevents a slow fallback walk.
                continue
        epoch = candidate.get("epoch")
        if epoch:
            return Observation(epoch, False, source)
        raise SourceError("Indexed event has no verified canonical timestamp")

    def _verify_rpc(self, endpoint, candidate, address, source):
        tx_hash = candidate["hash"]
        receipt = _object(_result(self.http(endpoint, _request(1, "eth_getTransactionReceipt", [tx_hash])), 1))
        if _hash(receipt.get("transactionHash")) != tx_hash:
            raise SourceError("Receipt transaction mismatch")
        block = _quantity(receipt.get("blockNumber"))
        block_hash = _hash(receipt.get("blockHash"))
        if not block or candidate.get("block", block) != block:
            raise SourceError("Receipt block mismatch")
        direct = _matches(receipt, address, ("contractAddress",))
        if candidate.get("deployment"):
            if receipt.get("contractAddress") is not None and not direct:
                raise SourceError("Deployment address mismatch")
            if "status" in receipt and _quantity(receipt["status"]) != 1:
                raise SourceError("Failed contract deployment")
        else:
            logs = receipt.get("logs", [])
            if not isinstance(logs, list):
                raise SourceError("Invalid receipt logs")
            topic = "0x" + "0" * 24 + address[2:].lower()
            in_logs = any(isinstance(log, dict) and (
                _matches(log, address, ("address",)) or topic in
                [value.lower() for value in log.get("topics", []) if isinstance(value, str)])
                for log in logs)
            if not (_matches(receipt, address, ("from", "to", "contractAddress")) or in_logs or candidate.get("index_attributed")):
                raise SourceError("Receipt does not involve address")
        batch = self.http(endpoint, [_request(2, "eth_chainId", []),
                                     _request(3, "eth_getBlockByNumber", [hex(block), False]),
                                     _request(4, "eth_getBlockByNumber", ["finalized", False])])
        if not isinstance(batch, list) or len(batch) != 3:
            raise SourceError("Invalid canonical verification batch")
        by_id = {}
        for item in batch:
            if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] in by_id:
                raise SourceError("Invalid canonical verification response ID")
            by_id[item["id"]] = item
        if set(by_id) != {2, 3, 4} or _quantity(_result(by_id[2], 2)) != 1:
            raise SourceError("Verification endpoint is not Ethereum mainnet")
        canonical = _object(_result(by_id[3], 3))
        if _quantity(canonical.get("number")) != block or _hash(canonical.get("hash")) != block_hash:
            raise SourceError("Receipt is not in canonical block")
        transactions = canonical.get("transactions")
        if not isinstance(transactions, list) or tx_hash not in [value.lower() for value in transactions if isinstance(value, str)]:
            raise SourceError("Canonical block omits transaction")
        epoch = _quantity(canonical.get("timestamp"))
        if epoch <= 0 or candidate.get("epoch", epoch) != epoch:
            raise SourceError("Indexed timestamp mismatch")
        finalized = False
        try:
            head = _object(_result(by_id[4], 4))
            _hash(head.get("hash"))
            finalized = _quantity(head.get("number")) >= block
        except Exception:
            pass
        return Observation(epoch, finalized, source)
