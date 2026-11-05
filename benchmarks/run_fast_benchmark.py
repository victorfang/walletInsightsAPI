#!/usr/bin/env python3
"""Measure the production factory and HTTP route with a fresh isolated cache.

This runner never submits transactions or records endpoint URLs/credentials.
The existing 100-input corpus and independent deployment controls are reused.
Baseline benchmark files are not modified. A fresh output directory is required.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eth_wallet_check.age_store import AgeStore
from eth_wallet_check.cache import SQLiteCache
from eth_wallet_check.config import rpc_endpoints
from eth_wallet_check.factory import build_ethereum_checker
from eth_wallet_check.http import JSONHTTP, SourceError
from eth_wallet_check.server import WalletHTTPServer
from analyze_benchmark import accuracy, cache as response_cache, latency, record, stats


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MeteredHTTP(JSONHTTP):
    """Keep production deadline support and redact private endpoint identifiers."""

    def __init__(self, timeout, endpoints):
        super().__init__(timeout=timeout)
        self.endpoints = {url: name for name, url in endpoints}
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, url, payload=None):
        parts = urlsplit(url)
        source = self.endpoints.get(url, {
            "eth.blockscout.com": "blockscout",
            "api.routescan.io": "routescan",
            "api.blockcypher.com": "blockcypher",
        }.get(parts.hostname, "other"))
        methods = ([item.get("method") for item in payload] if isinstance(payload, list) else
                   [payload.get("method")] if isinstance(payload, dict) else [])
        event = {"source": source, "rpc_methods": methods,
                 "action": parse_qs(parts.query).get("action", [None])[0],
                 "success": False, "finished": False}
        started = time.perf_counter()
        with self.lock:
            event["event_id"] = len(self.events)
            self.events.append(event)
        try:
            response = super().__call__(url, payload)
            with self.lock:
                event["success"] = True
                if isinstance(response, dict) and "status" in response:
                    event["api_status"] = response["status"]
                if isinstance(response, dict) and "error" in response:
                    event["rpc_error"] = True
            return response
        except Exception as exc:
            with self.lock:
                event["error"] = str(exc) if isinstance(exc, SourceError) else type(exc).__name__
            raise
        finally:
            with self.lock:
                event["seconds"] = time.perf_counter() - started
                event["finished"] = True

    def snapshot(self, start=0):
        with self.lock:
            return [dict(event) for event in self.events[start:]]


def age_status(row):
    value = stats(row)
    return value.get("age_status", row.get("response", {}).get("meta", {}).get("age", {}).get("status"))


def first_epoch(row):
    value = stats(row)
    return value.get("first_seen_timestamp", value.get("first_txn"))


def summarize(rows, cases, references, metadata):
    case_map = {case["id"]: case for case in cases}
    phases = {phase: [row for row in rows if row["phase"] == phase]
              for phase in dict.fromkeys(row["phase"] for row in rows)}
    metrics = {}
    for phase, selected in phases.items():
        valid = [row for row in selected if case_map[row["id"]]["expected_valid_format"]]
        used = [row for row in valid if case_map[row["id"]]["expected_has_appeared"] is True]
        hits = [row for row in valid if response_cache(row).get("hit") is True]
        age_hits = [row for row in valid if row.get("response", {}).get("meta", {}).get("age_cache", {}).get("hit") is True]
        metrics[phase] = {
            "requests": len(selected), "valid_requests": len(valid),
            "latency_all": latency([row["latency_seconds"] for row in selected]),
            "latency_valid": latency([row["latency_seconds"] for row in valid]),
            "latency_result_cache_hits": latency([row["latency_seconds"] for row in hits]),
            "result_cache_hits": len(hits),
            "permanent_age_cache_hits": len(age_hits),
            "over_3_seconds": sum(row["latency_seconds"] > 3 for row in selected),
            "http_statuses": dict(Counter(str(row["http_status"]) for row in selected)),
            "transport_errors": sum(row["transport_error"] is not None for row in selected),
            "upstream_call_attempts": sum(row["upstream_call_attempts"] for row in selected),
            "upstream_rpc_method_attempts": sum(row["upstream_rpc_method_attempts"] for row in selected),
            "accuracy": accuracy(selected, case_map),
            "known_used_age_coverage": {
                "eligible": len(used),
                "timestamp_returned": sum(type(first_epoch(row)) is int for row in used),
                "age_statuses": dict(Counter(str(age_status(row)) for row in used)),
            },
        }
    cold = {row["id"]: row for row in phases.get("detailed_cold", [])}
    controls = []
    for ref in references.get("deployment_controls", []):
        row = cold.get(ref["id"])
        if row is None:
            continue
        epoch = first_epoch(row)
        delta = epoch - ref["timestamp"] if type(epoch) is int else None
        controls.append({"id": ref["id"], "address": ref["address"], "label": ref.get("label"),
                         "reference_deployment_epoch": ref["timestamp"], "returned_epoch": epoch,
                         "age_status": age_status(row), "error_seconds": delta,
                         "assessment": ("missing" if delta is None else "agrees_with_deployment" if delta == 0 else
                                        "later_than_deployment" if delta > 0 else "earlier_than_deployment_ambiguous")})
    age_rows = [row for row in rows if type(first_epoch(row)) is int]
    age_formula_correct = sum(
        type(stats(row).get("age_as_of")) is int and
        stats(row).get("age_days") == max(0, (stats(row)["age_as_of"] - first_epoch(row)) // 86400)
        for row in age_rows
    )
    repeated_age_rows = [row for row in rows if row["phase"] != "detailed_cold"
                         and row["id"] in cold and type(first_epoch(row)) is int]
    storage = {}
    for name, filename in (("sqlite", "age-store-million.json"), ("python_dictionary", "age-dictionary-million.json")):
        path = ROOT / "benchmarks" / filename
        if path.exists():
            storage[name] = json.loads(path.read_text())
    baseline_path = ROOT / "benchmarks" / "run-2026-11-05" / "summary.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    baseline_timing = baseline.get("phase_metrics", {}).get("detailed_cold", {}).get("valid_latency_seconds", {})
    return {
        "generated_at": stamp(), "metadata": metadata, "phase_metrics": metrics,
        "deployment_controls": controls,
        "deployment_assessments": dict(Counter(item["assessment"] for item in controls)),
        "age_computation": {
            "timestamp_responses": len(age_rows), "age_days_formula_correct": age_formula_correct,
            "repeated_timestamp_responses": len(repeated_age_rows),
            "repeated_epoch_matches_cold": sum(first_epoch(row) == first_epoch(cold[row["id"]]) for row in repeated_age_rows),
        },
        "baseline_detailed_cold_valid_latency": baseline_timing,
        "storage_benchmarks": storage,
        "definitions": {
            "latency": "Client wall time over the actual local HTTP route, including handler, providers and serialization; pauses excluded.",
            "cold": "Empty application TTL and permanent-age stores; upstream provider caches are outside the benchmark's control.",
            "warm": "Same inputs after cold pass. Failed/unknown results can be retried and are not automatically cache hits.",
            "permanent_age": "TTL result entries deleted only from this run's isolated database; positive persisted addresses checked with details=false.",
            "accuracy": "Unknown/unusable is counted separately; synthetic absence is an expectation, not proof of complete history.",
            "age": "Returned observed timestamps and lower-bound ages are separate from exact-age claims. Deployment agreement is not universal first-appearance accuracy.",
            "upstream_call_attempts": "Calls entering the instrumented HTTP client, including deadline-rejected calls; not necessarily sockets opened.",
        },
    }


def seconds(value):
    return "—" if value is None else f"{value:.4f} s"


def write_report(path, summary, rows, cases):
    meta = summary["metadata"]
    metric = summary["phase_metrics"]
    lines = ["# Fast address-age API benchmark", "", f"Run completed: {meta.get('finished_at', 'incomplete')}", "",
             "The same 100-input corpus is exercised through the production Python factory and local HTTP endpoint. "
             "The result reports speed, usable coverage, and timestamp evidence separately.", "",
             f"Configured paid providers: {', '.join(meta['paid_providers_configured']) or 'none; public sources only'}.", "",
             "## Latency", "",
             "Valid-address timing excludes the 20 malformed inputs, which are rejected locally.", "",
             "| Phase | Valid requests | Median | p95 | Maximum | All requests over 3 s | Result-cache hits | Permanent-age hits |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for phase, values in metric.items():
        timing = values["latency_valid"]
        lines.append(f"| {phase} | {values['valid_requests']} | {seconds(timing['median'])} | "
                     f"{seconds(timing['p95'])} | {seconds(timing['max'])} | {values['over_3_seconds']} | "
                     f"{values['result_cache_hits']} | {values['permanent_age_cache_hits']} |")
    baseline = summary.get("baseline_detailed_cold_valid_latency", {})
    current = metric.get("detailed_cold", {}).get("latency_valid", {})
    if baseline.get("median") and current.get("median"):
        reduction = 100 * (1 - current["median"] / baseline["median"])
        lines += ["", f"Against the saved baseline's same 80 valid inputs, cold median fell from "
                  f"{seconds(baseline['median'])} to {seconds(current['median'])} ({reduction:.1f}% lower); "
                  f"maximum fell from {seconds(baseline['max'])} to {seconds(current['max'])}. "
                  "These are two sequential runs at different times, not a controlled simultaneous provider comparison."]
    lines += ["", "Maximum timing is measured for this run; it is not a guarantee of successful complete third-party data within three seconds. "
              "At the request deadline the service may return partial evidence or unavailable/unknown.", "",
              "## Classification and age coverage", "",
              "| Phase | Format judgments | Known-used correct / eligible | Known-used unknown | Synthetic agreement / eligible | Synthetic unknown | Known-used timestamps | Age statuses |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
    for phase, values in metric.items():
        a = values["accuracy"]
        used, synth = a["known_used_recall"], a["synthetic_candidate_agreement"]
        coverage = values["known_used_age_coverage"]
        lines.append(f"| {phase} | {a['format_correct']}/{a['unique_cases']} | {used['correct']}/{used['eligible']} | "
                     f"{used['unknown_or_unusable']} | {synth['correct']}/{synth['eligible']} | "
                     f"{synth['unknown_or_unusable']} | {coverage['timestamp_returned']}/{coverage['eligible']} | "
                     f"{json.dumps(coverage['age_statuses'], sort_keys=True)} |")
    lines += ["", "Synthetic addresses are deterministic fabricated 20-byte strings, without private keys. Their expected lack of activity is not absolute historical ground truth. "
              "The 40 existing controls have positive on-chain evidence. The corpus is a convenience sample and does not estimate performance across all Ethereum addresses.", "",
              "An observed timestamp provides a minimum age when history coverage is incomplete. A missing timestamp remains unknown. "
              "No wallet-key creation date can be inferred from a generated address.", "",
              "## Independent deployment controls", "",
              "References come from separately collected canonical deployment receipts/blocks and archive code checks. "
              "Matching a contract deployment timestamp measures deployment agreement; an address may have been funded or mentioned before deployment.", "",
              "| Assessment | Controls |", "|---|---:|"]
    for status, count in sorted(summary["deployment_assessments"].items()):
        lines.append(f"| {status} | {count} |")
    lines += ["", "| Contract | Returned epoch | Deployment epoch | Difference | Age status |",
              "|---|---:|---:|---:|---|"]
    for item in summary["deployment_controls"]:
        delta = item["error_seconds"]
        lines.append(f"| {item['label'] or item['id']} | {item['returned_epoch'] if item['returned_epoch'] is not None else 'unknown'} | "
                     f"{item['reference_deployment_epoch']} | {str(delta) + ' s' if delta is not None else 'unknown'} | {item['age_status']} |")
    lines += ["", "## Provider use and permanent storage", "",
              "| Phase | HTTP-client call attempts | RPC method attempts | HTTP statuses |",
              "|---|---:|---:|---|"]
    for phase, values in metric.items():
        lines.append(f"| {phase} | {values['upstream_call_attempts']} | {values['upstream_rpc_method_attempts']} | "
                     f"{json.dumps(values['http_statuses'], sort_keys=True)} |")
    lines += ["", f"Persisted age rows after cold/warm: {meta.get('persisted_age_rows', 0)}. "
              f"Permanent-age pass inputs: {meta.get('permanent_age_inputs', 0)}. "
              "That pass removes the isolated TTL cache entries before checking persisted positive addresses with details=false. "
              "Mutable balances/counts are not permanently cached.", "",
              "Call attempts include requests rejected before networking because their deadline had already expired. "
              "Timed-out source jobs can finish after the API response; complete upstream instrumentation is saved separately. "
              f"The global log contains {meta.get('total_upstream_call_attempts', 0)} attempts; "
              "per-request snapshots can omit attempts that start between response completion and the next request.", ""]
    checked = summary["age_computation"]
    lines += [f"AgeDays formula checks passed for {checked['age_days_formula_correct']}/{checked['timestamp_responses']} timestamp-bearing responses: "
              "`max(0, floor((age_as_of - first_txn) / 86400))`. "
              f"The stored first epoch stayed equal to its cold value for {checked['repeated_epoch_matches_cold']}/{checked['repeated_timestamp_responses']} repeated responses.", "",
              "The permanent cache preserves the earliest finalized observation it knows; it does not automatically rescan partial history. "
              "A later verified earlier observation can correct the minimum through `observe()`. "
              "Until then an incomplete record continues to return `lower_bound`, even though its timestamp never expires.", ""]
    storage = summary.get("storage_benchmarks", {})
    if "sqlite" in storage and "python_dictionary" in storage:
        db, dictionary = storage["sqlite"], storage["python_dictionary"]
        mib = lambda size: size / (1024 * 1024)
        lines += ["## One-million-address storage experiment", "",
                  "These separate offline experiments used actual one-million-row stores, not estimates from a tiny sample. "
                  "Synthetic records require no provider calls or private keys.", "",
                  "| Storage | Process peak RSS | Additional peak RSS | Durable disk file | Lookup median | Lookup p95 |",
                  "|---|---:|---:|---:|---:|---:|",
                  f"| SQLite, production provenance schema | {mib(db['peak_rss_after_bytes']):.2f} MiB | "
                  f"{mib(db['additional_peak_rss_bytes']):.2f} MiB | {mib(db['database_bytes']):.2f} MiB | "
                  f"{db['lookup_seconds']['median'] * 1000:.3f} ms | {db['lookup_seconds']['p95'] * 1000:.3f} ms |",
                  f"| Python dictionary, tuple key + integer epoch | {mib(dictionary['peak_rss_after_bytes']):.2f} MiB | "
                  f"{mib(dictionary['additional_peak_rss_bytes']):.2f} MiB | Not persisted | "
                  f"{dictionary['lookup_seconds']['median'] * 1_000_000:.3f} µs | {dictionary['lookup_seconds']['p95'] * 1_000_000:.3f} µs |", "",
                  "SQLite stores protocol, network, address, epoch and provenance/finality/scope metadata; the dictionary stores only a shared chain string, "
                  "address, tuple and epoch. Their contents differ. Dictionary lookups exclude constructing the lookup key. "
                  "SQLite lookup timings call the production `get()` method after population with a warm OS filesystem cache. "
                  "RSS excludes OS filesystem-cache accounting; SQLite disk size is not RAM use. "
                  "SQLite population uses bulk inserts, so its load time is not production `observe()` throughput.", "",
                  "The SQLite approach keeps the million records on disk with a bounded connection page cache, survives restarts, "
                  "and avoids loading the full Python dictionary. "
                  "Raw measurements: [SQLite](../age-store-million.json), [Python dictionary](../age-dictionary-million.json).", ""]
    lines += ["The [service README](../../README.md) documents the current cache/deadline policy and official API research. "
              "The age semantics follow [first activity](https://docs.moralis.com/data-api/evm/wallet/wallet-insights), "
              "while permanent Ethereum observations require [finalized canonical blocks](https://ethereum.org/developers/docs/consensus-mechanisms/pos/). "
              "[SQLite cache sizing](https://www.sqlite.org/pragma.html#pragma_cache_size) controls its connection page cache.", "",
              "## Reproducibility and limits", "",
              f"- Dataset SHA-256: `{meta['dataset_sha256']}`.",
              f"- Python: `{meta['python'].splitlines()[0]}`.",
              f"- Sequential HTTP requests; pause between cold requests: {meta['inter_cold_request_pause_seconds']} s, excluded from latency.",
              f"- Production files unchanged during run: {meta.get('production_unchanged')}.",
              *([f"- Validation: {meta['validation_note']}"] if meta.get("validation_note") else []),
              "- Raw responses: `results.jsonl`; aggregate metrics: `summary.json`; upstream attempts: `upstream-events.json`.",
              "- The reference corpus is reused from the baseline; this run does not perform new independent balance verification.",
              "- A deadline bounds waiting, not upstream availability. Unknown and partial answers are retained in reported denominators.", "",
              "## Per-input results", "",
              "| ID | Category | Cold seconds | Warm seconds | HTTP | Seen | First epoch | Age status |",
              "|---|---|---:|---:|---:|---|---:|---|"]
    cold = {row["id"]: row for row in rows if row["phase"] == "detailed_cold"}
    warm = {row["id"]: row for row in rows if row["phase"] == "detailed_warm"}
    for case in cases:
        row = cold.get(case["id"], {})
        value = stats(row)
        lines.append(f"| {case['id']} | {case['category']} | {seconds(row.get('latency_seconds'))} | "
                     f"{seconds(warm.get(case['id'], {}).get('latency_seconds'))} | {row.get('http_status')} | "
                     f"{value.get('has_appeared')} | {first_epoch(row)} | {age_status(row)} |")
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks" / "addresses.json")
    parser.add_argument("--references", type=Path, default=ROOT / "benchmarks" / "references.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=2.6)
    parser.add_argument("--pause", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=20261105)
    parser.add_argument("--validation-note", help="Optional independently verified validation note for the report metadata")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    cases = dataset["cases"] if isinstance(dataset, dict) else dataset
    if len(cases) != 100 or len({row["address"] for row in cases}) != 100:
        parser.error("Expected 100 distinct corpus inputs")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output directory must be fresh and empty")
    args.output.mkdir(parents=True, exist_ok=True)
    references = json.loads(args.references.read_text()) if args.references.exists() else {}
    endpoints = rpc_endpoints()
    meter = MeteredHTTP(args.timeout, endpoints)
    cache_path = args.output / "cache.sqlite3"
    age_path = args.output / "first-seen.sqlite3"
    result_cache = SQLiteCache(cache_path)
    age_store = AgeStore(age_path)
    checker = build_ethereum_checker(meter, result_cache, age_store=age_store)
    production = sorted((ROOT / "eth_wallet_check").glob("*.py")) + [ROOT / "openapi.json"]
    metadata = {
        "started_at": stamp(), "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "python": sys.version, "cases": len(cases), "seed": args.seed, "concurrency": 1,
        "upstream_timeout_seconds": args.timeout, "inter_cold_request_pause_seconds": args.pause,
        "provider_order": [name for name, _ in endpoints],
        "paid_providers_configured": [name for name, _ in endpoints if name in {"alchemy", "quicknode"}],
        "production_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in production},
        "cohorts": dict(Counter(row["category"] for row in cases)),
    }
    if args.validation_note:
        metadata["validation_note"] = args.validation_note
    metadata_path = args.output / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    rows = []
    shuffled = list(cases)
    random.Random(args.seed).shuffle(shuffled)
    with WalletHTTPServer(("127.0.0.1", 0), checker) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        with (args.output / "results.jsonl").open("a", buffering=1) as output:
            def request(case, phase, details):
                query = urlencode({"proto": "eth", "address": case["address"], "details": str(details).lower()})
                event_start = len(meter.snapshot())
                start = time.perf_counter()
                body, error, http_status = {}, None, None
                try:
                    try:
                        response = urlopen(base + "/api/analytics/address/check?" + query, timeout=6)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        http_status = response.status
                        body = json.load(response)
                except Exception as exc:
                    error = type(exc).__name__
                duration = time.perf_counter() - start
                events = meter.snapshot(event_start)
                row = {
                    "id": case["id"], "category": case["category"], "label": case["label"],
                    "address": case["address"], "phase": phase, "details": details,
                    "measured_at": stamp(), "latency_seconds": duration,
                    "http_status": http_status, "transport_error": error, "response": body,
                    "upstream_call_attempts": len(events),
                    "upstream_rpc_method_attempts": sum(len(event["rpc_methods"]) for event in events),
                    "upstream_events": events,
                }
                output.write(json.dumps(row, allow_nan=False) + "\n")
                rows.append(row)
                return row

            for phase in ("detailed_cold", "detailed_warm"):
                for index, case in enumerate(shuffled, 1):
                    row = request(case, phase, details=True)
                    if index % 10 == 0:
                        print(json.dumps({"phase": phase, "completed": index,
                                          "last_status": row["http_status"],
                                          "last_seconds": round(row["latency_seconds"], 4)}), flush=True)
                    if phase == "detailed_cold" and row["upstream_call_attempts"]:
                        time.sleep(args.pause)
            persisted = [case for case in cases if case["expected_valid_format"]
                         and age_store.get("eth", "mainnet", case["address"].lower())]
            metadata["persisted_age_rows"] = len(persisted)
            metadata["permanent_age_inputs"] = len(persisted)
            # The only database changed here belongs to this new benchmark run.
            with sqlite3.connect(cache_path) as db:
                db.execute("DELETE FROM cache_entries")
            for index, case in enumerate(persisted, 1):
                request(case, "permanent_age", details=False)
            print(json.dumps({"phase": "permanent_age", "completed": len(persisted)}), flush=True)
        server.shutdown()
        thread.join(timeout=2)
    # A response deadline does not forcibly terminate running source threads.
    # Allow their bounded HTTP calls to finish before saving final instrumentation.
    settle_deadline = time.monotonic() + 3
    while any(not event["finished"] for event in meter.snapshot()) and time.monotonic() < settle_deadline:
        time.sleep(0.05)
    metadata["finished_at"] = stamp()
    metadata["total_api_requests"] = len(rows)
    metadata["total_upstream_call_attempts"] = len(meter.snapshot())
    metadata["unfinished_upstream_calls"] = sum(not event["finished"] for event in meter.snapshot())
    metadata["production_unchanged"] = all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        for name, digest in metadata["production_sha256"].items()
    )
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    (args.output / "upstream-events.json").write_text(json.dumps(meter.snapshot(), indent=2) + "\n")
    summary = summarize(rows, cases, references, metadata)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    write_report(args.output / "REPORT.md", summary, rows, cases)
    result_cache.close()
    age_store.close()
    print(json.dumps({"complete": True, "requests": len(rows), "report": str(args.output / "REPORT.md"),
                      "over_3_seconds": sum(row["latency_seconds"] > 3 for row in rows)}), flush=True)


if __name__ == "__main__":
    main()
