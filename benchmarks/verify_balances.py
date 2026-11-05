#!/usr/bin/env python3
"""Verify benchmark API wei balances using an independent RPC at exact blocks.

Only reads the benchmark's SQLite cache and JSONL results. No transactions are
submitted, no cache entries are mutated, and unavailable evidence remains null.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.request import Request, urlopen


RPC_URL = "https://gateway.tenderly.co/public/mainnet"
RPC_NAME = "tenderly_public"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RPC:
    def __init__(self):
        self.http_requests = 0
        self.rpc_methods = 0

    def call(self, payload):
        self.http_requests += 1
        self.rpc_methods += len(payload) if isinstance(payload, list) else 1
        request = Request(RPC_URL, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "eth-wallet-check/1.0",
        })
        with urlopen(request, timeout=15) as response:
            return json.load(response)


def verify(run):
    started_at, started = stamp(), time.monotonic()
    cold = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    cold = [row for row in cold if row["phase"] == "detailed_cold"]
    if len(cold) != 100:
        raise ValueError("Detailed cold profile must finish all100cases before balance verification")
    cache_uri = "file:" + str((run / "cache.sqlite3").resolve()) + "?mode=ro"
    with closing(sqlite3.connect(cache_uri, uri=True)) as connection:
        cached = {key: json.loads(value) for key, value in connection.execute(
            "SELECT cache_key, value_json FROM cache_entries WHERE cache_key LIKE '%details=1'"
        )}
    rows, pending, skipped = [], [], []
    for case in cold:
        address = case["address"]
        if not ADDRESS.fullmatch(address):
            skipped.append({"id": case["id"], "reason": "Malformed input has no on-chain balance"})
            continue
        key = f"v1:ethereum:1:{address.lower()}:details=1"
        snapshot = cached.get(key, {})
        record = next(iter(case.get("response", {}).get("data", {}).values()), {})
        reported = record.get("stats", {}).get("balance_wei")
        row = {"id": case["id"], "category": case["category"], "address": address.lower(),
               "state_block_number": snapshot.get("state_block_number"),
               "api_balance_wei": reported, "cached_balance_wei": snapshot.get("balance_wei"),
               "api_matches_cached_balance": reported == snapshot.get("balance_wei") if reported is not None else None,
               "dut_rpc_source": snapshot.get("rpc_source"), "reference_balance_wei": None,
               "reference_matches_api": None, "reference_matches_cache": None}
        rows.append(row)
        if type(row["state_block_number"]) is not int or not isinstance(reported, str) or not reported.isdecimal():
            row["error"] = "API or cached exact block/balance unavailable"
        else:
            pending.append(row)
    rpc = RPC()
    output = {"started_at": started_at, "reference_provider": RPC_NAME,
              "chain_id": None, "chain_verified": False, "valid_cases": len(rows),
              "skipped_malformed_cases": skipped, "checks": rows,
              "methodology": "Compare integer wei from each detailed-cold HTTP response with an independent Tenderly eth_getBalance query at the EXACT state_block_number stored in its isolated read-only SQLite cache. No latest-state comparisons or floating-point balances are used.",
              "notes": ["Additional reference RPC requests are outside measured DUT latency and are reported separately.",
                        "Reference collection may overlap other benchmark phases; public provider quotas are not isolated.",
                        "Missing/error responses remain unverified, never treated as zero."]}
    output_path = run / "balance-verification.json"

    def save():
        available = [row for row in rows if row["reference_balance_wei"] is not None]
        output.update({"finished_at": stamp(), "duration_seconds": round(time.monotonic()-started, 3),
                       "reference_http_requests": rpc.http_requests, "reference_rpc_methods": rpc.rpc_methods,
                       "verified_cases": len(available),
                       "exact_matches": sum(row["reference_matches_api"] is True for row in available),
                       "mismatches": sum(row["reference_matches_api"] is False for row in available),
                       "unverified_cases": len(rows)-len(available)})
        output_path.write_text(json.dumps(output, indent=2) + "\n")

    try:
        response = rpc.call({"jsonrpc": "2.0", "id": 10000, "method": "eth_chainId", "params": []})
        if response.get("id") != 10000 or response.get("result") != "0x1":
            raise ValueError("Reference RPC was not verified as Ethereum mainnet")
        output.update(chain_id=1, chain_verified=True)
    except Exception as error:
        output["chain_error"] = {"type": type(error).__name__, "http_status": getattr(error, "code", None)}
        save()
        return output
    for offset in range(0, len(pending), 5):
        chunk = pending[offset:offset+5]
        payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_getBalance",
                    "params": [row["address"], hex(row["state_block_number"])]} for i, row in enumerate(chunk)]
        try:
            response = rpc.call(payload)
            if not isinstance(response, list) or len(response) != len(chunk):
                raise ValueError("Malformed batch response")
            by_id = {item.get("id"): item for item in response}
            if set(by_id) != set(range(len(chunk))):
                raise ValueError("Malformed batch IDs")
            for i, row in enumerate(chunk):
                value = by_id[i].get("result")
                if "error" in by_id[i] or not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", value):
                    row["error"] = "Reference RPC balance unavailable"
                    row["rpc_error"] = by_id[i].get("error")
                    continue
                amount = str(int(value, 16))
                row.update(reference_balance_wei=amount,
                           reference_matches_api=amount == row["api_balance_wei"],
                           reference_matches_cache=amount == row["cached_balance_wei"])
        except Exception as error:
            for row in chunk:
                row["error"] = {"type": type(error).__name__, "http_status": getattr(error, "code", None)}
        save()
        print(json.dumps({"balance_checks_completed": min(offset+5, len(pending)),
                          "verified": output["verified_cases"], "matches": output["exact_matches"]}), flush=True)
        time.sleep(0.2)
    save()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.run)
    print(json.dumps({key: result[key] for key in ["verified_cases", "exact_matches", "mismatches", "unverified_cases", "reference_http_requests", "reference_rpc_methods"]}))


if __name__ == "__main__":
    main()
