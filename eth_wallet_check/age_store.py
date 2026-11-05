"""Durable, bounded-memory first-observation records, separate from TTL cache.

A saved observation does not prove globally complete history. Callers must use
the complete/finalized flags when deciding whether it can replace a new lookup.
Only positive epochs are stored; absence, timeouts and null ages are not records.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterator
from uuid import uuid4


_COLUMNS = ("proto", "network", "address", "first_seen_timestamp", "block_number", "block_hash",
            "transaction_hash", "source", "scope_json", "complete", "finalized", "updated_at")
_SELECT = "SELECT " + ", ".join(_COLUMNS) + " FROM address_first_seen WHERE proto = ? AND network = ? AND address = ?"
_INSERT = (
    "INSERT INTO address_first_seen (" + ", ".join(_COLUMNS) + ") "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(proto, network, address) DO UPDATE SET "
    "first_seen_timestamp=excluded.first_seen_timestamp, block_number=excluded.block_number, block_hash=excluded.block_hash, "
    "transaction_hash=excluded.transaction_hash, source=excluded.source, scope_json=excluded.scope_json, "
    "complete=excluded.complete, finalized=excluded.finalized, updated_at=excluded.updated_at"
)


def _key(proto, network, address):
    for name, value, limit in (("proto", proto, 64), ("network", network, 64), ("address", address, 256)):
        if not isinstance(value, str) or not value or len(value) > limit:
            raise ValueError(f"{name} must be a nonempty string of at most {limit} characters")
    return proto, network, address


def _decode(row):
    if row is None:
        return None
    result = dict(zip(_COLUMNS, row))
    result["scope"] = json.loads(result.pop("scope_json"))
    result["complete"] = bool(result["complete"])
    result["finalized"] = bool(result["finalized"])
    result["basis"] = "first_observed_activity"
    return result


def _same_block_or_event(left, right):
    if left["block_hash"] is not None and right["block_hash"] is not None and left["block_hash"] != right["block_hash"]:
        return False
    return (
        left["block_hash"] is not None and left["block_hash"] == right["block_hash"]
    ) or (
        left["transaction_hash"] is not None and left["transaction_hash"] == right["transaction_hash"]
    )


class AgeStore:
    """Persistent minimum observed epoch, keyed by protocol/network/address.

    Keys preserve case exactly. Each chain adapter owns canonicalization. No
    dictionary of the full store is loaded into RAM; SQLite's connection cache is
    capped at approximately 4 MiB. Writes wait at most 50 ms for a database lock.
    Production callers must persist only finalized canonical observations; the
    store permits provisional records for staging/tests but never upgrades them
    to finalized merely because a later observation is finalized.
    """

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._uri = str(path) == ":memory:"
        self._keeper = None
        if self._uri:
            self._path = f"file:address-age-{uuid4().hex}?mode=memory&cache=shared"
            self._keeper = sqlite3.connect(self._path, timeout=0.05, uri=True, check_same_thread=False)
        else:
            file_path = Path(path).expanduser()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(file_path)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS address_first_seen ("
                "proto TEXT NOT NULL, network TEXT NOT NULL, address TEXT NOT NULL, "
                "first_seen_timestamp INTEGER NOT NULL CHECK(first_seen_timestamp > 0), "
                "block_number INTEGER CHECK(block_number IS NULL OR block_number >= 0), block_hash TEXT, "
                "transaction_hash TEXT, source TEXT, scope_json TEXT NOT NULL, "
                "complete INTEGER NOT NULL CHECK(complete IN (0,1)), "
                "finalized INTEGER NOT NULL CHECK(finalized IN (0,1)), updated_at INTEGER NOT NULL, "
                "PRIMARY KEY(proto, network, address)) WITHOUT ROWID"
            )
            if "block_hash" not in {row[1] for row in db.execute("PRAGMA table_info(address_first_seen)")}:
                db.execute("ALTER TABLE address_first_seen ADD COLUMN block_hash TEXT")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=0.05, uri=self._uri)
        try:
            db.execute("PRAGMA busy_timeout=50")
            db.execute("PRAGMA cache_size=-4096")
            db.execute("PRAGMA mmap_size=0")
            with db:
                yield db
        finally:
            db.close()

    def get(self, proto, network, address):
        key = _key(proto, network, address)
        with self._connection() as db:
            return _decode(db.execute(_SELECT, key).fetchone())

    def observe(self, proto, network, address, first_seen_timestamp, block_number=None, block_hash=None,
                transaction_hash=None, source=None, scope=None, complete=False, finalized=False):
        key = _key(proto, network, address)
        now = int(self.clock())
        if type(first_seen_timestamp) is not int or first_seen_timestamp <= 0 or first_seen_timestamp > now:
            raise ValueError("first_seen_timestamp must be a positive epoch in seconds, not in the future")
        if block_number is not None and (type(block_number) is not int or block_number < 0):
            raise ValueError("block_number must be a nonnegative integer or null")
        for name, value, limit in (("block_hash", block_hash, 256), ("transaction_hash", transaction_hash, 256), ("source", source, 64)):
            if value is not None and (not isinstance(value, str) or not value or len(value) > limit or "://" in value):
                raise ValueError(f"{name} must be a short identifier or null")
        if type(complete) is not bool or type(finalized) is not bool:
            raise ValueError("complete and finalized must be booleans")
        scope = [] if scope is None else scope
        if not isinstance(scope, list) or len(scope) > 32 or any(
            not isinstance(item, str) or not item or len(item) > 64 for item in scope
        ):
            raise ValueError("scope must be a list of at most 32 short identifiers")
        candidate = {
            "proto": proto, "network": network, "address": address,
            "first_seen_timestamp": first_seen_timestamp, "block_number": block_number, "block_hash": block_hash,
            "transaction_hash": transaction_hash, "source": source,
            "scope": sorted(set(scope)), "complete": complete, "finalized": finalized,
            "updated_at": now,
        }
        with self._connection() as db:
            # Serialize the read/minimum/write across threads AND processes.
            db.execute("BEGIN IMMEDIATE")
            old = _decode(db.execute(_SELECT, key).fetchone())
            if old is not None:
                if old["first_seen_timestamp"] < first_seen_timestamp:
                    return old
                if old["first_seen_timestamp"] == first_seen_timestamp:
                    if _same_block_or_event(old, candidate):
                        # A complete claim's scope replaces partial scopes; merely
                        # unioning partial categories never establishes completeness.
                        scope = (candidate["scope"] if complete and not old["complete"] else
                                 old["scope"] if old["complete"] else
                                 sorted(set(old["scope"]) | set(candidate["scope"])))
                        primary, secondary = (candidate, old) if complete and not old["complete"] else (old, candidate)
                        candidate = {**primary, **{
                            name: primary[name] if primary[name] is not None else secondary[name]
                            for name in ("block_number", "block_hash", "transaction_hash", "source")
                        }, "scope": scope, "complete": old["complete"] or complete,
                            "finalized": old["finalized"] or finalized, "updated_at": now}
                    else:
                        quality = lambda row: (row["complete"] and row["finalized"], row["finalized"], row["complete"])
                        if quality(old) >= quality(candidate):
                            return old
            encoded = dict(candidate)
            encoded["scope_json"] = json.dumps(candidate["scope"], separators=(",", ":"))
            db.execute(_INSERT, tuple(int(encoded[name]) if name in {"complete", "finalized"} else encoded[name] for name in _COLUMNS))
        return {**candidate, "basis": "first_observed_activity"}

    def close(self):
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None
