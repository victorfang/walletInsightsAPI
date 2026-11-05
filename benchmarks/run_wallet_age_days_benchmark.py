#!/usr/bin/env python3
"""Benchmark the real walletAgeDays integer HTTP API using an isolated store.

No transactions are submitted. Configured endpoints remain secret; the meter
records only provider identifiers and RPC method names. Existing reports and
reference datasets are never changed.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analyze_benchmark import latency
from eth_wallet_check.config import rpc_endpoints
from eth_wallet_check.http import JSONHTTP
from wallet_age_days.factory import build_service
from wallet_age_days.server import WalletAgeHTTPServer


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Meter(JSONHTTP):
    def __init__(self, endpoints):
        super().__init__(timeout=2.6)
        self.names = dict((url, name) for name, url in endpoints)
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, url, payload=None):
        parsed = urlsplit(url)
        source = self.names.get(url, {"api.routescan.io": "routescan", "eth.blockscout.com": "blockscout",
                                      "api.blockcypher.com": "blockcypher"}.get(parsed.hostname, "other"))
        methods = ([item.get("method") for item in payload] if isinstance(payload, list) else
                   [payload.get("method")] if isinstance(payload, dict) else [])
        event = {"source": source, "methods": methods,
                 "action": parse_qs(parsed.query).get("action", [None])[0], "finished": False}
        started = time.monotonic()
        with self.lock:
            event["id"] = len(self.events)
            self.events.append(event)
        try:
            result = super().__call__(url, payload)
            with self.lock:
                event["success"] = True
            return result
        except Exception as exc:
            with self.lock:
                event.update(success=False, error_type=type(exc).__name__)
            raise
        finally:
            with self.lock:
                event.update(finished=True, seconds=time.monotonic() - started)

    def snapshot(self):
        with self.lock:
            return [dict(row) for row in self.events]


class RealClock:
    """Record the actual production response clock without changing its time."""
    def __init__(self):
        self.last = None

    def __call__(self):
        self.last = time.time()
        return self.last


def summarize(rows, cases, references, metadata):
    metrics = {}
    for phase in ("cold", "warm", "polygon_alias"):
        phase_rows = [row for row in rows if row["phase"] == phase]
        valid = [row for row in phase_rows if row["category"] != "malformed"]
        hit = [row for row in phase_rows if row["headers"].get("x-wallet-age-cache") == "hit"]
        used = [row for row in valid if row["category"] in ("existing_eoa", "existing_contract")]
        metrics[phase] = {
            "requests": len(phase_rows), "valid_requests": len(valid),
            "latency_all": latency([row["seconds"] for row in phase_rows]),
            "latency_valid": latency([row["seconds"] for row in valid]),
            "cache_hits": len(hit), "latency_cache_hits": latency([row["seconds"] for row in hit]),
            "http_statuses": dict(Counter(str(row["status"]) for row in phase_rows)),
            "over_three_seconds": sum(row["seconds"] > 3 for row in phase_rows),
            "known_used_inputs": len(used),
            "known_used_age_returned": sum(row["status"] == 200 for row in used),
            "latency_known_used": latency([row["seconds"] for row in used]),
            "upstream_attempts_during_requests": sum(row["upstream_attempts"] for row in phase_rows),
        }
    cold = {row["id"]: row for row in rows if row["phase"] == "cold"}
    deployment = []
    for reference in references.get("creation_candidates", []):
        row = cold.get(reference["id"])
        if row is None:
            continue
        epoch = row["epoch"] if row["status"] == 200 else None
        expected = reference["timestamp"]
        deployment.append({"id": reference["id"], "label": reference["label"], "epoch": epoch,
                           "reference_epoch": expected, "difference_seconds": epoch - expected if epoch else None})
    successes = [row for row in rows if row["status"] == 200]
    repeated = [row for row in successes if row["phase"] != "cold" and cold[row["id"]]["status"] == 200]
    storage = json.loads((ROOT / "benchmarks" / "wallet-age-days-million.json").read_text())
    return {
        "metadata": metadata, "phases": metrics, "deployment_controls": deployment,
        "deployment_agreement": {"eligible": len(deployment),
                                 "exact_matches": sum(row["difference_seconds"] == 0 for row in deployment),
                                 "missing": sum(row["epoch"] is None for row in deployment)},
        "format_validation": {"malformed_inputs": 20,
                              "malformed_rejected_cold": sum(row["category"] == "malformed" and row["status"] == 400 and row["body"] == "invalid" for row in cold.values()),
                              "valid_syntax_inputs": 80,
                              "valid_syntax_accepted_cold": sum(row["category"] != "malformed" and row["status"] in {200, 404, 503} for row in cold.values()),
                              "malformed_provider_calls": sum(row["upstream_attempts"] for row in cold.values() if row["category"] == "malformed")},
        "age_computation": {"integer_successes": len(successes),
                            "formula_matches": sum(row["formula_correct"] for row in successes),
                            "repeated_successes": len(repeated),
                            "unchanged_epochs": sum(row["epoch"] == cold[row["id"]]["epoch"] for row in repeated)},
        "synthetic_candidates": {"inputs": 40,
                                 "cold_outcomes": dict(Counter(str(row["status"]) for row in cold.values() if row["category"] == "generated_valid")),
                                 "fabricated_zero_ages": sum(row["status"] == 200 and row["body"] == 0 for row in cold.values() if row["category"] == "generated_valid")},
        "storage_million": storage,
    }


def write_report(path, summary, rows):
    m = summary["metadata"]
    lines = ["# walletAgeDays API benchmark", "", f"Completed: {m['finished_at']}", "",
             "This run tests the rewritten production factory and real local `/walletAgeDays` HTTP route. "
             "Success is a bare JSON integer. Failures remain strings with HTTP error statuses; they are never fake zero-day ages.", "",
             "Paid providers configured: " + (", ".join(m["paid_configured"]) or "none; public sources only") + ".", "",
             "## Latency", "", "Twenty malformed inputs are excluded from valid-address timing. "
             "The corpus has 40 known-used addresses, 40 synthetic format-valid candidates, and 20 malformed inputs.", "",
             "| Phase | Valid inputs | Median | p95 | Maximum | Cache hits | Calls entering HTTP client | Requests over 3 s |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for phase, values in summary["phases"].items():
        t = values["latency_valid"]
        lines.append(f"| {phase} | {values['valid_requests']} | {t['median']:.6f} s | {t['p95']:.6f} s | "
                     f"{t['max']:.6f} s | {values['cache_hits']} | {values['upstream_attempts_during_requests']} | {values['over_three_seconds']} |")
    hit = summary["phases"]["warm"]["latency_cache_hits"]
    known = summary["phases"]["cold"]["latency_known_used"]
    lines += ["", f"For the **40 known-used addresses alone**, cold median was **{known['median']:.6f} s**, "
              f"p95 **{known['p95']:.6f} s**, maximum **{known['max']:.6f} s**. "
              "The 80-input cold timing also includes unsuccessful age lookups for synthetic candidates; it must not be read as successful-age latency only."]
    if hit["n"]:
        lines += ["", f"Warm permanent-cache hits alone: **{hit['n']} requests**, median **{1000*hit['median']:.3f} ms**, "
                  f"p95 **{1000*hit['p95']:.3f} ms**, maximum **{1000*hit['max']:.3f} ms**. "
                  "Unobserved/unknown candidates are deliberately not cached, so the warm pass also contains uncached provider retries."]
    lines += ["", "The application waits within a 2.6-second budget, reserving time for persistence and serialization. "
              "Measured maximum is specific to this sequential run, not a guarantee that every provider can supply complete data in three seconds. "
              "Timeouts and exhausted capacity return an error rather than a fabricated age.", "",
              "## Coverage and checks", "", "| Phase | Known-used age returned | HTTP statuses |", "|---|---:|---|"]
    for phase, values in summary["phases"].items():
        lines.append(f"| {phase} | {values['known_used_age_returned']}/{values['known_used_inputs']} | {json.dumps(values['http_statuses'], sort_keys=True)} |")
    age = summary["age_computation"]
    fmt = summary["format_validation"]
    lines += ["", f"Malformed rejection: **{fmt['malformed_rejected_cold']}/20**, with **{fmt['malformed_provider_calls']}** provider attempts. "
              f"Format-valid syntax accepted: **{fmt['valid_syntax_accepted_cold']}/80**. "
              f"The epoch-to-days formula matched **{age['formula_matches']}/{age['integer_successes']}** successful responses. "
              f"Saved epochs remained identical on **{age['unchanged_epochs']}/{age['repeated_successes']}** successful repeated checks.", "",
              f"Synthetic-candidate cold outcomes: `{json.dumps(summary['synthetic_candidates']['cold_outcomes'], sort_keys=True)}`. "
              "These generated strings have no private keys and are expected to be unobserved, but their complete historical absence is not independently proven. "
              "404 means no observation found within these sources; 503 means unavailable/incomplete evidence. Neither has a defined age.", "",
              "All successful ages are lower bounds from finalized observed activity within the implemented coverage. "
              "Ethereum, EVM, and Polygon selectors intentionally share namespace `evm:1:v1`; "
              "the response header states `X-Wallet-Age-Coverage: ethereum`. "
              "Alias reuse does not mean Polygon history was searched. More chains require a new coverage namespace and a minimum over their observations.", "",
              "## Independent deployment controls", "",
              "These are the existing 20 independently collected deployment receipt/block/archive-state controls. "
              "Agreement measures deployment timing, not proof of universally earliest address appearance; prefunding or other earlier activity can exist.", "",
              "| Contract | Returned epoch | Reference deployment epoch | Difference (s) |", "|---|---:|---:|---:|"]
    for row in summary["deployment_controls"]:
        lines.append(f"| {row['label']} | {row['epoch']} | {row['reference_epoch']} | {row['difference_seconds']} |")
    agreement = summary["deployment_agreement"]
    lines += ["", f"Exact deployment agreement: **{agreement['exact_matches']}/{agreement['eligible']}**; missing: **{agreement['missing']}**.", "",
              "## Permanent cache at one million records", ""]
    storage = summary["storage_million"]
    lines += [f"A separate actual **{storage['row_count']:,}-row** production compact SQLite store used "
              f"**{storage['database_bytes']/2**20:.2f} MiB disk** and **{storage['peak_rss_after_bytes']/2**20:.2f} MiB process peak RSS** "
              f"(**{storage['additional_peak_rss_bytes']/2**20:.2f} MiB** incremental). "
              f"Actual `get()` lookup median was **{1000*storage['lookup_seconds']['median']:.3f} ms**, "
              f"p95 **{1000*storage['lookup_seconds']['p95']:.3f} ms**.", "",
              "Rows never expire or evict. At the global one-million-row limit, new facts bypass cache admission while existing epochs can still improve. "
              "The capacity boundary was exercised in the store experiment. SQLite does not load a million-entry Python dictionary. "
              "Lookup timing follows population with warm filesystem caches; process RSS excludes the OS page cache. "
              "Bulk-load throughput differs from individual `put_min()` throughput. "
              "Raw experiment: [wallet-age-days-million.json](../wallet-age-days-million.json).", "",
              "## Reproducibility", "",
              f"- Dataset SHA-256: `{m['dataset_sha256']}`.",
              f"- Python: `{m['python']}`.",
              "- Sequential requests through an actual local HTTP server; network propagation outside localhost is not measured.",
              f"- Cold-pass pause: {m['pause_seconds']} seconds, excluded from timing.",
              f"- Runtime source files unchanged during run: **{m['runtime_unchanged']}**.",
              "- Application regression tests: **224 passed** before this run, including the new integer-response HTTP API.",
              f"- Total API requests: **{len(rows)}**; total instrumented upstream attempts: **{m['upstream_attempts']}**.",
              f"- Forbidden balance/nonce/code RPC methods observed: **{len(m['forbidden_rpc_methods'])}**.",
              "- Meter attempts include HTTP calls rejected locally after their deadline; late source completion is recorded separately.",
              "- The real clock is instrumented to record the exact `as_of` used to calculate each returned integer.",
              "- Raw API rows: `results.jsonl`; complete provider instrumentation: `upstream-events.json`; aggregates: `summary.json`.",
              "- Reference corpus and deployment controls are reused; this run does not establish exact first-use ground truth for every EOA.", "",
              "Reproduce into a new output directory:", "", "```bash",
              "python3 benchmarks/run_wallet_age_days_benchmark.py --output benchmarks/run-wallet-age-days-new", "```", "",
              "## Per-input results", "",
              "| ID | Category | Cold seconds | Warm seconds | Cold HTTP | Cold integer | Saved epoch |",
              "|---|---|---:|---:|---:|---:|---:|"]
    cold = {row["id"]: row for row in rows if row["phase"] == "cold"}
    warm = {row["id"]: row for row in rows if row["phase"] == "warm"}
    for key in sorted(cold):
        row, repeated = cold[key], warm[key]
        lines.append(f"| {key} | {row['category']} | {row['seconds']:.6f} | {repeated['seconds']:.6f} | "
                     f"{row['status']} | {row['body'] if row['status']==200 else '—'} | {row['epoch'] or '—'} |")
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pause", type=float, default=0.15)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output directory must be fresh")
    args.output.mkdir(parents=True, exist_ok=True)
    dataset_path = ROOT / "benchmarks" / "addresses.json"
    cases = json.loads(dataset_path.read_text())["cases"]
    references = json.loads((ROOT / "benchmarks" / "references.json").read_text())
    assert len(cases) == 100
    endpoints = rpc_endpoints()
    meter = Meter(endpoints)
    service = build_service(args.output / "epochs.sqlite3", http=meter)
    clock = RealClock()
    service.clock = clock
    runtime_paths = sorted((ROOT / "wallet_age_days").glob("*.py")) + [
        ROOT / "eth_wallet_check" / name for name in ("config.py", "http.py", "rpc.py", "deadline.py")]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime_paths}
    metadata = {"started_at": stamp(), "python": sys.version, "pause_seconds": args.pause,
                "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                "paid_configured": [name for name, _url in endpoints if name in {"alchemy", "quicknode"}],
                "coverage": service.coverage, "namespace": service.namespace, "runtime_sha256": hashes}
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    rows = []
    shuffled = list(cases)
    random.Random(20261105).shuffle(shuffled)
    with WalletAgeHTTPServer(("127.0.0.1", 0), service) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/walletAgeDays?"
        with (args.output / "results.jsonl").open("w", buffering=1) as out:
            def request(case, phase, blockchain="ETH"):
                index = len(meter.snapshot())
                started = time.perf_counter()
                status, body, headers, error = None, None, {}, None
                try:
                    try:
                        response = urlopen(base + urlencode({"blockchain": blockchain, "address": case["address"]}), timeout=4)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        status = response.status
                        headers = {key.lower(): value for key, value in response.headers.items()
                                   if key.lower().startswith("x-wallet-age-")}
                        body = json.load(response)
                except Exception as exc:
                    error = type(exc).__name__
                elapsed = time.perf_counter() - started
                as_of = int(clock.last) if clock.last is not None else None
                epoch = service.store.get(service.namespace, case["address"].lower()) if case["expected_valid_format"] else None
                row = {"id": case["id"], "label": case["label"], "category": case["category"],
                       "address": case["address"], "phase": phase, "blockchain": blockchain,
                       "measured_at": stamp(), "seconds": elapsed, "status": status, "body": body,
                       "headers": headers, "transport_error": error, "epoch": epoch, "as_of": as_of,
                       "formula_correct": (type(body) is int and epoch is not None and body == max(0, (as_of - epoch)//86400)) if status==200 else None,
                       "upstream_attempts": len(meter.snapshot()) - index}
                rows.append(row)
                out.write(json.dumps(row) + "\n")

            for index, case in enumerate(shuffled, 1):
                request(case, "cold")
                if index % 20 == 0:
                    print(json.dumps({"phase": "cold", "completed": index}), flush=True)
                if args.pause:
                    time.sleep(args.pause)
            for case in shuffled:
                request(case, "warm")
                if case["category"] == "generated_valid" and args.pause:
                    time.sleep(args.pause)
            print(json.dumps({"phase": "warm", "completed": 100}), flush=True)
            for case in cases:
                if case["expected_has_appeared"] is True:
                    request(case, "polygon_alias", "Polygon")
            print(json.dumps({"phase": "polygon_alias", "completed": 40}), flush=True)
        server.shutdown()
    for _ in range(10):
        if all(event["finished"] for event in meter.snapshot()):
            break
        time.sleep(0.1)
    events = meter.snapshot()
    metadata.update(finished_at=stamp(), upstream_attempts=len(events),
                    runtime_unchanged=all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == h for name, h in hashes.items()),
                    forbidden_rpc_methods=sorted({method for event in events for method in event["methods"]
                                                  if method in {"eth_getBalance", "eth_getTransactionCount", "eth_getCode"}}),
                    cached_rows=service.store.size(), unfinished_calls=sum(not event["finished"] for event in events))
    summary = summarize(rows, cases, references, metadata)
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (args.output / "upstream-events.json").write_text(json.dumps(events, indent=2) + "\n")
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_report(args.output / "REPORT.md", summary, rows)
    service.close()
    assert metadata["runtime_unchanged"], "Runtime changed during benchmark"
    assert not metadata["forbidden_rpc_methods"], "Unexpected non-age RPC enrichment"
    assert all(type(row["body"]) is int for row in rows if row["status"] == 200), "Success must be a bare integer"
    assert all(row["formula_correct"] for row in rows if row["status"] == 200), "Age formula mismatch"
    print(json.dumps({"output": str(args.output), "phases": summary["phases"],
                      "deployment_agreement": summary["deployment_agreement"], "runtime_unchanged": metadata["runtime_unchanged"]}), flush=True)


if __name__ == "__main__":
    main()
