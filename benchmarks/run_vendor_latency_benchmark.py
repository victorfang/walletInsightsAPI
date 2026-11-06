#!/usr/bin/env python3
"""Read-only independent vendor latency samples; never writes credentials/URLs.

RPC verification and history discovery are intentionally different cohorts.
No service cache is used. Twenty real addresses from the fixed corpus are used
per provider; upstream providers may retain their own caches.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eth_wallet_check.http import JSONHTTP, SourceError
from wallet_age_days.config import settings


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def latency(values):
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    values = sorted(values)
    return {"n": len(values), "p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(values[max(0, math.ceil(.95 * len(values)) - 1)], 3),
            "max_ms": round(values[-1], 3)}


def json_rpc_error(data):
    """Return a fixed classification only; provider text can include secrets."""
    message = ""
    if isinstance(data, dict):
        message = " ".join(str(data.get(k, "")) for k in ("message", "result", "error")).lower()
    if any(x in message for x in ("rate limit", "rate-limit", "too many requests", "throttl", "compute units per second")):
        return "rate_limited"
    if any(x in message for x in ("free api access", "upgrade", "subscription", "paid plan", "not supported for this chain")):
        return "plan_restricted"
    if any(x in message for x in ("api key", "apikey", "unauthorized", "authentication", "invalid token")):
        return "authentication_error"
    return "provider_error"


def unix_time(value):
    if not isinstance(value, str):
        return None
    try:
        value = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        return value if value > 0 else None
    except (ValueError, OverflowError):
        return None


def normal_history(data, address):
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        return {"outcome": json_rpc_error(data)}
    rows = data["result"]
    if not rows:
        if str(data.get("status")) == "1" or str(data.get("message", "")).lower() in {
                "no transactions found", "no records found", "no data found"}:
            return {"outcome": "empty_history"}
        return {"outcome": json_rpc_error(data)}
    row = rows[0]
    if str(data.get("status")) != "1" or not isinstance(row, dict):
        return {"outcome": "invalid_payload"}
    matched = any(isinstance(row.get(k), str) and row[k].lower() == address for k in ("from", "to", "contractAddress"))
    try:
        epoch = int(row["timeStamp"])
        block = int(row["blockNumber"])
    except (ValueError, TypeError, KeyError):
        return {"outcome": "invalid_payload"}
    if not matched or epoch <= 0 or block < 0 or not isinstance(row.get("hash"), str) or len(row["hash"]) != 66:
        return {"outcome": "invalid_payload"}
    return {"outcome": "usable_candidate", "candidate_epoch": epoch, "candidate_block": block}


def alchemy_history(data, address):
    if not isinstance(data, list):
        return {"outcome": json_rpc_error(data)}
    by_id = {row.get("id"): row for row in data if isinstance(row, dict)}
    if set(by_id) != {1, 2}:
        return {"outcome": "invalid_payload"}
    epochs = []
    candidates = 0
    for index, direction in ((1, "from"), (2, "to")):
        row = by_id[index]
        if "error" in row:
            return {"outcome": json_rpc_error(row)}
        result = row.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("transfers"), list):
            return {"outcome": "invalid_payload"}
        transfers = result["transfers"]
        if transfers:
            entry = transfers[0]
            if not isinstance(entry, dict) or str(entry.get(direction, "")).lower() != address:
                return {"outcome": "invalid_payload"}
            if not isinstance(entry.get("hash"), str) or len(entry["hash"]) != 66:
                return {"outcome": "invalid_payload"}
            candidates += 1
            metadata = entry.get("metadata")
            epoch = unix_time(metadata.get("blockTimestamp")) if isinstance(metadata, dict) else None
            if epoch:
                epochs.append(epoch)
    if not candidates:
        return {"outcome": "empty_history"}
    return {"outcome": "usable_candidate", "candidate_epoch": min(epochs) if epochs else None,
            "candidate_directions": candidates, "timestamp_needs_rpc": not bool(epochs)}


def blockcypher_history(data, address):
    if not isinstance(data, dict):
        return {"outcome": "invalid_payload"}
    if "error" in data:
        return {"outcome": json_rpc_error(data)}
    if str(data.get("address", "")).lower().removeprefix("0x") != address.removeprefix("0x"):
        return {"outcome": "invalid_payload"}
    rows = data.get("txrefs", [])
    count = data.get("n_tx")
    if not isinstance(rows, list) or type(count) is not int or count < 0:
        return {"outcome": "invalid_payload"}
    if data.get("hasMore") is True or count > len(rows):
        return {"outcome": "truncated_history"}
    if not rows:
        return {"outcome": "empty_history"}
    epochs = [unix_time(row.get("confirmed")) for row in rows if isinstance(row, dict)]
    if len(epochs) != len(rows) or any(v is None for v in epochs):
        return {"outcome": "invalid_payload"}
    return {"outcome": "usable_candidate", "candidate_epoch": min(epochs)}


def rpc_result(data, tx_hash):
    if not isinstance(data, list):
        return {"outcome": json_rpc_error(data)}
    by_id = {row.get("id"): row for row in data if isinstance(row, dict)}
    if set(by_id) != {1, 2, 3}:
        return {"outcome": "invalid_payload"}
    if any("error" in row for row in by_id.values()):
        return {"outcome": json_rpc_error(next(row for row in by_id.values() if "error" in row))}
    try:
        chain_id = int(by_id[1]["result"], 16)
        receipt = by_id[2]["result"]
        head = by_id[3]["result"]
        if chain_id != 1:
            return {"outcome": "wrong_chain"}
        if not isinstance(receipt, dict) or not isinstance(head, dict):
            return {"outcome": "invalid_payload"}
        if str(receipt.get("transactionHash", "")).lower() != tx_hash.lower():
            return {"outcome": "invalid_payload"}
        number = int(receipt["blockNumber"], 16)
        finalized = int(head["number"], 16)
        if number <= 0 or finalized < number or not isinstance(receipt.get("blockHash"), str):
            return {"outcome": "invalid_payload"}
    except (KeyError, TypeError, ValueError):
        return {"outcome": "invalid_payload"}
    return {"outcome": "usable_rpc_response", "chain_id": chain_id,
            "receipt_block": number, "finalized_height": finalized}


def provider_specs(config):
    specs = []
    endpoints = dict(config["endpoints"])
    if "alchemy" in endpoints:
        specs.append(("alchemy_history", "history", endpoints["alchemy"], 0.0))
    if config["etherscan_key"]:
        specs.append(("etherscan_history", "history", "https://api.etherscan.io/v2/api", .55))
    specs.extend([
        ("routescan_history", "history", "https://api.routescan.io/v2/network/mainnet/evm/1/etherscan", .55),
        ("blockscout_history", "history", "https://eth.blockscout.com/api", .30),
        ("blockcypher_history", "history", "https://api.blockcypher.com/v1/eth/main/addrs/", .55),
    ])
    for name in ("alchemy", "quicknode", "publicnode", "1rpc", "blockpi"):
        if name in endpoints:
            specs.append((name + "_rpc", "verification_rpc", endpoints[name], .05))
    return specs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--output", default=str(ROOT / "benchmarks/run-provider-latency-2026-11-05"))
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    if not 1 <= args.samples <= 20 or not 0 < args.timeout <= 5:
        parser.error("samples must be 1..20 and timeout <=5 seconds")
    network_lock = open("/private/tmp/wallet-age-benchmark-network.lock", "a")
    fcntl.flock(network_lock, fcntl.LOCK_EX)
    print("Vendor benchmark acquired shared network lock.", flush=True)
    config = settings(args.env_file)
    corpus_path = ROOT / "benchmarks/addresses.json"
    corpus = json.loads(corpus_path.read_text())
    cases = ([r for r in corpus["cases"] if r["category"] == "existing_eoa"][:10] +
             [r for r in corpus["cases"] if r["category"] == "existing_contract"][:10])[:args.samples]
    references = json.loads((ROOT / "benchmarks/references.json").read_text())
    creation = {r["id"]: r["transaction_hash"] for r in references["creation_candidates"]}
    fixed_tx = "0x9b629147b75dc0b275d478fa34d97c5d4a26926457540b15a5ce871df36c23fd"
    for case in cases:
        case["benchmark_transaction"] = case.get("proof", {}).get("transaction_hash") or creation.get(case["id"]) or fixed_tx
    specs = provider_specs(config)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    semaphore = threading.BoundedSemaphore(6)
    lock = threading.Lock()
    results = []
    started_at, wall_start = stamp(), time.perf_counter()
    stream = (output / "results.jsonl").open("w")

    def run_provider(spec):
        provider, role, endpoint, pace = spec
        http = JSONHTTP(timeout=args.timeout, headers_by_url=config["headers_by_url"])
        last_dispatch = 0
        for case in cases:
            wait_time = max(0, last_dispatch + pace - time.perf_counter())
            if wait_time:
                time.sleep(wait_time)
            address, tx_hash = case["address"].lower(), case["benchmark_transaction"]
            url, payload = endpoint, None
            if provider == "alchemy_history":
                payload = [{"jsonrpc": "2.0", "id": n, "method": "alchemy_getAssetTransfers", "params": [{
                    "fromBlock": "0x0", "toBlock": "latest", direction + "Address": address,
                    "category": ["external", "internal", "erc20", "erc721", "erc1155", "specialnft"],
                    "order": "asc", "maxCount": "0x1", "excludeZeroValue": False, "withMetadata": True}]}
                    for n, direction in ((1, "from"), (2, "to"))]
            elif role == "verification_rpc":
                payload = [{"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                           {"jsonrpc": "2.0", "id": 2, "method": "eth_getTransactionReceipt", "params": [tx_hash]},
                           {"jsonrpc": "2.0", "id": 3, "method": "eth_getBlockByNumber", "params": ["finalized", False]}]
            elif provider == "blockcypher_history":
                url += address + "?limit=50"
            else:
                params = {"module": "account", "action": "txlist", "address": address,
                          "startblock": 0, "endblock": 99999999, "page": 1, "offset": 1, "sort": "asc"}
                if provider == "etherscan_history":
                    params.update(chainid=1, apikey=config["etherscan_key"])
                url += "?" + urlencode(params)
            queued = time.perf_counter()
            with semaphore:
                dispatched = time.perf_counter()
                last_dispatch = dispatched
                row = {"provider": provider, "role": role, "case_id": case["id"], "category": case["category"],
                       "queue_ms": round((dispatched - queued) * 1000, 3), "http_requests": 1,
                       "rpc_methods": len(payload) if isinstance(payload, list) else 0, "censored": False}
                data = None
                try:
                    with http.deadline_at(time.monotonic() + args.timeout):
                        data = http(url, payload)
                    row.update(transport="json_response", http_status=200)
                except SourceError as exc:
                    message = str(exc)
                    if message.startswith("HTTP ") and message[5:].isdigit():
                        code = int(message[5:])
                        row.update(transport="http_error", http_status=code,
                                   outcome="rate_limited" if code == 429 else "authentication_error" if code in {401, 403} else "http_error")
                    elif "TimeoutError" in message or "deadline" in message.lower():
                        row.update(transport="timeout", outcome="timeout", http_status=None, censored=True)
                    else:
                        row.update(transport="network_error", outcome="network_error", http_status=None)
                except Exception:
                    row.update(transport="client_error", outcome="client_error", http_status=None)
                row["latency_ms"] = round((time.perf_counter() - dispatched) * 1000, 3)
            validated = time.perf_counter()
            if row["transport"] == "json_response":
                if provider == "alchemy_history":
                    row.update(alchemy_history(data, address))
                elif provider == "blockcypher_history":
                    row.update(blockcypher_history(data, address))
                elif role == "verification_rpc":
                    row.update(rpc_result(data, tx_hash))
                else:
                    row.update(normal_history(data, address))
            row["validation_ms"] = round((time.perf_counter() - validated) * 1000, 3)
            with lock:
                results.append(row)
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                stream.flush()
        print(json.dumps({"provider": provider, "samples_complete": len(cases)}), flush=True)

    with ThreadPoolExecutor(max_workers=len(specs)) as pool:
        for future in as_completed([pool.submit(run_provider, spec) for spec in specs]):
            future.result()
    stream.close()
    providers = {}
    for name, role, _endpoint, _pace in specs:
        rows = [r for r in results if r["provider"] == name]
        usable = [r for r in rows if r["outcome"] in {"usable_candidate", "usable_rpc_response"}]
        providers[name] = {"role": role, "requests": len(rows), "usable_results": len(usable),
                           "outcomes": dict(Counter(r["outcome"] for r in rows)),
                           "transport": dict(Counter(r["transport"] for r in rows)),
                           "timeouts_censored": sum(r["censored"] for r in rows),
                           "latency_all": latency([r["latency_ms"] for r in rows]),
                           "latency_usable": latency([r["latency_ms"] for r in usable]),
                           "json_response_latency": latency([r["latency_ms"] for r in rows if r["transport"] == "json_response"])}
    summary = {"metadata": {"started_at": started_at, "finished_at": stamp(),
               "wall_seconds": round(time.perf_counter() - wall_start, 3),
               "samples_per_provider": len(cases), "total_http_requests": len(results),
               "total_rpc_methods": sum(r["rpc_methods"] for r in results),
               "timeout_seconds": args.timeout, "concurrency": 6, "one_inflight_per_provider_workload": True,
               "physical_vendor_overlap_limits": {"alchemy": 2, "other_vendors": 1},
               "concurrency_note": "Independent named workload loops; Alchemy history and Alchemy RPC may overlap. Not a serial isolated-vendor test.",
               "etherscan_min_spacing_seconds": .55, "routescan_min_spacing_seconds": .55,
               "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
               "application_cache": "none", "provider_cache": "not controlled",
               "runtime": "Python " + platform.python_version(), "platform": platform.system(),
               "network_context": "development machine normal HTTPS route; no provider regions asserted",
               "methodology": "20 distinct existing addresses per provider, 10 EOAs+10 contracts; measured dispatch-to-JSON/body time excludes scheduler queue and local validation. Discovery is not canonical first-appearance proof; RPC cohort measures chain/receipt/finalized-head retrieval, not indexed history. RPC request payload for a case is identical across vendors. No transactions submitted."},
               "providers": providers}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = ["# Independent provider latency benchmark", "", "Completed: " + summary["metadata"]["finished_at"], "",
             "Each provider received 20 distinct existing-address cases (10 EOAs, 10 contracts) from the unchanged 100-address corpus. No application cache was used. Provider caches cannot be cleared. One request per named provider/workload was in flight at a time, with six overall; Alchemy history and Alchemy RPC could overlap (at most two physical Alchemy calls), while other physical vendors had at most one; Etherscan and Routescan dispatches were spaced at least 550 ms.", "",
             "History queries and RPC verification are different workloads. Alchemy history batches include both directions (two billable RPC methods); the other history requests query one scoped history. Each verification batch contains chain ID, a known receipt and the finalized head (three RPC methods). QuickNode is measured as RPC, not an arbitrary-address history index.", "",
             "| Provider/workload | Usable / samples | All p50 ms | All p95 ms | All max ms | Usable p50 ms | Timeouts |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    def fmt(v):
        return "—" if v is None else f"{v:.1f}"
    for name, _role, _url, _pace in specs:
        p = providers[name]
        a, u = p["latency_all"], p["latency_usable"]
        lines.append(f"| {name} | {p['usable_results']}/{p['requests']} | {fmt(a['p50_ms'])} | {fmt(a['p95_ms'])} | {fmt(a['max_ms'])} | {fmt(u['p50_ms'])} | {p['timeouts_censored']} |")
    lines += ["", "## Outcomes", ""]
    for name, p in providers.items():
        lines.append(f"- `{name}`: `{json.dumps(p['outcomes'], sort_keys=True)}`")
    lines += ["", "Fast invalid, throttled or truncated responses do not count as useful age data. The invalid_payload category includes failed chain/receipt/finality validation and can include unavailable/null receipt data; it does not assert malformed JSON. BlockCypher's newest-first truncated history is rejected for oldest-event discovery. Empty normal history on a contract does not exclude creation/internal/token history. A usable discovery candidate still requires canonical receipt/block/finality verification in the full API.", "",
              "All-attempt latency includes failures. Timed-out requests are right-censored at their observed wait under the configured 5-second client timeout; DNS/TLS/socket overhead can exceed that setting. Their true completion times are unknown, and observed maxima are not an SLA. With only 20 samples, p95 is the 19th ordered sample and tail estimates are coarse. These are provider-response timings from one development machine, not deployed API end-to-end latency or fraud-detection accuracy.", "",
              f"Actual HTTP requests: {len(results)}. RPC methods requested: {summary['metadata']['total_rpc_methods']}. Total wall time: {summary['metadata']['wall_seconds']:.1f} seconds. API keys, private endpoint URLs and raw error bodies are excluded from artifacts."]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"completed": True, "http_requests": len(results), "wall_seconds": summary["metadata"]["wall_seconds"],
                      "output": str(output)}), flush=True)
    fcntl.flock(network_lock, fcntl.LOCK_UN)
    network_lock.close()


if __name__ == "__main__":
    main()
