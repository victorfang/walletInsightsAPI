"""One job: retain verified first observations and return completed days."""

import asyncio
from dataclasses import dataclass
import re
import sqlite3
import time


class AgeError(Exception):
    def __init__(self, status, message, coverage=None):
        self.status, self.message, self.coverage = status, message, coverage
        super().__init__(message)


@dataclass(frozen=True)
class Result:
    days: int
    epoch: int
    cache: str
    finalized: bool = True
    evidence: str = "single-source"
    positive_sources: int = 1
    coverage: str = "ethereum"


class Service:
    def __init__(self, feeds, cache, config, clock=time.time):
        self.feeds, self.cache, self.config, self.clock = feeds, cache, config, clock
        settings = config["wallet_age"]
        self.hard = settings.get("deadline_ms", 2600) / 1000
        self.soft = settings.get("soft_deadline_ms", 1000) / 1000
        self.limit = settings.get("max_inflight", 32)
        if not 0 <= self.soft <= self.hard <= 2.6 or self.hard <= 0 or self.limit < 1:
            raise ValueError("Invalid age time budget or concurrency limit")
        self.active, self.tasks, self.drainers = 0, set(), set()
        self.coverage = ",".join(row.get("name", name) for name, row in config["chains"].items())

    def _chains(self, blockchain, address):
        if not isinstance(blockchain, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", blockchain):
            raise AgeError(400, "invalid request")
        selector = blockchain.lower()
        chains = self.config["chains"]
        names = [name for name, row in chains.items()
                 if selector in [name, *row.get("aliases", [])]]
        for name, scope in self.config.get("scopes", {}).items():
            if selector in [name, *scope.get("aliases", [])]:
                names = scope["chains"]
        if not names:
            raise AgeError(501, "coming soon contact info@anchain.ai")
        if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", address, re.ASCII):
            raise AgeError(400, "invalid")
        return [chains[name] for name in names], address.lower()

    @staticmethod
    def _key(chain, address):
        return str(chain["chain_id"]), address, chain.get("coverage_version", "history-v1")

    async def _run(self, chain, address, provider, job, deadline):
        epoch = None
        try:
            async with asyncio.timeout_at(deadline):
                observation = await job
                epoch, complete = observation.epoch, observation.complete is True
                if epoch is not None and (type(epoch) is not int or not 0 < epoch <= int(self.clock())):
                    return provider, None, False, False
                bypass = False
                if epoch is not None:
                    try:
                        epoch = await self.cache.put(*self._key(chain, address), epoch, deadline=deadline)
                    except (sqlite3.Error, TimeoutError):
                        bypass = True
                return provider, epoch, complete, bypass
        except TimeoutError:
            return provider, epoch, False, epoch is not None
        except Exception:
            # Exception strings can contain authenticated URLs. Never retain them.
            return provider, None, False, False

    async def _drain(self, pending):
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            self.active -= 1

    async def check(self, blockchain, address):
        loop = asyncio.get_running_loop()
        started = loop.time()
        chains, address = self._chains(blockchain, address)
        coverage = ",".join(row.get("name", str(row["chain_id"])) for row in chains)
        epochs, missing, bypass = [], [], False
        for chain in chains:
            try:
                async with asyncio.timeout_at(started + self.hard):
                    cached = await self.cache.get(*self._key(chain, address))
            except sqlite3.Error:
                cached, bypass = None, True
            except TimeoutError:
                raise AgeError(503, "unknown", coverage) from None
            if type(cached) is int and 0 < cached <= int(self.clock()):
                epochs.append(cached)
            else:
                missing.append(chain)
        if not missing:
            epoch = min(epochs)
            return Result(max(0, (int(self.clock()) - epoch) // 86400), epoch,
                          "hit", evidence="cached-canonical", positive_sources=0, coverage=coverage)
        if self.active >= self.limit or loop.time() >= started + self.hard:
            raise AgeError(503, "unknown", coverage)
        self.active += 1
        pending, rows, unqueryable = set(), [], False
        try:
            for chain in missing:
                jobs = list(self.feeds.jobs(chain, address))
                unqueryable |= not jobs
                for provider, job in jobs:
                    task = asyncio.create_task(self._run(chain, address, provider, job, started + self.hard))
                    pending.add(task)
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
            if not pending:
                raise AgeError(503, "unknown", coverage)
            while pending:
                usable = {provider for provider, epoch, complete, _ in rows if complete and epoch is not None}
                now = loop.time()
                if len(usable) >= 2 or (usable and now >= started + self.soft) or now >= started + self.hard:
                    break
                until = started + (self.soft if usable and now < started + self.soft else self.hard)
                done, pending = await asyncio.wait(pending, timeout=max(0, until - now),
                                                  return_when=asyncio.FIRST_COMPLETED)
                rows.extend(task.result() for task in done)
            epochs.extend(epoch for _, epoch, _, _ in rows if epoch is not None)
            if not epochs:
                empty = rows and not pending and not unqueryable and all(complete for _, _, complete, _ in rows)
                raise AgeError(404 if empty else 503, "not_found" if empty else "unknown", coverage)
            epoch = min(epochs)
            sources = len({provider for provider, value, _, _ in rows if value is not None})
            evidence = "multiple-sources" if sources >= 2 else "single-source" if sources else "cached-canonical"
            return Result(max(0, (int(self.clock()) - epoch) // 86400), epoch,
                          "bypass" if bypass or any(row[3] for row in rows) else "miss",
                          evidence=evidence, positive_sources=sources, coverage=coverage)
        finally:
            if pending:
                drainer = asyncio.create_task(self._drain(pending))
                self.drainers.add(drainer)
                drainer.add_done_callback(self.drainers.discard)
            else:
                self.active -= 1

    async def close(self):
        tasks = list(self.tasks | self.drainers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.cache.close()
