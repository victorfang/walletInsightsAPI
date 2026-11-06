"""Persistent first-observation epochs with an independent, bounded SQLite LRU."""

import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from time import monotonic


class Cache:
    """One route's cache. Callers supply canonical chain/address/coverage keys."""

    def __init__(self, path, max_entries=1_000_000):
        if type(max_entries) is not int or not 0 < max_entries < 2**63:
            raise ValueError("max_entries must be a positive integer")
        self.max_entries = max_entries
        self._lock = threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=0.25, check_same_thread=False,
                                   isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS first_seen (
                chain TEXT NOT NULL, address TEXT NOT NULL,
                coverage_version TEXT NOT NULL, epoch INTEGER NOT NULL CHECK(epoch > 0),
                last_access INTEGER NOT NULL,
                PRIMARY KEY (chain, address, coverage_version)) WITHOUT ROWID""")
            db.execute("CREATE INDEX IF NOT EXISTS age_lru ON first_seen(last_access)")
            db.execute("""CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                access_counter INTEGER NOT NULL, row_count INTEGER NOT NULL)""")
            if db.execute("SELECT 1 FROM state WHERE id=1").fetchone() is None:
                counter, count = db.execute(
                    "SELECT COALESCE(MAX(last_access),0), COUNT(*) FROM first_seen"
                ).fetchone()
                db.execute("INSERT INTO state VALUES (1,?,?)", (counter, count))
            count = db.execute("SELECT row_count FROM state WHERE id=1").fetchone()[0]
            if count > max_entries:
                self._evict(db, count - max_entries)
                db.execute("UPDATE state SET row_count=? WHERE id=1", (max_entries,))

    @contextmanager
    def _transaction(self, deadline=None):
        with self._lock:
            if self._db is None:
                raise RuntimeError("cache is closed")
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("cache deadline elapsed")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                if deadline is not None and monotonic() >= deadline:
                    raise TimeoutError("cache deadline elapsed")
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    @staticmethod
    def _evict(db, count):
        db.execute("""DELETE FROM first_seen WHERE (chain,address,coverage_version) IN
            (SELECT chain,address,coverage_version FROM first_seen
             ORDER BY last_access LIMIT ?)""", (count,))

    @staticmethod
    def _key(chain, address, coverage_version):
        key = (chain, address, coverage_version)
        if any(not isinstance(value, str) or not value for value in key):
            raise ValueError("cache key components must be nonempty strings")
        return key

    def _access(self, key, epoch=None, deadline=None):
        with self._transaction(deadline) as db:
            row = db.execute("""SELECT epoch FROM first_seen
                WHERE chain=? AND address=? AND coverage_version=?""", key).fetchone()
            if row is None and epoch is None:
                return None
            counter, count = db.execute(
                "SELECT access_counter,row_count FROM state WHERE id=1"
            ).fetchone()
            counter += 1
            if row is None:
                excess = max(0, count - self.max_entries + 1)
                if excess:
                    self._evict(db, excess)
                db.execute("INSERT INTO first_seen VALUES (?,?,?,?,?)",
                           (*key, epoch, counter))
                count += 1 - excess
            else:
                epoch = row[0] if epoch is None else min(epoch, row[0])
                db.execute("""UPDATE first_seen SET epoch=?,last_access=?
                    WHERE chain=? AND address=? AND coverage_version=?""",
                           (epoch, counter, *key))
            db.execute("UPDATE state SET access_counter=?,row_count=? WHERE id=1",
                       (counter, count))
            return epoch

    async def get(self, chain, address, coverage_version):
        return await asyncio.to_thread(self._access, self._key(chain, address, coverage_version))

    async def put(self, chain, address, coverage_version, epoch, *, deadline=None):
        if type(epoch) is not int or not 0 < epoch < 2**63:
            raise ValueError("epoch must be a positive signed 64-bit integer")
        return await asyncio.to_thread(self._access, self._key(chain, address, coverage_version), epoch, deadline)

    def _close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    async def close(self):
        await asyncio.to_thread(self._close)
