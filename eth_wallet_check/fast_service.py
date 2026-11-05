"""Deadline-bounded checks with a permanent store of finalized observations."""

from concurrent.futures import FIRST_COMPLETED, wait
from contextlib import nullcontext
import sqlite3
import time

from .api import _unix_seconds
from .deadline import REQUEST_BUDGET_SECONDS, shared_executor
from .service import LIMITATIONS, utc_timestamp, valid_address, wei_to_eth


class FastWalletChecker:
    def __init__(self, history, rpc, cache, age_store, http, clock=time.time,
                 budget=REQUEST_BUDGET_SECONDS, executor=None):
        self.history, self.rpc, self.cache = history, rpc, cache
        self.age_store, self.http, self.clock = age_store, http, clock
        self.budget = budget
        self.executor = executor or shared_executor()

    def _run(self, deadline, fn, *args):
        context = self.http.deadline_at(deadline) if hasattr(self.http, "deadline_at") else nullcontext()
        with context:
            return fn(*args)

    def _attach_age(self, result, saved, hit=False):
        result["age_cache"] = {"policy": "permanent", "hit": bool(hit and saved),
                               "stored": bool(saved), "expires_at": None}
        if saved:
            result["first_seen"] = {
                "timestamp": utc_timestamp(saved["first_seen_timestamp"]),
                "epoch": saved["first_seen_timestamp"], "block_number": saved["block_number"],
                "block_hash": saved.get("block_hash"),
                "transaction_hash": saved["transaction_hash"], "source": saved["source"],
            }
            result["first_seen_scope"] = saved["scope"]
            result["first_seen_finalized"] = saved["finalized"]
            result["first_seen_complete"] = saved["complete"]
            result.update(status="seen", has_appeared=True, is_new_wallet=False)
        first = result.get("first_seen")
        result["age_status"] = ("unknown" if not first else "provisional" if not
                                result.get("first_seen_finalized") else
                                "exact" if result.get("first_seen_complete") else "lower_bound")
        if saved and not saved["complete"]:
            warning = "Age is a lower bound from finalized observed activity; complete earliest history is not proven."
            if warning not in result.setdefault("warnings", []):
                result["warnings"].append(warning)
        return result

    def check(self, address, details=False):
        start = time.monotonic()
        deadline = start + self.budget
        if not valid_address(address):
            return {"address": address, "valid_format": False, "status": "invalid_address",
                    "has_appeared": None, "is_new_wallet": None,
                    "error": "Expected 0x followed by exactly 40 hexadecimal characters."}
        address = address.lower()
        key = f"v2:eth:mainnet:{address}:details={int(details)}"
        warnings = []
        try:
            saved = self.age_store.get("eth", "mainnet", address)
            # Never let an old provisional record act as permanent evidence.
            if saved and not saved["finalized"]:
                saved = None
        except sqlite3.Error:
            saved = None
            warnings.append("Permanent age store temporarily unavailable.")
        try:
            cached = self.cache.get(key)
        except sqlite3.Error:
            cached = None
            warnings.append("24-hour cache temporarily unavailable.")
        if cached is not None:
            cached["cache"]["hit"] = True
            cached["warnings"].extend(warnings)
            return self._attach_age(cached, saved, hit=True)

        now = self.clock()
        result = {
            "address": address, "chain": "ethereum-mainnet", "chain_id": 1,
            "valid_format": True, "status": "unknown", "has_appeared": None,
            "is_new_wallet": None, "checked_at": utc_timestamp(now),
            "history_source": None, "rpc_source": None, "history_scope": [],
            "evidence": [], "warnings": warnings, "limitations": LIMITATIONS,
            "source_attempts": {"history": [], "rpc": []},
            "cache": {"hit": False, "stored": False, "ttl_seconds": self.cache.ttl_seconds,
                      "expires_at": None},
            "deadline": {"budget_seconds": self.budget, "exhausted": False},
        }
        if saved and not details:
            result["history_source"] = saved["source"]
            result["history_scope"] = saved["scope"]
            # A finalized positive observation establishes appearance forever.
            # No provider calls and no TTL writes are necessary for age-only.
            return self._attach_age(result, saved, hit=True)

        # Reserve time for local persistence and JSON formatting. Workers never
        # write caches, so late results cannot mutate a response after return.
        upstream_deadline = max(start, deadline - min(0.15, self.budget / 10))
        pending, completed = {}, {}
        jobs = [("rpc", self.rpc.inspect, (address,))]
        names = ("blockcypher",) if saved else self.history.sources
        for name in names:
            jobs.append((name, self.history.inspect_source, (name, address, details)))
        for name, fn, args in jobs:
            if time.monotonic() >= upstream_deadline:
                break
            future = self.executor.submit(self._run, upstream_deadline, fn, *args)
            if future is not None:
                pending[future] = name
            else:
                warnings.append(f"Source capacity busy: {name}.")
        while pending:
            remaining = upstream_deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                name = pending.pop(future)
                try:
                    completed[name] = future.result()
                except Exception:
                    completed[name] = {"source": None, "attempts": [
                        {"source": name, "status": "error", "error": "Source job failed"}]}
            # Once all sources finish this loop exits without spending the budget.
        if pending:
            result["deadline"]["exhausted"] = True
            warnings.append("Request deadline reached; unavailable details remain null.")
            for future, name in pending.items():
                future.cancel()
                result["source_attempts"]["rpc" if name == "rpc" else "history"].append(
                    {"source": name, "status": "deadline"})

        rpc = completed.pop("rpc", {})
        available = rpc.get("source") is not None
        rpc_seen = available and (int(rpc.get("balance_wei") or 0) > 0 or
                                  int(rpc.get("nonce") or 0) > 0 or rpc.get("code_present"))
        histories = list(completed.values())
        seen = True if saved or rpc_seen or any(h.get("seen") is True for h in histories) else (
            False if available and any(h.get("seen") is False for h in histories) else None)
        result.update(status="seen" if seen is True else "no_observed_activity" if seen is False else "unknown",
                      has_appeared=seen, is_new_wallet=None if seen is None else not seen,
                      rpc_source=rpc.get("source"))
        result["evidence"].extend(rpc.get("evidence", []))
        result["source_attempts"]["rpc"].extend(rpc.get("attempts", []))
        result["history_source"] = [h["source"] for h in histories if h.get("source")]
        result["history_scope"] = sorted({s for h in histories for s in h.get("scope", [])})
        for history in histories:
            result["evidence"].extend(history.get("evidence", []))
            warnings.extend(history.get("warnings", []))
            result["source_attempts"]["history"].extend(history.get("attempts", []))
        if not available:
            warnings.append("Current RPC state unavailable within request budget.")
        if seen is None:
            warnings.append("Missing history is not evidence of an unused address.")

        candidates = [h for h in histories if h.get("first_seen") and
                      _unix_seconds(h["first_seen"].get("timestamp")) is not None]
        finalized = [h for h in candidates if h.get("first_seen_finalized")]
        selected = min(finalized or candidates,
                       key=lambda h: _unix_seconds(h["first_seen"]["timestamp"]), default=None)
        old_hit = bool(saved)
        if selected:
            result.update({k: selected.get(k) for k in ("first_seen", "first_seen_scope",
                          "first_seen_finalized", "first_seen_complete")})
            if selected.get("first_seen_finalized"):
                first = selected["first_seen"]
                try:
                    saved = self.age_store.observe("eth", "mainnet", address,
                        _unix_seconds(first["timestamp"]), block_number=first.get("block_number"),
                        block_hash=first.get("block_hash"), transaction_hash=first.get("transaction_hash"),
                        source=first.get("source") or selected.get("source"),
                        scope=selected.get("first_seen_scope", []),
                        complete=bool(selected.get("first_seen_complete")), finalized=True)
                except (sqlite3.Error, ValueError):
                    warnings.append("Finalized age could not be saved in permanent store.")
        if details:
            fallback = next((h for h in histories if h.get("balance_wei") is not None), {})
            count = next((h for h in histories if h.get("transaction_count") is not None), {})
            balance = rpc.get("balance_wei") if available else fallback.get("balance_wei")
            result.update(balance_wei=balance, balance_eth=wei_to_eth(balance) if balance is not None else None,
                          nonce=rpc.get("nonce"), code_present=rpc.get("code_present"),
                          state_block_number=rpc.get("block_number"),
                          transaction_count=count.get("transaction_count"),
                          transaction_count_scope=count.get("count_scope"))
        self._attach_age(result, saved, hit=old_hit)
        if seen is not None and time.monotonic() < deadline:
            result["cache"].update(stored=True, expires_at=utc_timestamp(now + self.cache.ttl_seconds))
            try:
                self.cache.set(key, result)
            except sqlite3.Error:
                result["cache"].update(stored=False, expires_at=None)
                warnings.append("24-hour cache write unavailable.")
        return result

    def close(self):
        self.age_store.close()
