"""Durable first-observation epochs in a bounded, persistent LRU cache.

Only finalized positive observations belong here. Values have no TTL, but a new
entry evicts the least recently used one when the database is at capacity.
"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


MAX_CAPACITY = 1_000_000


def _key(chain, address):
    for name, value, limit in (("chain", chain, 64), ("address", address, 256)):
        if not isinstance(value, str) or not value or len(value) > limit:
            raise ValueError(f"{name} must be a nonempty string of at most {limit} characters")
    # Chain adapters own canonicalization, especially for case-sensitive chains.
    return chain, address


class EpochStore:
    """Keep MIN(epoch) per (chain, address), with no expiry and LRU eviction.

    Successful reads and writes advance a database-wide sequence, making LRU
    ordering deterministic even when wall-clock times tie or move backwards.
    Transactions serialize recency, eviction and admission across processes.
    Connections wait at most 50 ms for locks and use a bounded SQLite page cache.
    Reopening with a tighter capacity persists that limit for every connection;
    reopening with a larger value cannot enlarge the existing database limit.
    """

    def __init__(self, path, capacity=MAX_CAPACITY, clock=time.time):
        if type(capacity) is not int or not 0 < capacity <= MAX_CAPACITY:
            raise ValueError(f"capacity must be an integer from 1 to {MAX_CAPACITY}")
        self.clock = clock
        self.path = str(path)
        self._uri = self.path == ":memory:"
        self._keeper = None
        if self._uri:
            self._path = f"file:wallet-age-epochs-{uuid4().hex}?mode=memory&cache=shared"
            self._keeper = sqlite3.connect(self._path, uri=True, check_same_thread=False,
                                           timeout=0.05)
        else:
            file_path = Path(path).expanduser()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(file_path)
        try:
            with self._connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS first_appearances ("
                    "chain TEXT NOT NULL, address TEXT NOT NULL, "
                    "epoch INTEGER NOT NULL CHECK(epoch > 0), "
                    "last_access INTEGER NOT NULL DEFAULT 0 CHECK(last_access >= 0), "
                    "PRIMARY KEY(chain, address)) WITHOUT ROWID"
                )
                if "last_access" not in {row[1] for row in db.execute("PRAGMA table_info(first_appearances)")}:
                    db.execute("ALTER TABLE first_appearances ADD COLUMN last_access INTEGER NOT NULL DEFAULT 0")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS first_appearances_lru "
                    "ON first_appearances(last_access, chain, address)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS epoch_store_metadata ("
                    "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
                    "row_count INTEGER NOT NULL CHECK(row_count >= 0), "
                    "capacity INTEGER NOT NULL CHECK(capacity > 0 AND capacity <= 1000000), "
                    "access_counter INTEGER NOT NULL DEFAULT 0 CHECK(access_counter >= 0))"
                )
                if "access_counter" not in {row[1] for row in db.execute("PRAGMA table_info(epoch_store_metadata)")}:
                    db.execute("ALTER TABLE epoch_store_metadata ADD COLUMN access_counter INTEGER NOT NULL DEFAULT 0")
                # Full COUNT is only needed for a new metadata row/migration,
                # never for normal lookups, updates or full-cache insertions.
                metadata = db.execute(
                    "SELECT row_count,capacity,access_counter FROM epoch_store_metadata WHERE singleton=1"
                ).fetchone()
                maximum = db.execute(
                    "SELECT last_access FROM first_appearances ORDER BY last_access DESC LIMIT 1"
                ).fetchone()
                maximum = maximum[0] if maximum else 0
                if metadata is None:
                    row_count = db.execute("SELECT COUNT(*) FROM first_appearances").fetchone()[0]
                    persisted_capacity, counter = capacity, maximum
                    db.execute("INSERT INTO epoch_store_metadata VALUES (1,?,?,?)",
                               (row_count, persisted_capacity, counter))
                else:
                    row_count, persisted_capacity, counter = metadata
                self.capacity = min(capacity, persisted_capacity)
                if row_count > self.capacity:
                    row_count -= self._evict(db, row_count - self.capacity)
                db.execute("UPDATE epoch_store_metadata SET row_count=?,capacity=?,access_counter=? WHERE singleton=1",
                           (row_count, self.capacity, max(counter, maximum)))
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
    def _evict(db, count):
        # The LRU index makes normal full-cache eviction an indexed first-row
        # lookup. Legacy rows all start at zero and tie by chain/address.
        return db.execute(
            "DELETE FROM first_appearances WHERE (chain,address) IN "
            "(SELECT chain,address FROM first_appearances "
            "ORDER BY last_access,chain,address LIMIT ?)", (count,)
        ).rowcount

    def peek(self, chain, address):
        """Inspect a cached epoch without changing its recency."""
        key = _key(chain, address)
        with self._connection() as db:
            row = db.execute(
                "SELECT epoch FROM first_appearances WHERE chain=? AND address=?", key
            ).fetchone()
        return None if row is None else row[0]

    def get(self, chain, address):
        """Read and mark a found entry as most recently used; misses do not touch."""
        key = _key(chain, address)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT epoch FROM first_appearances WHERE chain=? AND address=?", key
            ).fetchone()
            if row is None:
                return None
            sequence = db.execute(
                "SELECT access_counter FROM epoch_store_metadata WHERE singleton=1"
            ).fetchone()[0] + 1
            db.execute("UPDATE first_appearances SET last_access=? WHERE chain=? AND address=?",
                       (sequence, *key))
            db.execute("UPDATE epoch_store_metadata SET access_counter=? WHERE singleton=1", (sequence,))
        return row[0]

    def put_min(self, chain, address, epoch):
        """Touch/update an existing minimum or insert after indexed LRU eviction.

        A valid stored observation returns True, including a full-cache insertion
        that replaced a less recently used address. No capacity-bypass path is
        needed: admission and eviction happen in the same transaction.
        """
        key = _key(chain, address)
        if type(epoch) is not int or epoch <= 0 or epoch > int(self.clock()):
            raise ValueError("epoch must be positive integer seconds, not in the future")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT epoch FROM first_appearances WHERE chain=? AND address=?", key
            ).fetchone()
            row_count, persisted_capacity, counter = db.execute(
                "SELECT row_count,capacity,access_counter FROM epoch_store_metadata WHERE singleton=1"
            ).fetchone()
            self.capacity = persisted_capacity
            sequence = counter + 1
            if existing is not None:
                db.execute("UPDATE first_appearances SET epoch=?,last_access=? WHERE chain=? AND address=?",
                           (min(epoch, existing[0]), sequence, *key))
            else:
                if row_count >= persisted_capacity:
                    row_count -= self._evict(db, row_count - persisted_capacity + 1)
                db.execute("INSERT INTO first_appearances(chain,address,epoch,last_access) VALUES (?,?,?,?)",
                           (*key, epoch, sequence))
                row_count += 1
            db.execute("UPDATE epoch_store_metadata SET row_count=?,access_counter=? WHERE singleton=1",
                       (row_count, sequence))
        return True

    def size(self):
        """Read the tracked size without scanning or touching address rows."""
        with self._connection() as db:
            return db.execute("SELECT row_count FROM epoch_store_metadata WHERE singleton=1").fetchone()[0]

    def close(self):
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None
