"""Measure authenticated wallet POST requests. Writes aggregate metrics only."""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import getpass
import json
import math
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
from time import perf_counter

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
RAILWAY = "https://walletinsightsapi-production.up.railway.app"
AGE, INSIGHTS = "walletAgeDays", "walletInsights"


def percentiles(samples):
    if not samples:
        return None
    ordered = sorted(samples)
    return {name: round(ordered[math.ceil(len(ordered) * p) - 1], 2)
            for name, p in (("p50", .50), ("p95", .95), ("p99", .99), ("max", 1))}


async def stage(session, origin, route, label, addresses, concurrency, *, count=None, seconds=None):
    # Each worker starts its next request as soon as the previous one finishes.
    started, issued, rows = perf_counter(), 0, []

    async def worker():
        nonlocal issued
        while (issued < count if count is not None else perf_counter() - started < seconds):
            address = addresses[issued % len(addresses)]
            issued += 1
            begin = perf_counter()
            try:
                async with session.post(f"{origin}/{route}", allow_redirects=False,
                                        json={"blockchain": "ETH", "address": address}) as response:
                    status = response.status
                    body = await response.json()
                    if status in (401, 403) or 300 <= status < 400:
                        raise RuntimeError(f"Unexpected HTTP {status}; stopping the test")
                    if origin == RAILWAY and status == 429:
                        raise RuntimeError("Railway quota reached; stopping the test")
                    if route == AGE:
                        ok = status == 200 and type(body) is int and body >= 0
                        detail = "integer-age" if ok else str(status)
                        cache = response.headers.get("X-Wallet-Age-Cache", "none")
                    else:
                        detail = body.get("status") if isinstance(body, dict) else str(status)
                        ok = status == 200 and detail in ("complete", "partial") and body.get("address") == address.lower()
                        cache = response.headers.get("X-Wallet-Insights-Cache", "none")
            except (aiohttp.ClientError, TimeoutError, ValueError):
                status, ok, detail, cache = 0, False, "transport-or-body-error", "none"
            rows.append((status, ok, detail, cache, 1000 * (perf_counter() - begin)))

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = perf_counter() - started
    success = [r[4] for r in rows if r[1]]
    result = {
        "stage": label, "route": route, "concurrency": concurrency, "requests": len(rows),
        "success": len(success), "elapsed_s": round(elapsed, 4),
        "success_tps": round(len(success) / elapsed, 2), "total_rps": round(len(rows) / elapsed, 2),
        "status": dict(Counter(str(r[0]) for r in rows)), "results": dict(Counter(r[2] for r in rows)),
        "cache": dict(Counter(r[3] for r in rows)), "success_ms": percentiles(success),
        "all_ms": percentiles([r[4] for r in rows]), "over_3s": sum(r[4] > 3000 for r in rows),
    }
    print(json.dumps(result), flush=True)
    return result


async def exercise(origin, key, wallets, target):
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(headers={"x-api-key": key}, timeout=timeout) as session:
        rows = []
        async def run(route, label, addresses, concurrency, **limits):
            rows.append(await stage(session, origin, route, label, addresses, concurrency, **limits))

        if target == "local-burst":
            cases = json.loads((ROOT / "benchmarks/addresses.json").read_text())["cases"]
            candidates = [case["address"] for case in cases if case["category"] == "generated_valid"]
            await run(AGE, "cold-burst-above-32", wallets, 40, count=40)
            await asyncio.sleep(2.7)
            await run(AGE, "unseen-candidates", candidates[:16], 8, count=16)
            await asyncio.sleep(2.7)
            await run(AGE, "default-quota-boundary", wallets[:1], 4, count=12)
        elif target == "local":
            for concurrency in (1, 8, 32, 64):
                await run(AGE, "seeded-cache", wallets[:8], concurrency, seconds=8)
            for offset, size, concurrency in ((8, 4, 1), (12, 12, 4), (24, 16, 8)):
                await run(AGE, "fresh-database-keys", wallets[offset:offset+size], concurrency, count=size)
                await asyncio.sleep(2.7)  # Drain previous discovery jobs outside the measured phase.
            await run(INSIGHTS, "first-lookup", wallets[12:28], 4, count=16)
            await run(INSIGHTS, "burst", wallets[28:36], 8, count=8)
            await run(INSIGHTS, "prime", wallets[:4], 4, count=4)
            await run(INSIGHTS, "repeat", wallets[:4], 4, seconds=5)
        else:
            # 46 total requests, at most four in flight, on a single client key.
            await run(AGE, "first-lookup", wallets[8:16], 4, count=8)
            await run(AGE, "repeat", wallets[8:16], 4, count=24)
            await run(INSIGHTS, "first-lookup", wallets[12:16], 2, count=4)
            await run(INSIGHTS, "repeat", wallets[12:16], 4, count=10)
        return rows


