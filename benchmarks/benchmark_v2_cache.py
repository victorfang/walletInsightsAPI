"""Offline SQLite capacity check; synthetic epochs, no API keys or network calls."""

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2.cache import Cache


BASE = 1_500_000_000


def key(index):
    return "1", f"0x{index:040x}", "synthetic-v1"


def stats(values):
    values = sorted(values)
    return {"samples": len(values), "median_ms": statistics.median(values),
            "p95_ms": values[math.ceil(.95 * len(values)) - 1], "max_ms": values[-1]}


async def measure(count, operation):
    values = []
    for index in range(count):
        started = time.perf_counter()
        await operation(index)
        values.append((time.perf_counter() - started) * 1000)
    return stats(values)


async def benchmark(path, entries):
    started = time.perf_counter()
    cache = Cache(path, max_entries=entries)
    await cache.close()
    with closing(sqlite3.connect(path)) as db:
        # Fixture setup is one bulk transaction, not one million cache/fsync calls.
        db.executemany("INSERT INTO first_seen VALUES (?,?,?,?,?)",
                       ((*key(i), BASE + i, i + 1) for i in range(entries)))
        db.execute("UPDATE state SET access_counter=?,row_count=? WHERE id=1",
                   (entries, entries))
        db.commit()
    seed_seconds = time.perf_counter() - started
    samples = min(1000, entries // 4)
    cache = Cache(path, max_entries=entries)
    try:
        async def hit(index):
            assert await cache.get(*key(index)) == BASE + index

        async def earlier(index):
            assert await cache.put(*key(index), BASE + index - 86400) == BASE + index - 86400

        async def churn(index):
            address = entries + index
            assert await cache.put(*key(address), BASE + address) == BASE + address

        timings = {"cache_hit": await measure(samples, hit),
                   "minimum_upsert_at_capacity": await measure(samples, earlier),
                   "insert_with_lru_eviction": await measure(samples, churn)}
        assert await cache.get(*key(0)) == BASE - 86400  # Touched oldest survived churn.
        assert await cache.get(*key(samples)) is None  # Untouched oldest was evicted.
        assert await cache.put(*key(0), BASE) == BASE - 86400  # Later epochs cannot replace MIN.
        assert await cache.get(*key(2 * samples)) == BASE + 2 * samples
        await cache.close()
        cache = Cache(path, max_entries=entries)
        await cache.put(*key(entries + samples), BASE)
        assert await cache.get(*key(2 * samples)) == BASE + 2 * samples
        assert await cache.get(*key(2 * samples + 1)) is None  # Recency survived reopening.
        with closing(sqlite3.connect(path)) as db:
            actual, maximum = db.execute("SELECT COUNT(*),MAX(last_access) FROM first_seen").fetchone()
            counter, recorded = db.execute("SELECT access_counter,row_count FROM state").fetchone()
        assert actual == recorded == entries
        assert counter == maximum == entries + 3 * samples + 5
    finally:
        await cache.close()
    sizes = {"main": path.stat().st_size}
    for suffix in ("-wal", "-shm"):
        file = Path(str(path) + suffix)
        sizes[suffix[1:]] = file.stat().st_size if file.exists() else 0
    sizes["total"] = sum(sizes.values())
    return {"measured_at": datetime.now(timezone.utc).isoformat(),
            "fixture": "synthetic epochs and addresses; offline, not blockchain ground truth",
            "entries": actual, "metadata_entries": recorded, "access_counter": counter,
            "seed_seconds": seed_seconds, "timings": timings, "database_bytes": sizes,
            "checks": {"capacity": True, "metadata": True, "minimum_epoch": True,
                       "lru_eviction": True, "persistent_recency": True}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=1_000_000)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/v2-cache-million.json"))
    args = parser.parse_args()
    if args.entries < 8:
        parser.error("--entries must be at least 8")
    with tempfile.TemporaryDirectory(prefix="wallet-age-v2-cache-") as directory:
        result = asyncio.run(benchmark(Path(directory) / "age.sqlite3", args.entries))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    lines = ["# V2 SQLite cache capacity benchmark", "", result["measured_at"], "",
             "Synthetic address/epoch fixture; no provider requests or API keys. "
             "This measures local cache operations, not HTTP latency or wallet-age accuracy.", "",
             f"- Entries: **{result['entries']:,}**; metadata count matches.",
             f"- Fixture bulk load: {result['seed_seconds']:.3f} seconds (not cache insertion latency).",
             f"- SQLite bytes after close: {result['database_bytes']['total']:,}.",
             "- Capacity, MIN epochs, LRU eviction and recency after reopening: **passed**.", "",
             "| Operation at capacity | Samples | Median ms | p95 ms | Maximum ms |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for name, row in result["timings"].items():
        lines.append(f"| {name} | {row['samples']} | {row['median_ms']:.3f} | "
                     f"{row['p95_ms']:.3f} | {row['max_ms']:.3f} |")
    lines += ["", "One sequential client on the development machine with a warm filesystem cache. "
              "Percentiles use nearest rank. Temporary database and sidecars are deleted; "
              "this report contains no large database artifact.", ""]
    args.output.with_suffix(".md").write_text("\n".join(lines))
    print(json.dumps({"json": str(args.output), "markdown": str(args.output.with_suffix('.md')),
                      "entries": result["entries"], "checks": result["checks"]}))


if __name__ == "__main__":
    main()
