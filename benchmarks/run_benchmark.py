#!/usr/bin/env python3
"""Benchmark the real local HTTP handler with isolated cache and public sources.

No transactions are submitted. Provider URLs and credentials are never recorded.
Results are appended after every request, so a partial run remains inspectable.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eth_wallet_check.cache import SQLiteCache
from eth_wallet_check.config import rpc_endpoints
from eth_wallet_check.history import PublicHistory
from eth_wallet_check.http import JSONHTTP, SourceError
from eth_wallet_check.rpc import PublicRPC
from eth_wallet_check.server import WalletHTTPServer
from eth_wallet_check.service import WalletChecker, valid_address


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MeteredHTTP:
    def __init__(self, timeout, endpoints):
        self.http = JSONHTTP(timeout=timeout)
        self.endpoints = dict((url, name) for name, url in endpoints)
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, url, payload=None):
        parts = urlsplit(url)
        if url in self.endpoints:
            source = self.endpoints[url]
        elif parts.hostname == "eth.blockscout.com":
            source = "blockscout"
        elif parts.hostname == "api.routescan.io":
            source = "routescan"
        elif parts.hostname == "api.blockcypher.com":
            source = "blockcypher"
        else:
            source = "other"
        methods = [row.get("method") for row in payload] if isinstance(payload, list) else (
            [payload.get("method")] if isinstance(payload, dict) else []
        )
        action = parse_qs(parts.query).get("action", [None])[0]
        event = {"source": source, "rpc_methods": methods, "action": action, "success": False}
        started = time.perf_counter()
        try:
            response = self.http(url, payload)
            event["success"] = True
            if isinstance(response, dict) and "status" in response:
                event["api_status"] = response["status"]
            if isinstance(response, dict) and "error" in response:
                event["rpc_error"] = True
            return response
        except Exception as exc:
            event["error"] = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
            raise
        finally:
            event["seconds"] = time.perf_counter() - started
            with self.lock:
                self.events.append(event)


def api_record(body):
    records = body.get("data", {})
    return next(iter(records.values()), {}) if isinstance(records, dict) else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks" / "addresses.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--pause", type=float, default=0.3, help="Pause between uncached requests; excluded from measured latency")
    parser.add_argument("--warm-repeats", type=int, default=3, help="Additional cache-hit-only passes")
    parser.add_argument("--seed", type=int, default=20261105)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    cases = dataset["cases"] if isinstance(dataset, dict) else dataset
    assert len(cases) == 100, "Expected exactly 100 cases"
    assert len({row["address"] for row in cases}) == 100, "Duplicate raw inputs"
    normalized = [row["address"].lower() for row in cases if row["expected_valid_format"]]
    assert len(normalized) == len(set(normalized)), "Valid addresses must have independent cache keys"
    args.output.mkdir(parents=True, exist_ok=True)
    cache_path = args.output / "cache.sqlite3"
    if cache_path.exists() or (args.output / "results.jsonl").exists():
        parser.error("Output already contains benchmark state; use a fresh directory")
    endpoints = rpc_endpoints()
    meter = MeteredHTTP(args.timeout, endpoints)
    cache = SQLiteCache(cache_path)
    checker = WalletChecker(PublicHistory(meter), PublicRPC(meter, endpoints), cache, rpc_first=True)
    production = sorted((ROOT / "eth_wallet_check").glob("*.py")) + [ROOT / "check_eth_wallet.py", ROOT / "openapi.json"]
    metadata = {
        "started_at": stamp(), "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "python": sys.version, "cases": len(cases), "seed": args.seed,
        "concurrency": 1, "upstream_timeout_seconds": args.timeout,
        "inter_uncached_request_pause_seconds": args.pause,
        "additional_cache_hit_passes": args.warm_repeats,
        "provider_order": [name for name, _ in endpoints],
        "paid_providers_configured": [name for name, _ in endpoints if name in {"alchemy", "quicknode"}],
        "production_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in production},
        "cohorts": dict(Counter(row["category"] for row in cases)),
        "notes": ["Cold means empty application cache, not cleared upstream caches.",
                  "Real HTTP requests use the production handler and checker with an isolated SQLite file.",
                  "Benchmarked production source is unchanged throughout the run.",
                  "Detailed and basic requests use different application cache keys.",
                  "Additional warm passes exclude unknown responses because unknown results are not cacheable."]
    }
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    all_results = []
    shuffled = list(cases)
    random.Random(args.seed).shuffle(shuffled)
    with WalletHTTPServer(("127.0.0.1", 0), checker) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        metadata["local_api"] = base
        with (args.output / "results.jsonl").open("a", buffering=1) as output:
            def request(case, details, phase, repeat=0):
                query = urlencode({"proto": "eth", "address": case["address"], "details": str(details).lower()})
                event_start = len(meter.events)
                started = time.perf_counter()
                body, error, status = {}, None, None
                try:
                    try:
                        response = urlopen(base + "/api/analytics/address/check?" + query, timeout=180)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        status = response.status
                        body = json.load(response)
                except Exception as exc:
                    error = type(exc).__name__
                seconds = time.perf_counter() - started
                record = api_record(body)
                events = meter.events[event_start:]
                row = {
                    "id": case["id"], "category": case["category"], "label": case["label"],
                    "address": case["address"], "phase": phase, "details": details, "repeat": repeat,
                    "measured_at": stamp(), "latency_seconds": seconds, "http_status": status,
                    "transport_error": error, "response": body,
                    "upstream_http_requests": len(events),
                    "upstream_rpc_methods": sum(len(event["rpc_methods"]) for event in events),
                    "upstream_events": events,
                }
                if valid_address(case["address"]):
                    key = f"v1:ethereum:1:{case['address'].lower()}:details={int(details)}"
                    internal = cache.get(key)
                    if internal:
                        row["internal_provenance"] = {key: internal.get(key) for key in ["first_seen", "source_attempts"]}
                output.write(json.dumps(row, allow_nan=False) + "\n")
                all_results.append(row)
                return row

            for details, profile in [(True, "detailed"), (False, "basic")]:
                cold = {}
                for i, case in enumerate(shuffled, 1):
                    row = request(case, details, profile + "_cold")
                    cold[case["id"]] = row
                    if i % 10 == 0:
                        print(json.dumps({"phase": profile + "_cold", "completed": i,
                                          "last_status": row["http_status"], "last_seconds": round(row["latency_seconds"], 3)}), flush=True)
                    if row["upstream_http_requests"]:
                        time.sleep(args.pause)
                for i, case in enumerate(shuffled, 1):
                    request(case, details, profile + "_warm")
                print(json.dumps({"phase": profile + "_warm", "completed": len(shuffled)}), flush=True)
                for repeat in range(1, args.warm_repeats + 1):
                    for case in shuffled:
                        initial = cold[case["id"]]
                        if initial["http_status"] == 503 or initial["transport_error"]:
                            continue
                        request(case, details, profile + "_warm_extra", repeat)
                print(json.dumps({"phase": profile + "_warm_extra", "passes": args.warm_repeats}), flush=True)
        server.shutdown()
        thread.join(timeout=2)
    cache.close()
    metadata["finished_at"] = stamp()
    metadata["total_api_requests"] = len(all_results)
    metadata["total_upstream_http_requests"] = len(meter.events)
    metadata["total_rpc_methods"] = sum(len(event["rpc_methods"]) for event in meter.events)
    metadata["sources_http_requests"] = dict(Counter(event["source"] for event in meter.events))
    metadata["production_unchanged"] = all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        for name, digest in metadata["production_sha256"].items()
    )
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"complete": True, "requests": len(all_results), "output": str(args.output)}), flush=True)


if __name__ == "__main__":
    main()
