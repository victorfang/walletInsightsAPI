"""One bounded request combines pinned state and independently sourced labels."""

from concurrent.futures import FIRST_COMPLETED, wait
import hashlib
import json
import re
import sqlite3
import time

from eth_wallet_check.deadline import REQUEST_BUDGET_SECONDS, shared_executor
from eth_wallet_check.service import utc_timestamp
from wallet_age_days.service import WalletAgeError


ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}", re.ASCII)
CHAIN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}", re.ASCII)


class WalletInsightsService:
    def __init__(self, rpc, labels, cache, http, assets, *, clock=time.time,
                 budget=REQUEST_BUDGET_SECONDS, executor=None):
        if not 0 < budget <= 2.6:
            raise ValueError("WalletInsights budget must be positive and at most 2.6 seconds")
        self.rpc, self.labels, self.cache, self.http = rpc, labels, cache, http
        self.assets, self.clock, self.budget = tuple(assets), clock, budget
        self.executor = executor or shared_executor()
        self.coverage = "ethereum"
        self._asset_id = hashlib.sha256(json.dumps(self.assets, sort_keys=True).encode()).hexdigest()[:16]

    def keys(self, address):
        address = address.lower()
        return {"snapshot": f"insights:v1:eth:1:{address}:{self._asset_id}",
                "riskLabels": f"insights-labels:v1:eth:1:{address}"}

    def cache_probe(self, address):
        # Diagnostics must not make an entry look recently used.
        return {name: self.cache.peek(key) is not None for name, key in self.keys(address).items()}

    def _run(self, deadline, fn, *args):
        with self.http.deadline_at(deadline):
            return fn(*args)

    def _meta(self, record, hit, ttl):
        return {"hit": hit, "ttlSeconds": ttl,
                "capturedAt": utc_timestamp(record["captured_at"]) if record else None,
                "expiresAt": utc_timestamp(record["expires_at"]) if record else None}

    def check(self, blockchain, address):
        started = time.monotonic()
        if not isinstance(blockchain, str) or not CHAIN.fullmatch(blockchain):
            raise WalletAgeError(400, "invalid request")
        if blockchain.lower() not in {"eth", "ethereum", "eth-mainnet"}:
            raise WalletAgeError(501, "coming soon contact info@anchain.ai")
        if not isinstance(address, str) or not ADDRESS.fullmatch(address):
            raise WalletAgeError(400, "invalid")
        address = address.lower()
        key, entries, hits = self.keys(address), {}, {}
        warnings = []
        for name in key:
            try:
                entries[name] = self.cache.get(key[name])
            except sqlite3.Error:
                entries[name] = None
                warnings.append(f"{name} cache unavailable.")
            hits[name] = entries[name] is not None
        snapshot = entries["snapshot"]["value"] if hits["snapshot"] else None
        risk = entries["riskLabels"]["value"] if hits["riskLabels"] else None
        limit = started + self.budget
        deadline = limit - min(0.15, self.budget / 10)
        pending = {}
        if risk is None:
            future = self.executor.submit(self._run, deadline, self.labels.inspect, address)
            if future is not None:
                pending[future] = "labels"
            else:
                warnings.append("Risk-label lookup capacity unavailable.")
        endpoints = list(self.rpc.endpoints) if snapshot is None else []
        next_endpoint, next_hedge = 0, started
        best_score = 0
        while pending or next_endpoint < len(endpoints):
            now = time.monotonic()
            if now >= deadline:
                break
            if next_endpoint < len(endpoints) and now >= next_hedge:
                name, url = endpoints[next_endpoint]
                future = self.executor.submit(self._run, deadline, self.rpc.inspect_endpoint,
                                              name, url, address, self.assets)
                next_endpoint += 1
                next_hedge = now + (0.6 if next_endpoint == 1 else 0.35)
                if future is not None:
                    pending[future] = "rpc"
                else:
                    warnings.append("RPC source capacity unavailable.")
                    next_hedge = now
            if not pending:
                continue
            timeout = max(0, deadline - time.monotonic())
            if next_endpoint < len(endpoints):
                timeout = min(timeout, max(0, next_hedge - time.monotonic()))
            done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            for future in done:
                kind = pending.pop(future, None)
                if kind is None:
                    continue
                try:
                    value = future.result()
                except Exception:
                    if kind == "rpc":
                        next_hedge = time.monotonic()
                    continue
                if kind == "labels":
                    risk = value
                    continue
                score = sum(value.get(field) is not None for field in
                            ("native_balance_wei", "account_nonce", "code_profile")) + sum(
                    token.get("status") == "available" for token in value.get("token_balances", []))
                if score > best_score:
                    snapshot, best_score = value, score
                if value.get("complete"):
                    snapshot = value
                    next_endpoint = len(endpoints)
                    # Don't wait for or keep racing redundant RPC providers.
                    for other, other_kind in list(pending.items()):
                        if other_kind == "rpc":
                            other.cancel()
                            pending.pop(other)
                else:
                    next_hedge = time.monotonic()
        if pending:
            warnings.append("Request deadline reached; missing components remain unavailable.")
            for future in pending:
                future.cancel()
        risk = risk or {"status": "unavailable", "source": "anchain", "labels": None,
                        "attribution": None, "checked_at": None, "error": "deadline_or_source_unavailable"}
        complete_snapshot = bool(snapshot and snapshot.get("complete"))
        for name, value, ttl, cacheable in (
            ("snapshot", snapshot, 15, complete_snapshot),
            ("riskLabels", risk, 300, risk.get("status") == "available")):
            if not hits[name] and cacheable and time.monotonic() < limit:
                try:
                    entries[name] = self.cache.put(key[name], value, ttl)
                except sqlite3.Error:
                    warnings.append(f"{name} cache write unavailable.")
        profile = snapshot.get("code_profile") if snapshot else None
        risk_public = {"status": risk.get("status", "unavailable"), "source": "anchain",
                       "labels": risk.get("labels"), "attribution": risk.get("attribution"),
                       "checkedAt": risk.get("checked_at"),
                       "providerVerdictTime": risk.get("provider_verdict_time")}
        if risk.get("error"):
            risk_public["error"] = risk["error"]
        snapshot_state = "complete" if complete_snapshot else "partial" if snapshot else "unavailable"
        status = "complete" if complete_snapshot and risk_public["status"] == "available" else (
            "partial" if snapshot or risk_public["status"] == "available" else "unavailable")
        tokens = snapshot.get("token_balances", []) if snapshot else [
            {**asset, "balance_raw": None, "status": "unavailable"} for asset in self.assets]
        body = {"status": status, "blockchain": "eth", "chainId": 1, "address": address,
                "checkedAt": utc_timestamp(self.clock()),
                "block": {**snapshot["block"], "source": snapshot["source"]} if snapshot else None,
                "balances": {"native": {"symbol": "ETH", "decimals": 18,
                    "balanceRaw": snapshot.get("native_balance_wei") if snapshot else None},
                    "tokens": [{"symbol": t["symbol"], "address": t["address"], "decimals": t["decimals"],
                                "balanceRaw": t["balance_raw"], "status": t["status"]} for t in tokens]},
                "accountNonce": snapshot.get("account_nonce") if snapshot else None,
                "codeProfile": {"kind": profile["kind"], "codeSize": profile["code_size"],
                    "delegationTarget": profile["delegation_target"]} if profile else None,
                "riskLabels": risk_public,
                "components": {"snapshot": snapshot_state, "riskLabels": risk_public["status"]},
                "cache": {"snapshot": self._meta(entries["snapshot"], hits["snapshot"], 15),
                          "riskLabels": self._meta(entries["riskLabels"], hits["riskLabels"], 300)},
                "warnings": warnings + (snapshot.get("warnings", []) if snapshot else [])}
        return (503 if status == "unavailable" else 200), body

    def close(self):
        self.cache.close()
