#!/usr/bin/env python3
"""Measure the real V2 HTTP service against the unchanged 100-address corpus.

Live requests consume configured provider quota. No secrets or configured URLs
are written to artifacts. Uses a fresh age database and never calls V1/Insights.
"""

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
import sys
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v2.app import SERVICE, create_app, load_config


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timings(values):
    values = sorted(values)
    return {"count": len(values), "median": statistics.median(values),
            "p95": values[math.ceil(len(values) * .95) - 1], "max": values[-1]} if values else None


def cached_epoch(database, address):
    with sqlite3.connect(database, timeout=.25) as db:
        return db.execute("SELECT MIN(epoch) FROM first_seen WHERE address=?", (address.lower(),)).fetchone()[0]


def summarize(rows, references, metadata):
    cold = [row for row in rows if row["phase"] == "cold"]
    cohorts = {
        "uncached_known_used": [row for row in cold if row["category"] in {"existing_eoa", "existing_contract"}],
        "uncached_format_valid": [row for row in cold if row["category"] != "malformed"],
        "uncached_overall": cold,
        "cached_positive_repeats": [row for row in rows if row["phase"] == "warm"],
    }
    metrics = {name: {"latency_seconds": timings([row["seconds"] for row in cohort]),
                     "statuses": dict(Counter(str(row["status"]) for row in cohort)),
                     "age_results": sum(row["status"] == 200 for row in cohort),
                     "over_three_seconds": sum(row["seconds"] > 3 for row in cohort)}
               for name, cohort in cohorts.items()}
    by_id = {row["id"]: row for row in cold}
    controls = []
    for reference in references["creation_candidates"]:
        row = by_id[reference["id"]]
        controls.append({"id": row["id"], "reference_epoch": reference["timestamp"],
                         "initial_epoch": row["epoch"], "refined_epoch": row["refined_epoch"],
                         "initial_difference_seconds": row["epoch"] - reference["timestamp"] if row["epoch"] else None,
                         "refined_difference_seconds": row["refined_epoch"] - reference["timestamp"] if row["refined_epoch"] else None})
    return {"metadata": metadata, "cohorts": metrics, "deployment_controls": controls,
            "initial_deployment_matches": sum(row["initial_difference_seconds"] == 0 for row in controls),
            "refined_deployment_matches": sum(row["refined_difference_seconds"] == 0 for row in controls),
            "background_epoch_corrections": sum(row["epoch"] is not None and row["refined_epoch"] is not None
                                                and row["refined_epoch"] < row["epoch"] for row in cold),
            "warm_cache_hits": sum(row["cache"] == "hit" for row in cohorts["cached_positive_repeats"]),
            "age_formula_matches": sum(row["formula_matches"] is True for row in rows),
            "age_formula_eligible": sum(row["formula_matches"] is not None for row in rows)}