async def local_run(directory, wallets, env_file, target):
    sys.path.insert(0, str(ROOT))
    from v2.cache import Cache
    cases = json.loads((ROOT / "benchmarks/addresses.json").read_text())["cases"]
    epochs = {r["id"]: r["refined_epoch"] for line in (ROOT / "benchmarks" /
              "run-v2-final-2026-11-06/results.jsonl").read_text().splitlines()
              if (r := json.loads(line))["refined_epoch"]}
    cache = Cache(directory / "data/wallet-age.sqlite3")
    for case in cases:
        if target == "local" and case["address"].lower() in wallets[:8]:
            await cache.put("1", case["address"].lower(), "history-v1", epochs[case["id"]])
    await cache.close()
    config = (ROOT / "v2/config.toml").read_text()
    if target == "local":
        assert "requests_per_minute = 60 " in config
        config = config.replace("requests_per_minute = 60 ", "requests_per_minute = 100000 ")
    (directory / "config.toml").write_text(config)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = secrets.token_hex(24)
    env = dict(os.environ, WALLET_CHECK_API_KEY=key, WALLET_INSIGHTS_DB=str(directory / "insights.sqlite3"))
    cmd = [sys.executable, "-m", "v2.app", "--config", str(directory / "config.toml"),
           "--port", str(port), "--env-file", str(env_file)]
    origin = f"http://127.0.0.1:{port}"
    with (directory / "access.log").open("wb") as log:
        process = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            async with aiohttp.ClientSession() as probe:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Isolated server exited during startup")
                    try:
                        async with probe.get(origin + "/health") as response:
                            if response.status == 200:
                                break
                    except aiohttp.ClientConnectionError:
                        pass
                    await asyncio.sleep(.05)
                else:
                    raise RuntimeError("Isolated server did not become ready")
            return await exercise(origin, key, wallets, target)
        finally:
            process.terminate()
            process.wait(timeout=15)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("local", "local-burst", "railway"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    cases = json.loads((ROOT / "benchmarks/addresses.json").read_text())["cases"]
    wallets = [c["address"].lower() for c in cases if c["category"] in ("existing_eoa", "existing_contract")]
    started = datetime.now(timezone.utc).isoformat()
    if args.target == "railway":
        key = os.getenv("CLIENT_API_KEY")
        if not key and args.env_file.is_file():
            for line in args.env_file.read_text().splitlines():
                name, _, value = line.strip().removeprefix("export ").partition("=")
                if name == "CLIENT_API_KEY":
                    key = value.strip().strip("\"'")
        rows = await exercise(RAILWAY, key or getpass.getpass("Railway client key: "), wallets, "railway")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            rows = await local_run(Path(tmp), wallets, args.env_file, args.target)
    metrics = {"target": args.target, "start_utc": started, "finish_utc": datetime.now(timezone.utc).isoformat(),
               "python": platform.python_version(), "aiohttp": aiohttp.__version__, "platform": platform.platform(),
               "cpu_count": os.cpu_count(), "rows": rows}
    args.output.write_text(json.dumps(metrics, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
