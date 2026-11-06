"""Experimental first-two/soft-deadline age policy, never a production default.

Uses the production discovery/proof adapters and EpochStore. A provider becomes
usable only when all of its jobs settle successfully and at least one contains
finalized evidence. Independent providers need not agree. Verified observations
from partial groups may still contribute the minimum at return/deadline.

Early responses may admit one bounded background drain of already-started jobs.
The drain can only decrease the retained epoch, and never schedules a database
write after the original hard deadline. It starts no new discovery jobs.
"""

from collections import Counter
from concurrent.futures import FIRST_COMPLETED, wait
from copy import deepcopy
from dataclasses import dataclass
import sqlite3
import threading
import time
from uuid import uuid4

from eth_wallet_check.deadline import BoundedExecutor, shared_executor
from wallet_age_days.service import (
    AgeResult, ENABLED_CHAINS, WalletAgeError, _provider_group, validate,
)


@dataclass(frozen=True)
class BenchmarkAgeResult(AgeResult):
    benchmark_token: str = ""
    response_reason: str = ""


class FirstResponseAgeService:
    """Benchmark-compatible integer service with a soft and a hard deadline.

    snapshot(token) returns a copied trace; snapshot() returns all traces.
    trace_for(address) returns the most recent trace for a normalized address.
    drain() must be called before the runner's final cache/accuracy assessment.
    A five-second hard deadline is allowed only to compare the user's suggested
    timeout; no deployed service settings are changed by this class.
    """

    def __init__(self, sources, store, http, *, clock=time.time,
                 soft_deadline=1.0, hard_deadline=2.6, executor=None, refine=True,
                 background_executor=None, chains=ENABLED_CHAINS):
        if not 0 < hard_deadline <= 5 or not 0 <= soft_deadline <= hard_deadline:
            raise ValueError("Require 0 <= soft deadline <= hard deadline <= 5 seconds")
        self.sources, self.store, self.http = sources, store, http
        self.clock = clock
        self.soft_deadline, self.hard_deadline = soft_deadline, hard_deadline
        self.budget = hard_deadline
        self.executor = executor or shared_executor()
        self.refine = refine
        self.chains = tuple(sorted(chains))
        self.namespace = "evm:" + ",".join(str(chain_id) for chain_id, _ in self.chains) + ":v1"
        self.coverage = ",".join(name for _, name in self.chains)
        self._background = background_executor or BoundedExecutor(max_workers=2, max_pending=0)
        self._owns_background = background_executor is None
        self._background_futures = set()
        self._records, self._latest = {}, {}
        self._lock = threading.Lock()

    def snapshot(self, token=None):
        with self._lock:
            return deepcopy(list(self._records.values()) if token is None else self._records[token])

    def trace_for(self, address):
        with self._lock:
            token = self._latest.get(address.lower()) if isinstance(address, str) else None
            return None if token is None else deepcopy(self._records[token])

    def _patch(self, token, **values):
        with self._lock:
            self._records[token].update(values)

    def _trace(self, address, started):
        token = uuid4().hex
        record = {
            "token": token, "address": address, "started_monotonic": started,
            "soft_seconds": self.soft_deadline, "hard_seconds": self.hard_deadline,
            "initial_epoch": None, "final_epoch": None, "cached_final_epoch": None,
            "response_seconds": None, "response_reason": None, "cache": "miss",
            "positive_sources_at_response": 0, "usable_provider_groups_at_response": 0,
            "completed_groups_at_response": 0, "background_updates": 0,
            "late_smaller_epoch": False, "refinement_admitted": False,
            "cache_write_skipped_at_deadline": False, "finished": False,
            "observations": [],
        }
        with self._lock:
            self._records[token] = record
            self._latest[address] = token
        return token

    def _run(self, name, fn, started, deadline, cancelled):
        try:
            with self.http.deadline_at(deadline, cancelled=cancelled):
                value = fn()
            return name, value, None, time.monotonic() - started
        except Exception as exc:
            # Never retain exception text, which may contain provider secrets.
            return name, None, type(exc).__name__, time.monotonic() - started

    @staticmethod
    def _epochs(groups):
        return [epoch for group in groups.values() for epoch in group["epochs"]]

    @staticmethod
    def _usable(groups):
        return sum(not row["remaining"] and not row["failed"] and bool(row["epochs"])
                   for row in groups.values())

    def _consume(self, token, future, groups, failed, hard_seconds):
        name, value, error_type, seconds = future.result()
        group = groups[_provider_group(name)]
        group["remaining"] -= 1
        row = {"job": name, "provider": _provider_group(name), "seconds": seconds,
               "epoch": None, "finalized": False}
        if seconds > hard_seconds:
            row["state"] = "after_deadline"
            group["failed"] = True
            failed[0] = True
        elif error_type:
            row.update(state="error", error_type=error_type)
            group["failed"] = True
            failed[0] = True
        elif value is None:
            row["state"] = "empty"
        else:
            epoch = getattr(value, "epoch", None)
            finalized = getattr(value, "finalized", None) is True
            if type(epoch) is not int or not 0 < epoch <= int(self.clock()):
                row.update(state="error", error_type="InvalidObservation")
                group["failed"] = True
                failed[0] = True
            elif not finalized:
                row.update(state="unverified", epoch=epoch)
                group["failed"] = True
                failed[0] = True
                failed[0] = True
            else:
                row.update(state="verified", epoch=epoch, finalized=True)
                group["epochs"].append(epoch)
        with self._lock:
            self._records[token]["observations"].append(row)

    def _persist(self, address, epoch, deadline):
        if time.monotonic() >= deadline:
            return epoch, "bypass", False
        try:
            stored = self.store.put_min(self.namespace, address, epoch)
            cached = self.store.peek(self.namespace, address) if stored else None
            return min(epoch, cached or epoch), "miss" if stored else "bypass", stored
        except sqlite3.Error:
            return epoch, "bypass", False

    def _finish(self, token, address, groups, pending, cancelled, started):
        cancelled.set()
        for future in pending:
            future.cancel()
        epochs = self._epochs(groups)
        try:
            cached = self.store.peek(self.namespace, address)
        except sqlite3.Error:
            cached = None
        with self._lock:
            initial = self._records[token]["initial_epoch"]
        if initial is not None:
            epochs.append(initial)
        self._patch(token, final_epoch=min(epochs) if epochs else None,
                    cached_final_epoch=cached, finished=True,
                    finished_seconds=time.monotonic() - started,
                    unfinished_jobs_at_deadline=len(pending))

    def _refine(self, token, address, groups, pending, failed, cancelled, started, deadline, initial):
        minimum = initial
        try:
            while pending and time.monotonic() < deadline:
                done, _ = wait(pending, timeout=max(0, deadline - time.monotonic()),
                               return_when=FIRST_COMPLETED)
                if not done:
                    break
                for future in done:
                    pending.pop(future)
                    self._consume(token, future, groups, failed, self.hard_deadline)
                epochs = self._epochs(groups)
                candidate = min(epochs) if epochs else minimum
                if candidate < minimum:
                    self._patch(token, late_smaller_epoch=True)
                    if time.monotonic() < deadline:
                        persisted, _state, written = self._persist(address, candidate, deadline)
                        if written:
                            with self._lock:
                                self._records[token]["background_updates"] += 1
                        minimum = min(candidate, persisted)
                    else:
                        self._patch(token, cache_write_skipped_at_deadline=True)
        finally:
            self._finish(token, address, groups, pending, cancelled, started)

    def _admit_refinement(self, token, address, groups, pending, failed, cancelled,
                          started, deadline, epoch):
        if not self.refine or not pending or time.monotonic() >= deadline:
            self._finish(token, address, groups, pending, cancelled, started)
            return
        future = self._background.submit(self._refine, token, address, groups, pending,
                                         failed, cancelled, started, deadline, epoch)
        if future is None:
            self._finish(token, address, groups, pending, cancelled, started)
            return
        self._patch(token, refinement_admitted=True)
        with self._lock:
            self._background_futures.add(future)
        def finished(item):
            with self._lock:
                self._background_futures.discard(item)
        future.add_done_callback(finished)

    def check(self, blockchain, address):
        started = time.monotonic()
        address = validate(blockchain, address)
        token = self._trace(address, started)
        deadline = started + self.hard_deadline
        cache_state = "miss"
        try:
            cached = self.store.get(self.namespace, address)
        except sqlite3.Error:
            cached, cache_state = None, "bypass"
        if cached is not None:
            self._patch(token, initial_epoch=cached, final_epoch=cached,
                        cached_final_epoch=cached, cache="hit", response_reason="cache_hit",
                        response_seconds=time.monotonic()-started, finished=True)
            return BenchmarkAgeResult(max(0, (int(self.clock())-cached)//86400), cached,
                                      "hit", True, "cached-canonical", 0, token, "cache_hit")

        jobs = list(self.sources.jobs(address))
        groups = {name: {"remaining": count, "failed": False, "epochs": []}
                  for name, count in Counter(_provider_group(name) for name, _ in jobs).items()}
        pending, failed, cancelled = {}, [False], threading.Event()
        for name, fn in jobs:
            future = self.executor.submit(self._run, name, fn, started, deadline, cancelled)
            if future is None:
                group = groups[_provider_group(name)]
                group["remaining"] -= 1
                group["failed"] = failed[0] = True
            else:
                pending[future] = name
        reason = "all_finished"
        while pending:
            now = time.monotonic()
            usable = self._usable(groups)
            if usable >= 2:
                reason = "two_usable_providers"
                break
            if usable and now - started >= self.soft_deadline:
                reason = "one_provider_soft_deadline"
                break
            if now >= deadline:
                reason = "hard_deadline"
                break
            wake = deadline
            if usable and now < started + self.soft_deadline:
                wake = min(wake, started + self.soft_deadline)
            done, _ = wait(pending, timeout=max(0, wake-now), return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                self._consume(token, future, groups, failed, self.hard_deadline)
        epochs = self._epochs(groups)
        positive_sources = sum(bool(group["epochs"]) for group in groups.values())
        self._patch(token, positive_sources_at_response=positive_sources,
                    usable_provider_groups_at_response=self._usable(groups),
                    completed_groups_at_response=sum(not g["remaining"] for g in groups.values()),
                    response_reason=reason)
        if not epochs:
            all_empty = bool(jobs) and not pending and not failed[0]
            self._patch(token, response_seconds=time.monotonic()-started,
                        cache=cache_state, status=404 if all_empty else 503)
            self._finish(token, address, groups, pending, cancelled, started)
            raise WalletAgeError(404 if all_empty else 503, "not_found" if all_empty else "unknown")
        epoch = min(epochs)
        epoch, persisted_state, _written = self._persist(address, epoch, deadline)
        if persisted_state == "bypass":
            cache_state = "bypass"
        evidence = "multiple-sources" if positive_sources >= 2 else "single-source"
        self._patch(token, initial_epoch=epoch, cache=cache_state, status=200,
                    response_seconds=time.monotonic()-started)
        result = BenchmarkAgeResult(max(0, (int(self.clock())-epoch)//86400), epoch,
                                    cache_state, True, evidence, positive_sources, token, reason)
        self._admit_refinement(token, address, groups, pending, failed, cancelled,
                               started, deadline, epoch)
        return result

    def walletAgeDays(self, blockchain, address):
        return self.check(blockchain, address).days

    def drain(self, timeout=None):
        with self._lock:
            pending = set(self._background_futures)
        if not pending:
            return True
        _done, unfinished = wait(pending, timeout=timeout)
        return not unfinished

    def close(self):
        self.drain()
        if self._owns_background:
            self._background.shutdown(wait=True, cancel_futures=True)
        self.store.close()
