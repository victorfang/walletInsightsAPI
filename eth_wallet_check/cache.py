"""Persistent, thread-safe JSON cache with a 24-hour default lifetime."""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterator
from uuid import uuid4


class SQLiteCache:
    """Cache dictionaries until their absolute expiration time.

    File-backed entries survive service restarts. Each operation uses a separate
    SQLite connection, so one instance can be shared between request threads.
    Keys are used exactly as supplied; callers should normalize addresses.
    """

    def __init__(
        self,
        path: str | Path,
        ttl_seconds: int = 86400,
        clock: Callable[[], float] = time.time,
        timeout: float = 0.05,
    ) -> None:
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive and finite")
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.path = str(path)
        self.timeout = timeout
        self._keeper: sqlite3.Connection | None = None
        self._uri = str(path) == ":memory:"
        if self._uri:
            # Keep a shared in-memory database alive across per-operation
            # connections. A unique URI isolates separate cache instances.
            self._path = f"file:eth-wallet-cache-{uuid4().hex}?mode=memory&cache=shared"
            self._keeper = sqlite3.connect(
                self._path, uri=True, check_same_thread=False
            )
        else:
            file_path = Path(path).expanduser()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            self._path = str(file_path)
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS cache_entries ("
                "cache_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, "
                "expires_at REAL NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS cache_entries_expiration "
                "ON cache_entries (expires_at)"
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=self.timeout, uri=self._uri)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key: str) -> dict | None:
        """Return a fresh dictionary, or None when absent or expired."""
        now = self.clock()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json, expires_at FROM cache_entries WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if row[1] <= now:
                # Keep a concurrent writer's fresh replacement if one arrived
                # between the read above and this conditional cleanup.
                connection.execute(
                    "DELETE FROM cache_entries WHERE cache_key = ? AND expires_at <= ?",
                    (key, now),
                )
                return None
        return json.loads(row[0])

    def set(self, key: str, value: dict) -> None:
        """Store a JSON snapshot and reset this key's expiration time."""
        if not isinstance(value, dict):
            raise TypeError("cache values must be dictionaries")
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False)
        now = self.clock()
        with self._connection() as connection:
            connection.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (now,))
            connection.execute(
                "INSERT INTO cache_entries (cache_key, value_json, expires_at) "
                "VALUES (?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
                "value_json = excluded.value_json, expires_at = excluded.expires_at",
                (key, encoded, now + self.ttl_seconds),
            )

    def close(self) -> None:
        """Release an optional in-memory database; file-backed caches are idle."""
        if self._keeper is not None:
            self._keeper.close()
            self._keeper = None
