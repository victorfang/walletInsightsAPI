"""Persistent capacity-bounded LRU for short-lived WalletInsights components.

Successful reads and writes advance a database sequence without extending TTL.
Expired rows are reclaimed before live LRU rows. Age epochs use a separate DB.
"""

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


MAX_CAPACITY = 1_000_000


class InsightsCache:
    """One global row limit shared by snapshot and label entries in this DB.

    Persisted recency and atomic transactions make eviction consistent across
    threads, processes and restarts, even when wall-clock timestamps tie. Opening
    with a smaller capacity tightens the shared limit; reopening cannot enlarge
    it. Reads retain the original captured_at and expires_at values.
    """

    def __init__(self, path, clock=time.time, capacity=MAX_CAPACITY):
        if type(capacity) is not int or not 0 < capacity <= MAX_CAPACITY:
            raise ValueError(f"capacity must be an integer from 1 to {MAX_CAPACITY}")
        self.clock = clock
        self._uri = str(path) == ":memory:"
        self._keeper = None
        if self._uri:
            self._path = f"file:insights-{uuid4().hex}?mode=memory&cache=shared"
            self._keeper = sqlite3.connect(self._path, uri=True, check_same_thread=False,
                                           timeout=0.05)
        else:
            path = Path(path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
        try:
            with self._connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE IF NOT EXISTS wallet_insights_cache ("
                           "cache_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, "
                           "captured_at REAL NOT NULL, expires_at REAL NOT NULL, "
                           "last_access INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID")
                columns = {row[1] for row in db.execute("PRAGMA table_info(wallet_insights_cache)")}
                if "last_access" not in columns:
                    db.execute("ALTER TABLE wallet_insights_cache ADD COLUMN "
                               "last_access INTEGER NOT NULL DEFAULT 0")
                db.execute("CREATE INDEX IF NOT EXISTS insights_expiry ON wallet_insights_cache(expires_at)")
                db.execute("CREATE INDEX IF NOT EXISTS insights_lru ON wallet_insights_cache(last_access,cache_key)")
                db.execute("CREATE TABLE IF NOT EXISTS wallet_insights_cache_metadata ("
                           "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                           "row_count INTEGER NOT NULL CHECK(row_count>=0), "
                           "access_counter INTEGER NOT NULL CHECK(access_counter>=0), "
                           "capacity INTEGER NOT NULL CHECK(capacity>0 AND capacity<=1000000))")
                record = db.execute("SELECT row_count,access_counter,capacity "
                                    "FROM wallet_insights_cache_metadata WHERE singleton=1").fetchone()
                if record is None:
                    count, highest = db.execute("SELECT COUNT(*),COALESCE(MAX(last_access),0) "
                                                "FROM wallet_insights_cache").fetchone()
                    db.execute("INSERT INTO wallet_insights_cache_metadata VALUES (1,?,?,?)",
                               (count, highest, capacity))
                    persisted_capacity = capacity
                else:
                    persisted_capacity = min(capacity, record[2])
                    db.execute("UPDATE wallet_insights_cache_metadata SET capacity=? WHERE singleton=1",
                               (persisted_capacity,))
                self.capacity = persisted_capacity
                count = self._count(db)
                if count > self.capacity:
                    self._remove_expired(db, self.clock(), count-self.capacity)
                    self._remove_lru(db, max(0, self._count(db)-self.capacity))
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self._path, timeout=0.05, uri=self._uri)
        try:
            db.execute("PRAGMA busy_timeout=50")
            db.execute("PRAGMA cache_size=-1024")
            db.execute("PRAGMA mmap_size=0")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _count(db):
        return db.execute("SELECT row_count FROM wallet_insights_cache_metadata WHERE singleton=1").fetchone()[0]

    @staticmethod
    def _next_access(db):
        sequence = db.execute("SELECT access_counter FROM wallet_insights_cache_metadata "
                              "WHERE singleton=1").fetchone()[0] + 1
        db.execute("UPDATE wallet_insights_cache_metadata SET access_counter=? WHERE singleton=1", (sequence,))
        return sequence

    @staticmethod
    def _remove_expired(db, now, limit):
        if limit <= 0:
            return 0
        deleted = db.execute("DELETE FROM wallet_insights_cache WHERE cache_key IN "
                             "(SELECT cache_key FROM wallet_insights_cache WHERE expires_at<=? "
                             "ORDER BY expires_at,cache_key LIMIT ?)", (now, limit)).rowcount
        if deleted:
            db.execute("UPDATE wallet_insights_cache_metadata SET row_count=row_count-? WHERE singleton=1", (deleted,))
        return deleted

    @staticmethod
    def _remove_lru(db, limit):
        if limit <= 0:
            return 0
        deleted = db.execute("DELETE FROM wallet_insights_cache WHERE cache_key IN "
                             "(SELECT cache_key FROM wallet_insights_cache "
                             "ORDER BY last_access,cache_key LIMIT ?)", (limit,)).rowcount
        if deleted:
            db.execute("UPDATE wallet_insights_cache_metadata SET row_count=row_count-? WHERE singleton=1", (deleted,))
        return deleted

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not key or len(key) > 1024:
            raise ValueError("cache key must be a nonempty string of at most 1024 characters")
        return key

    def _read(self, key, touch):
        key = self._key(key)
        with self._connection() as db:
            if touch:
                db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value_json,captured_at,expires_at FROM wallet_insights_cache "
                             "WHERE cache_key=?", (key,)).fetchone()
            if row is None or row[2] <= self.clock():
                return None
            value = json.loads(row[0])
            if touch:
                db.execute("UPDATE wallet_insights_cache SET last_access=? WHERE cache_key=?",
                           (self._next_access(db), key))
        return {"value": value, "captured_at": row[1], "expires_at": row[2]}

    def get(self, key):
        """Read a live value and promote it to most recently used."""
        return self._read(key, True)

    def peek(self, key):
        """Inspect a live entry without changing recency or freshness."""
        return self._read(key, False)

    def put(self, key, value, ttl):
        key = self._key(key)
        if type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be positive and finite")
        now = self.clock()
        raw = json.dumps(value, separators=(",", ":"), allow_nan=False)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            # Normal expiry cleanup is bounded. If capacity still requires a
            # victim, reclaim any further expired row before a live LRU row.
            self._remove_expired(db, now, 64)
            existing = db.execute("SELECT 1 FROM wallet_insights_cache WHERE cache_key=?", (key,)).fetchone()
            if existing is None:
                count, capacity = db.execute("SELECT row_count,capacity FROM wallet_insights_cache_metadata "
                                             "WHERE singleton=1").fetchone()
                excess = max(0, count-capacity+1)
                if excess:
                    self._remove_expired(db, now, excess)
                    self._remove_lru(db, max(0, self._count(db)-capacity+1))
                db.execute("INSERT INTO wallet_insights_cache(cache_key,value_json,captured_at,expires_at,last_access) "
                           "VALUES (?,?,?,?,?)", (key, raw, now, now+ttl, self._next_access(db)))
                db.execute("UPDATE wallet_insights_cache_metadata SET row_count=row_count+1 WHERE singleton=1")
            else:
                db.execute("UPDATE wallet_insights_cache SET value_json=?,captured_at=?,expires_at=?,last_access=? "
                           "WHERE cache_key=?", (raw, now, now+ttl, self._next_access(db), key))
        return {"value": value, "captured_at": now, "expires_at": now+ttl}

    def size(self):
        """Stored rows, including expired rows awaiting reclamation; no scan."""
        with self._connection() as db:
            return self._count(db)

    def close(self):
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None
