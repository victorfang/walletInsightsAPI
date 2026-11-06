"""Only discover an epoch, persist it, and compute elapsed whole days."""

from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import dataclass
from collections import Counter
import re
import sqlite3
import threading
import time

from eth_wallet_check.deadline import REQUEST_BUDGET_SECONDS, shared_executor


ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}", re.ASCII)
BLOCKCHAIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}", re.ASCII)
EVM_ALIASES = frozenset({
    "eth", "ethereum", "eth-mainnet", "evm", "polygon", "matic",
    "polygon-mainnet", "matic-mainnet", "base", "arbitrum", "arb",
    "optimism", "op", "bsc", "bnb", "avalanche", "avax",
})
# Adding a chain changes this identity. An ETH-only cache entry must never
# silently stand in for a search that also covers another chain.
ENABLED_CHAINS = ((1, "ethereum"),)


class WalletAgeError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def validate(blockchain, address):
    if not isinstance(blockchain, str) or not BLOCKCHAIN_RE.fullmatch(blockchain):
        raise WalletAgeError(400, "invalid request")
    if blockchain.lower() not in EVM_ALIASES:
        raise WalletAgeError(501, "coming soon contact info@anchain.ai")
    if not isinstance(address, str) or not ADDRESS_RE.fullmatch(address):
        raise WalletAgeError(400, "invalid")
    return address.lower()


@dataclass(frozen=True)
class AgeResult:
    days: int
    epoch: int
    cache: str
    finalized: bool
    evidence: str = "single-source"
    positive_sources: int = 1


def _provider_group(job_name):
    """Directions/lookup kinds from one company are one history provider."""
    for suffix in ("_from", "_to", "_normal", "_creation"):
        if job_name.endswith(suffix):
            return job_name[:-len(suffix)]
    return job_name


class WalletAgeService:
    def __init__(self, sources, store, http, *, clock=time.time,
                 budget=REQUEST_BUDGET_SECONDS, executor=None, chains=ENABLED_CHAINS):
        if not 0 < budget <= REQUEST_BUDGET_SECONDS:
            raise ValueError("Request budget must be positive and at most 2.6 seconds")
        self.sources, self.store, self.http = sources, store, http
        self.clock, self.budget = clock, budget
        self.executor = executor or shared_executor()
        # An adapter set must explicitly cover every chain in this set. The
        # initial factory only configures Ethereum; future factories own fanout.
        self.chains = tuple(sorted(chains))
        self.namespace = "evm:" + ",".join(str(chain_id) for chain_id, _ in self.chains) + ":v1"
        self.coverage = ",".join(name for _, name in self.chains)

    def _run(self, deadline, fn, cancelled):
        with self.http.deadline_at(deadline, cancelled=cancelled):
            return fn()

    def _result(self, epoch, cache, finalized, evidence="single-source", positive_sources=1):
        return AgeResult(max(0, (int(self.clock()) - epoch) // 86400), epoch, cache,
                         finalized, evidence, positive_sources)

    def check(self, blockchain, address):
        start = time.monotonic()
        address = validate(blockchain, address)
        cache_state = "miss"
        try:
            epoch = self.store.get(self.namespace, address)
        except sqlite3.Error:
            epoch, cache_state = None, "bypass"
        if epoch is not None:
            return self._result(epoch, "hit", True, "cached-canonical", 0)

        # Leave headroom for a 50ms SQLite lock and response serialization. The
        # caller never waits for abandoned jobs to exit. Workers do not write.
        deadline = start + self.budget - min(0.15, self.budget / 10)
        pending, observations = {}, []
        cancelled = threading.Event()
        jobs = list(self.sources.jobs(address))
        groups = {group: {"remaining": count, "failed": False, "epochs": []}
                  for group, count in Counter(_provider_group(name) for name, _fn in jobs).items()}
        failed, finished, total = False, 0, 0
        for name, fn in jobs:
            total += 1
            if time.monotonic() >= deadline:
                failed = True
                groups[_provider_group(name)]["failed"] = True
                continue
            future = self.executor.submit(self._run, deadline, fn, cancelled)
            if future is None:
                failed = True
                groups[_provider_group(name)]["failed"] = True
            else:
                pending[future] = name
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                group = groups[_provider_group(pending.pop(future))]
                group["remaining"] -= 1
                try:
                    observation = future.result()
                    if observation is not None:
                        epoch = observation.epoch
                        if type(epoch) is not int or not 0 < epoch <= int(self.clock()):
                            raise ValueError("Invalid observation epoch")
                        observations.append(observation)
                        if observation.finalized is True:
                            group["epochs"].append(epoch)
                        else:
                            group["failed"] = True
                    finished += 1
                except Exception:
                    # Do not put configured URLs, keys or exception text in the
                    # response. Provider failure never means age zero.
                    failed = True
                    group["failed"] = True
            # One direction alone is not Alchemy's oldest observed history.
            # Count a provider only after every job for it has completed, and
            # never count empty/error/provisional responses as positive votes.
            votes = Counter(min(group["epochs"]) for group in groups.values()
                            if not group["remaining"] and not group["failed"] and group["epochs"])
            verified_epochs = [row.epoch for row in observations if row.finalized is True]
            # Normal-transaction indexes can agree before Alchemy's older NFT
            # or incoming transfer arrives. Its configured full group must
            # settle before an early return can truncate that coverage.
            alchemy_settled = "alchemy" not in groups or not groups["alchemy"]["remaining"]
            if alchemy_settled and verified_epochs and votes[min(verified_epochs)] >= 2:
                break
        # Running I/O cannot be forcibly interrupted by urllib, but cancellation
        # prevents a losing job from starting additional HTTP proof calls.
        cancelled.set()
        if pending:
            failed = True
            for future in pending:
                future.cancel()
        if not observations:
            if not failed and finished == total and total:
                raise WalletAgeError(404, "not_found")
            raise WalletAgeError(503, "unknown")

        # A bare integer cannot explain an unverified candidate. Require at
        # least one finalized canonical event before reporting an age.
        verified = [row for row in observations if row.finalized is True]
        if not verified:
            raise WalletAgeError(503, "unknown")
        chosen = min(verified, key=lambda row: row.epoch)
        finalized = True
        epoch = chosen.epoch
        positive_sources = sum(bool(group["epochs"]) for group in groups.values())
        votes = Counter(min(group["epochs"]) for group in groups.values()
                        if not group["remaining"] and not group["failed"] and group["epochs"])
        alchemy_settled = "alchemy" not in groups or not groups["alchemy"]["remaining"]
        evidence = ("corroborated" if alchemy_settled and votes[epoch] >= 2 else
                    "multiple-sources" if positive_sources >= 2 else "single-source")
        if finalized:
            try:
                stored = self.store.put_min(self.namespace, address, epoch)
                if stored:
                    # Another request/process may have discovered an earlier
                    # event while this request was waiting for its providers.
                    cached_epoch = self.store.get(self.namespace, address) or epoch
                    if cached_epoch < epoch:
                        epoch, evidence, positive_sources = cached_epoch, "cached-canonical", 0
                else:
                    cache_state = "bypass"
            except sqlite3.Error:
                cache_state = "bypass"
        return self._result(epoch, cache_state, finalized, evidence, positive_sources)

    def walletAgeDays(self, blockchain, address):
        return self.check(blockchain, address).days

    def close(self):
        self.store.close()
