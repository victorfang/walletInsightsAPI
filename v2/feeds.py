"""Bounded indexed history discovery, verified with canonical finalized RPC data.

RPC nodes verify discovered events; they are not arbitrary-address history indexes.
Only this module knows provider wire formats. It never loads or logs credentials.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import re
import time


class FeedError(ValueError):
    """Sanitized upstream failure; do not expose transport exception messages."""


@dataclass(frozen=True)
class Observation:
    epoch: int | None
    complete: bool


def _object(value):
    if not isinstance(value, dict):
        raise FeedError("Invalid upstream object")
    return value


def _integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"(?:[0-9]+|0x[0-9a-fA-F]+)", value):
        return int(value, 16 if value.startswith("0x") else 10)
    raise FeedError("Invalid upstream integer")


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        raise FeedError("Invalid upstream hash")
    return value.lower()


def _matches(row, address, fields):
    return any(isinstance(row.get(key), str) and row[key].lower() == address for key in fields)


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.timestamp() > 0:
            return int(parsed.timestamp())
    except (AttributeError, ValueError, OverflowError, OSError):
        pass
    raise FeedError("Invalid upstream timestamp")


class Feeds:
    """One provider result groups its subqueries; aliases never add independent votes."""

    def __init__(self, session, config):
        self.session, self.config = session, config
        self.feeds = config["feeds"]
        self.limit = asyncio.Semaphore(config.get("wallet_age", {}).get("max_provider_requests", 32))
        self.locks, self.next_request = {}, {}

    def jobs(self, chain, address):
        proofs = {}
        return [(self.feeds[name].get("group") or self.feeds[name].get("company") or name,
                 self._provider(name, chain, address, proofs))
                for name in chain["history"] if self.feeds.get(name, {}).get("url")]

    async def _json(self, name, *, params=None, payload=None):
        feed = self.feeds[name]
        interval = feed.get("min_interval_ms", 0) / 1000
        if interval:
            async with self.locks.setdefault(name, asyncio.Lock()):
                await asyncio.sleep(max(0, self.next_request.get(name, 0) - time.monotonic()))
                self.next_request[name] = time.monotonic() + interval
        try:
            async with self.limit:
                async with self.session.request(
                    "POST" if payload is not None else "GET", feed["url"],
                    params={**feed.get("params", {}), **(params or {})}, json=payload,
                    headers=feed.get("headers", {}), allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise FeedError("Upstream HTTP failure")
                    try:
                        body = await response.content.readexactly(2_000_001)
                    except asyncio.IncompleteReadError as exc:
                        body = exc.partial
                    if len(body) > 2_000_000:
                        raise FeedError("Upstream response too large")
                    return json.loads(body)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise FeedError("Upstream request failed") from None

    async def _rpc(self, name, calls):
        payload = [{"jsonrpc": "2.0", "id": i, "method": method, "params": params}
                   for i, (method, params) in enumerate(calls, 1)]
        data = await self._json(name, payload=payload)
        if not isinstance(data, list) or len(data) != len(calls):
            raise FeedError("Invalid RPC batch")
        by_id = {}
        for item in data:
            item = _object(item)
            if type(item.get("id")) is not int or item["id"] in by_id or "error" in item:
                raise FeedError("Invalid RPC result")
            by_id[item["id"]] = item
        if set(by_id) != set(range(1, len(calls) + 1)):
            raise FeedError("Mismatched RPC IDs")
        return [by_id[i].get("result") for i in range(1, len(calls) + 1)]

    async def _provider(self, name, chain, address, proofs):
        adapter = self.feeds[name]["adapter"]
        if adapter == "alchemy":
            calls = [self._alchemy(name, chain, address, direction, proofs) for direction in ("from", "to")]
        elif adapter == "etherscan_compatible":
            calls = [self._indexed(name, chain, address, kind, proofs) for kind in ("normal", "creation")]
        else:
            raise FeedError("Unsupported history adapter")
        tasks = [asyncio.create_task(call) for call in calls]
        settings = self.config.get("wallet_age", {})
        budget = min(settings.get("provider_deadline_ms", 2400), settings.get("deadline_ms", 2600)) / 1000
        try:
            _, pending = await asyncio.wait(tasks, timeout=budget)
            for task in pending:
                task.cancel()
            values = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        epochs = [value for value in values if type(value) is int]
        return Observation(min(epochs) if epochs else None,
                           all(value is None or type(value) is int for value in values))

    async def _alchemy(self, name, chain, address, direction, proofs):
        params = {"fromBlock": "0x0", "toBlock": "latest", f"{direction}Address": address,
                  "category": ["external", "internal", "erc20", "erc721", "erc1155", "specialnft"],
                  "order": "asc", "maxCount": "0x1", "excludeZeroValue": False, "withMetadata": True}
        result, = await self._rpc(name, [("alchemy_getAssetTransfers", [params])])
        rows = _object(result).get("transfers")
        if not isinstance(rows, list):
            raise FeedError("Invalid transfer history")
        if not rows:
            return None
        row = _object(rows[0])
        if not _matches(row, address, (direction,)):
            raise FeedError("Unrelated transfer")
        candidate = {"hash": _hash(row.get("hash")), "block": _integer(row.get("blockNum")),
                     "index_attributed": row.get("category") == "internal"}
        if isinstance(row.get("metadata"), dict) and row["metadata"].get("blockTimestamp"):
            candidate["epoch"] = _timestamp(row["metadata"]["blockTimestamp"])
        return await self._verify(candidate, chain, address, proofs)

    async def _indexed(self, name, chain, address, kind, proofs):
        params = ({"module": "contract", "action": "getcontractcreation", "contractaddresses": address}
                  if kind == "creation" else
                  {"module": "account", "action": "txlist", "address": address, "startblock": 0,
                   "endblock": 99999999, "page": 1, "offset": 1, "sort": "asc"})
        data = _object(await self._json(name, params=params))
        rows, status = data.get("result"), data.get("status")
        if not isinstance(rows, list) or status not in ("0", "1"):
            raise FeedError("Invalid indexed history")
        if status == "0" and (rows or str(data.get("message", "")).lower() not in
                              {"no transactions found", "no records found", "no data found"}):
            raise FeedError("Indexed history unavailable")
        if not rows:
            return None
        row = _object(rows[0])
        fields = ("contractAddress",) if kind == "creation" else ("from", "to", "contractAddress")
        if not _matches(row, address, fields):
            raise FeedError("Unrelated indexed history")
        candidate = {"hash": _hash(row.get("txHash") or row.get("hash") or row.get("transactionHash")),
                     "deployment": kind == "creation"}
        if kind == "normal":
            candidate.update(block=_integer(row.get("blockNumber")), epoch=_integer(row.get("timeStamp")))
        return await self._verify(candidate, chain, address, proofs)

    async def _verify(self, candidate, chain, address, proofs):
        tx_hash = candidate["hash"]
        if tx_hash not in proofs:
            proofs[tx_hash] = asyncio.create_task(self._proof(tx_hash, chain))
        receipt, epoch = await proofs[tx_hash]
        if candidate.get("block", _integer(receipt["blockNumber"])) != _integer(receipt["blockNumber"]):
            raise FeedError("Indexed block mismatch")
        if candidate.get("epoch", epoch) != epoch or not 0 < epoch <= int(time.time()):
            raise FeedError("Indexed timestamp mismatch")
        if candidate.get("deployment"):
            if not _matches(receipt, address, ("contractAddress",)) or _integer(receipt.get("status", "0x0")) != 1:
                raise FeedError("Invalid deployment")
        else:
            logs = receipt.get("logs", [])
            if not isinstance(logs, list):
                raise FeedError("Invalid receipt logs")
            topic = "0x" + "0" * 24 + address[2:]
            in_logs = any(isinstance(log, dict) and (_matches(log, address, ("address",)) or
                          topic in [item.lower() for item in log.get("topics", []) if isinstance(item, str)])
                          for log in logs if isinstance(log, dict) and isinstance(log.get("topics", []), list))
            if not (_matches(receipt, address, ("from", "to", "contractAddress")) or
                    in_logs or candidate.get("index_attributed")):
                raise FeedError("Receipt does not involve address")
        return epoch

    async def _proof(self, tx_hash, chain):
        tasks = [asyncio.create_task(self._proof_rpc(name, tx_hash, chain["chain_id"]))
                 for name in chain["rpc"] if self.feeds.get(name, {}).get("url")]
        try:
            for completed in asyncio.as_completed(tasks):
                try:
                    return await completed
                except (FeedError, KeyError, TypeError, ValueError):
                    continue
            raise FeedError("No canonical finalized proof")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _proof_rpc(self, name, tx_hash, chain_id):
        receipt, actual_chain, head = await self._rpc(name, [
            ("eth_getTransactionReceipt", [tx_hash]), ("eth_chainId", []),
            ("eth_getBlockByNumber", ["finalized", False])])
        receipt, head = _object(receipt), _object(head)
        block = _integer(receipt.get("blockNumber"))
        if _integer(actual_chain) != chain_id or _hash(receipt.get("transactionHash")) != tx_hash:
            raise FeedError("Wrong chain or receipt")
        if _integer(head.get("number")) < block:
            raise FeedError("Event is not finalized")
        _hash(head.get("hash"))
        canonical, = await self._rpc(name, [("eth_getBlockByNumber", [hex(block), False])])
        canonical = _object(canonical)
        if (_integer(canonical.get("number")) != block or
                _hash(canonical.get("hash")) != _hash(receipt.get("blockHash"))):
            raise FeedError("Receipt is not canonical")
        transactions = canonical.get("transactions")
        if not isinstance(transactions, list) or tx_hash not in [
                value.lower() for value in transactions if isinstance(value, str)]:
            raise FeedError("Canonical block omits transaction")
        epoch = _integer(canonical.get("timestamp"))
        if not 0 < epoch <= int(time.time()):
            raise FeedError("Invalid canonical timestamp")
        return receipt, epoch