def report(summary):
    lines = ["# V2 walletAgeDays benchmark", "", f"Measured {summary['metadata']['started_at']}.", "",
             "Real local HTTP and configured provider requests; fresh isolated SQLite LRU; one client at a time.", "",
             "| Cohort | Count | Integer ages | Median | p95 | Maximum | Over 3 s |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, cohort in summary["cohorts"].items():
        latency = cohort["latency_seconds"]
        if latency:
            lines.append(f"| {name} | {latency['count']} | {cohort['age_results']} | {latency['median']:.4f} s | "
                         f"{latency['p95']:.4f} s | {latency['max']:.4f} s | {cohort['over_three_seconds']} |")
    lines += ["", f"Initial deployment-control epoch agreement: {summary['initial_deployment_matches']}/20. "
              f"After bounded refinement: {summary['refined_deployment_matches']}/20.", "",
              f"Background epoch corrections: {summary['background_epoch_corrections']}. "
              f"Positive-repeat cache hits: {summary['warm_cache_hits']}. "
              f"Completed-day formula checks: {summary['age_formula_matches']}/{summary['age_formula_eligible']}.", "",
              "## Interpretation", "",
              "The corpus contains 40 known-used controls, 40 format-valid synthetic candidates and 20 malformed inputs. "
              "Synthetic absence is not established; unavailable history must not be scored as age zero. "
              "Contract deployment is independent activity evidence, not proof that no earlier prefunding occurred. "
              "These controls measure deployment agreement, not universal wallet-age or fraud-detection accuracy.", "",
              "Cold response latency excludes deliberate pacing and the wait for bounded background refinement. "
              "Warm results repeat only cold successes after refinement. This measures a small warmed cache, "
              "not one-million-entry capacity or production concurrency. Provider-side caches cannot be cleared. "
              "No provider-attempt count, billing count or hosted-site latency is asserted.", "",
              "Artifacts: `metadata.json`, `results.jsonl`, `summary.json`; credentials and configured URLs are omitted.", ""]
    gaps = [row for row in summary["deployment_controls"] if row["refined_difference_seconds"] != 0]
    if gaps:
        lines += ["## Deployment-control gaps", "", "| Case | Initial delta, seconds | Refined delta, seconds |",
                  "| --- | ---: | ---: |"]
        lines += [f"| {row['id']} | {row['initial_difference_seconds']} | {row['refined_difference_seconds']} |"
                  for row in gaps]
        lines += ["", "These discrepancies remain discovery/verification limitations. A valid response is an observed "
                  "lower bound, not proof of the exact first-ever appearance. See `summary.json` for every control.", ""]
    return "\n".join(lines)


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError("Use a fresh empty output directory")
    dataset = ROOT / "benchmarks/addresses.json"
    cases = json.loads(dataset.read_text())["cases"]
    references = json.loads((ROOT / "benchmarks/references.json").read_text())
    assert len(cases) == 100
    config = load_config(args.config, env_file=args.env_file)
    database = args.output.resolve() / "wallet-age.sqlite3"
    config["wallet_age"]["database"] = str(database)
    config.setdefault("legacy", {})["enabled"] = False
    app = await create_app(config)
    service = app[SERVICE]
    if service.coverage != "ethereum":
        await service.close()
        raise ValueError("This corpus requires Ethereum-only coverage")
    captured = {}
    original_check = service.check

    async def capture(blockchain, address):
        result = await original_check(blockchain, address)
        captured[address.lower()] = {"epoch": result.epoch, "returned_at": time.time()}
        return result

    service.check = capture
    metadata = {"started_at": stamp(), "python": sys.version, "seed": args.seed,
                "coverage": service.coverage, "cache_capacity": config["wallet_age"].get("max_entries", 1_000_000),
                "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                "runtime_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in sorted((ROOT / "v2").glob("*.py"))},
                "methodology": "one client; isolated cache; cold responses then bounded refinement; positive warm repeats"}
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    headers = {"x-api-key": config.get("server", {}).get("api_key") or ""}
    rows = []
    try:
        async with ClientSession(headers=headers, timeout=ClientTimeout(total=4)) as client:
            async def check(case, phase):
                started = time.perf_counter()
                try:
                    async with client.get(base + "/v2/walletAgeDays", params={"blockchain": "ETH", "address": case["address"]}) as response:
                        body = await response.json()
                        status, cache = response.status, response.headers.get("X-Wallet-Age-Cache")
                except (ClientError, TimeoutError, ValueError):
                    status, body, cache = 0, "client_error", None
                elapsed = time.perf_counter() - started
                result = captured.pop(case["address"].lower(), {})
                epoch, returned_at = result.get("epoch"), result.get("returned_at")
                row = {"id": case["id"], "category": case["category"], "phase": phase,
                       "status": status, "body": body, "seconds": elapsed,
                       "cache": cache, "epoch": epoch, "refined_epoch": epoch,
                       "formula_matches": body == max(0, (int(returned_at) - epoch) // 86400) if epoch else None}
                if phase == "cold" and case["category"] != "malformed":
                    budget = config["wallet_age"].get("deadline_ms", 2600) / 1000
                    await asyncio.sleep(max(0, budget + .05 - elapsed))
                    row["refined_epoch"] = await asyncio.to_thread(cached_epoch, database, case["address"])
                    await asyncio.sleep(args.pause)
                rows.append(row)
                with (args.output / "results.jsonl").open("a") as stream:
                    stream.write(json.dumps(row) + "\n")

            shuffled = list(cases)
            random.Random(args.seed).shuffle(shuffled)
            for index, case in enumerate(shuffled, 1):
                await check(case, "cold")
                if index % 10 == 0:
                    print(json.dumps({"cold_completed": index}), flush=True)
            successful_ids = {row["id"] for row in rows if row["status"] == 200}
            for case in cases:
                if case["id"] in successful_ids:
                    await check(case, "warm")
    finally:
        await runner.cleanup()
    summary = summarize(rows, references, metadata)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.output / "REPORT.md").write_text(report(summary))
    print(json.dumps(summary["cohorts"], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "v2/config.toml")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--pause", type=float, default=.2)
    args = parser.parse_args()
    if args.pause < 0:
        parser.error("pause must be nonnegative")
    with open("/private/tmp/wallet-age-benchmark-network.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
