#!/usr/bin/env python3
"""Populate the bounded LRU walletAgeDays schema and time real reads/touches.

Uses offline synthetic records and never queries or funds a blockchain address.
Bulk load measures SQLite capacity, not per-operation put_min throughput.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import resource
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from wallet_age_days.store import EpochStore, MAX_CAPACITY


GROUP = "evm:1:v1"


def rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def quantile(values, q):
    ordered = sorted(values)
    point = (len(ordered) - 1) * q
    low = int(point)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=MAX_CAPACITY)
    parser.add_argument("--lookups", type=int, default=1000)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.rows <= MAX_CAPACITY or args.lookups <= 0:
        parser.error("rows must be from 1 to 1000000; lookups must be positive")
    if args.db.exists() or args.output.exists():
        parser.error("database and output paths must be fresh")
    started_at = datetime.now(timezone.utc).isoformat()
    initial_rss = rss_bytes()
    store = EpochStore(args.db, capacity=args.rows)
    started = time.perf_counter()
    # A single transaction preserves exact counter/table agreement. Only each
    # bounded generator is in Python memory, never the million-record corpus.
    with store._connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for start in range(0, args.rows, 5000):
            db.executemany(
                "INSERT INTO first_appearances(chain, address, epoch, last_access) VALUES (?, ?, ?, ?)",
                ((GROUP, "0x" + format(i, "040x"), 1_500_000_000 + i, i + 1)
                 for i in range(start, min(start + 5000, args.rows)))
            )
        db.execute("UPDATE epoch_store_metadata SET row_count = ?, access_counter = ? WHERE singleton = 1",
                   (args.rows, args.rows))
    load_seconds = time.perf_counter() - started
    with closing(sqlite3.connect(args.db)) as db:
        count = db.execute("SELECT COUNT(*) FROM first_appearances").fetchone()[0]
        assert store.size() == count == args.rows
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page_count = db.execute("PRAGMA page_count").fetchone()[0]
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
    rng = random.Random(20261105)
    durations = []
    for _ in range(args.lookups):
        i = rng.randrange(args.rows)
        address = "0x" + format(i, "040x")
        started = time.perf_counter()
        epoch = store.get(GROUP, address)
        durations.append(time.perf_counter() - started)
        assert epoch == 1_500_000_000 + i
    missing_address = "0x" + "f" * 40
    started = time.perf_counter()
    assert store.get(GROUP, missing_address) is None
    missing_seconds = time.perf_counter() - started
    # Exercise real LRU admission at capacity, including protecting a hot key.
    hot_address = "0x" + "0" * 40
    assert store.get(GROUP, hot_address) == 1_500_000_000
    with store._connection() as db:
        victim = db.execute("SELECT chain,address FROM first_appearances "
                            "ORDER BY last_access,chain,address LIMIT 1").fetchone()
    started = time.perf_counter()
    new_record_stored = store.put_min(GROUP, missing_address, 1_500_000_000)
    eviction_insert_seconds = time.perf_counter() - started
    oldest_evicted = store.peek(*victim) is None
    hot_retained = store.peek(GROUP, hot_address) == 1_500_000_000 if args.rows > 1 else None
    existing_record_updated = store.put_min(GROUP, missing_address, 1_499_999_999)
    assert new_record_stored and oldest_evicted and existing_record_updated
    assert hot_retained is not False
    assert store.get(GROUP, missing_address) == 1_499_999_999
    assert store.size() == args.rows
    with closing(sqlite3.connect(args.db)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page_count = db.execute("PRAGMA page_count").fetchone()[0]
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
    store.close()
    final_rss = rss_bytes()
    results = {
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "platform": sys.platform, "row_count": count,
        "chain_key": GROUP, "database_capacity": args.rows, "eviction_policy": "lru",
        "load_seconds": load_seconds, "load_rows_per_second": args.rows / load_seconds,
        "database_bytes": args.db.stat().st_size, "bytes_per_record": args.db.stat().st_size / count,
        "sqlite_page_count": page_count, "sqlite_page_size": page_size,
        "peak_rss_before_bytes": initial_rss, "peak_rss_after_bytes": final_rss,
        "additional_peak_rss_bytes": max(0, final_rss - initial_rss),
        "lookup_seconds": {"n": len(durations), "mean": statistics.fmean(durations),
                           "median": statistics.median(durations), "p95": quantile(durations, .95),
                           "p99": quantile(durations, .99), "max": max(durations)},
        "missing_lookup_seconds": missing_seconds,
        "capacity_boundary": {"new_record_stored": new_record_stored,
                              "least_recent_entry_evicted": oldest_evicted,
                              "recently_read_entry_retained": hot_retained,
                              "existing_record_updated": existing_record_updated,
                              "row_count_unchanged": True},
        "eviction_insert_seconds": eviction_insert_seconds,
        "notes": [
            "Actual rows in the production WITHOUT ROWID schema: chain, address, epoch, persisted access sequence and LRU index.",
            "Chain namespace evm:1:v1 represents current Ethereum-only coverage; it does not establish every EVM chain's age.",
            "Synthetic offline records; no blockchain queries, funding or private keys.",
            "Bulk insertion throughput differs from put_min() throughput; timed get() includes per-operation setup and a persisted LRU touch.",
            "Read timings follow population with a warm OS filesystem cache; cold storage and upstream caches are not measured.",
            "RSS is process peak resident memory, excluding the operating system filesystem page cache.",
            "The complete million-record dictionary is never constructed in RAM; SQLite connection page cache is bounded.",
            "At capacity a new fact was stored and the least recently used entry evicted; a recently read key was retained and an existing epoch improved."
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
