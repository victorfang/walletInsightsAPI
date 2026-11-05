#!/usr/bin/env python3
"""Offline capacity/latency benchmark for one million first-seen records.

Bulk loading uses the exact production schema with batched SQLite inserts; the
timed lookups use the public AgeStore.get interface (one connection per call).
No addresses are queried on chain and no private keys are generated.
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
from eth_wallet_check.age_store import AgeStore, _COLUMNS


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
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--lookups", type=int, default=1000)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows <= 0 or args.lookups <= 0:
        parser.error("rows and lookups must be positive")
    if args.db.exists():
        parser.error("database already exists; choose a fresh path")
    started_at = datetime.now(timezone.utc).isoformat()
    initial_rss = rss_bytes()
    store = AgeStore(args.db)
    sql = "INSERT INTO address_first_seen (" + ",".join(_COLUMNS) + ") VALUES (" + ",".join("?" for _ in _COLUMNS) + ")"
    started = time.perf_counter()
    # Each batch is bounded to 5000 records. Do not construct a million-item list.
    with store._connection() as db:
        for start in range(0, args.rows, 5000):
            end = min(start + 5000, args.rows)
            db.executemany(sql, (
                ("eth", "mainnet", "0x" + format(i, "040x"), 1_500_000_000 + i,
                 4_000_000 + i, "0x" + format(i, "064x"), "0x" + format(i, "064x"), "benchmark", '["normal_transactions","token_transfers"]', 1, 1, 1_800_000_000)
                for i in range(start, end)
            ))
    load_seconds = time.perf_counter() - started
    with closing(sqlite3.connect(args.db)) as db:
        count = db.execute("SELECT COUNT(*) FROM address_first_seen").fetchone()[0]
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        page_count = db.execute("PRAGMA page_count").fetchone()[0]
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
    rng = random.Random(20261105)
    ids = [rng.randrange(args.rows) for _ in range(args.lookups)]
    durations = []
    for i in ids:
        started = time.perf_counter()
        row = store.get("eth", "mainnet", "0x" + format(i, "040x"))
        durations.append(time.perf_counter() - started)
        assert row["first_seen_timestamp"] == 1_500_000_000 + i
    miss_started = time.perf_counter()
    assert store.get("eth", "mainnet", "0x" + "f" * 40) is None
    miss_seconds = time.perf_counter() - miss_started
    store.close()
    results = {
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "platform": sys.platform, "row_count": count,
        "load_seconds": load_seconds, "load_rows_per_second": args.rows / load_seconds,
        "database_bytes": args.db.stat().st_size, "bytes_per_record": args.db.stat().st_size / count,
        "sqlite_page_count": page_count, "sqlite_page_size": page_size,
        "peak_rss_before_bytes": initial_rss, "peak_rss_after_bytes": rss_bytes(),
        "additional_peak_rss_bytes": max(0, rss_bytes() - initial_rss),
        "lookup_seconds": {"n": len(durations), "mean": statistics.fmean(durations),
                           "median": statistics.median(durations), "p95": quantile(durations, .95),
                           "p99": quantile(durations, .99), "max": max(durations)},
        "missing_lookup_seconds": miss_seconds,
        "notes": ["Real rows in the production WITHOUT ROWID schema, including full 64-hex block/transaction hashes and scope metadata.",
                  "Synthetic offline records; no blockchain lookup or private key generation.",
                  "Bulk insert throughput differs from observe() throughput; lookups use actual get().",
                  "RSS is process peak memory, not a claim about total OS filesystem cache.",
                  "Lookups run after population; filesystem/provider cold-cache behavior is not measured.",
                  "A Python dict is not constructed; SQLite keeps the million records on disk."]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
