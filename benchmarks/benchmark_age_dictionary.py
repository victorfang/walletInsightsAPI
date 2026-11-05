#!/usr/bin/env python3
"""Measure a real in-memory chain/address -> epoch dictionary without network I/O.

Run in a fresh Python process so RSS is independent of the SQLite benchmark:

    python3 benchmarks/benchmark_age_dictionary.py --rows 1000000 \
        --output /tmp/age-dictionary-million.json

This intentionally stores only an epoch per value. It does not provide SQLite's
durability, completeness/finality flags, hashes, scope, or provenance. Python
object sizes are summed across all actual records; there is no extrapolation.
"""

import argparse
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import platform
import resource
import statistics
import sys
import time


RSS_SAFETY_LIMIT_BYTES = 490 * 1024 * 1024


def rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def quantile(ordered, q):
    point = (len(ordered) - 1) * q
    low = int(point)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (point - low)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--lookups", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows <= 0 or args.lookups <= 0:
        parser.error("rows and lookups must be positive")
    if args.output.exists():
        parser.error("output already exists; choose a fresh path to preserve earlier results")

    started_at = datetime.now(timezone.utc).isoformat()
    gc.collect()
    rss_before = rss_bytes()
    if rss_before >= RSS_SAFETY_LIMIT_BYTES:
        parser.error("process already exceeds the 490 MiB RSS safety limit; use a fresh process")
    chain, epoch_base = "eth:mainnet", 1_600_000_000
    observations = {}
    try:
        started = time.perf_counter()
        # A guard every 10k records limits overshoot to a small allocation batch.
        # The dictionary itself really contains every record at the same time.
        for start in range(0, args.rows, 10_000):
            for index in range(start, min(start + 10_000, args.rows)):
                observations[(chain, f"0x{index:040x}")] = epoch_base + index
            if rss_bytes() >= RSS_SAFETY_LIMIT_BYTES:
                raise MemoryError(
                    f"Aborted at {len(observations)} rows: process reached the 490 MiB RSS safety limit"
                )
        load_seconds = time.perf_counter() - started
        assert len(observations) == args.rows
        rss_after = rss_bytes()

        # Each tuple, address and epoch is a distinct object. The shared chain
        # string is counted once; references inside tuples are already included
        # in each tuple's shallow size. Do not build a second set of all objects.
        object_bytes = sys.getsizeof(observations) + sys.getsizeof(chain)
        for key, value in observations.items():
            object_bytes += sys.getsizeof(key) + sys.getsizeof(key[1]) + sys.getsizeof(value)

        durations = []
        for lookup in range(args.lookups):
            index = (lookup * 104729) % args.rows
            key = (chain, f"0x{index:040x}")
            started = time.perf_counter()
            value = observations[key]
            durations.append(time.perf_counter() - started)
            assert value == epoch_base + index
        durations.sort()
        result = {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "platform": sys.platform,
            "row_count": len(observations),
            "key_format": "(chain: str, address: str); shared chain string eth:mainnet, 42-character ETH address",
            "value_format": "distinct integer first_seen_timestamp per record",
            "load_seconds": load_seconds,
            "load_rows_per_second": args.rows / load_seconds,
            "reachable_python_object_bytes": object_bytes,
            "python_object_bytes_per_record": object_bytes / args.rows,
            "peak_rss_before_bytes": rss_before,
            "peak_rss_after_bytes": rss_after,
            "additional_peak_rss_bytes": max(0, rss_after - rss_before),
            "rss_safety_limit_bytes": RSS_SAFETY_LIMIT_BYTES,
            "lookup_seconds": {
                "n": len(durations), "mean": statistics.fmean(durations),
                "median": statistics.median(durations),
                "p95": quantile(durations, .95), "p99": quantile(durations, .99),
                "max": max(durations),
            },
            "tracemalloc": {
                "status": "disabled",
                "note": "Instrumentation is omitted to keep memory bounded; object sizes and process RSS are measured directly.",
            },
            "notes": [
                "All rows are simultaneously present in a real dictionary in a fresh process; no SQLite allocations are retained.",
                "Synthetic offline addresses and timestamps; no private keys, blockchain queries, or funding.",
                "Object memory is the sum of sys.getsizeof over actual distinct reachable dict, tuple, address-string and integer objects. It excludes Python allocator fragmentation and interpreter overhead.",
                "The shared chain string is stored once; each address string, tuple key and epoch integer is distinct.",
                "Only an integer epoch is stored per value: no completeness, finality, provenance, block hashes, transaction hashes, or durable storage.",
                "Lookup key construction is outside each timed interval.",
                "RSS is process peak resident memory and cannot be compared directly to SQLite disk size.",
            ],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except MemoryError as exc:
        # A failed run produces no result file that could be mistaken for a
        # complete million-row measurement. Process exit releases all records.
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        observations.clear()
        gc.collect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
