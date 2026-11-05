#!/usr/bin/env python3
"""Collect on-chain deployment controls, independent of the API's history result.

Contract-creation indexes only discover a candidate creation transaction. Canonical
receipts, block timestamps, and archive code at the preceding/deployment blocks
are fetched from a separate public RPC. Contract deployment is a useful age
control, not universal proof of an address's first appearance (prefunding exists).
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from collections import Counter
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


RPCS = (
    ("blockpi", "https://ethereum.public.blockpi.network/v1/rpc/public"),
    ("tenderly_public", "https://gateway.tenderly.co/public/mainnet"),
)
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
GENESIS_URL = "https://ethereum.org/en/history/"
REQUEST_COUNTS = Counter()


def request(url, payload=None):
    REQUEST_COUNTS[urlsplit(url).hostname] += 1
    headers = {"Accept": "application/json", "User-Agent": "eth-wallet-check/1.0"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=data, headers=headers), timeout=15) as response:
        return json.load(response)


def rpc_batch(calls):
    payload = [{"jsonrpc": "2.0", "id": i, "method": method, "params": params}
               for i, (method, params) in enumerate(calls)]
    errors = []
    for name, url in RPCS:
        try:
            chain = request(url, {"jsonrpc": "2.0", "id": 100000, "method": "eth_chainId", "params": []})
            if chain.get("result") != "0x1":
                raise ValueError("Not Ethereum mainnet")
            by_id = {}
            for offset in range(0, len(payload), 5):
                response = request(url, payload[offset:offset+5])
                expected = set(range(offset, min(offset+5, len(payload))))
                if not isinstance(response, list) or {row.get("id") for row in response} != expected:
                    raise ValueError("Invalid batch response")
                if all("error" in row for row in response):
                    raise ValueError("RPC does not provide these historical records")
                by_id.update({row["id"]: row for row in response})
            return name, [by_id[i] for i in range(len(calls))]
        except Exception as error:
            errors.append({"source": name, "error": type(error).__name__, "http_status": getattr(error, "code", None)})
    raise RuntimeError(f"Independent RPCs failed: {errors}")


def result(row):
    if "error" in row or "result" not in row:
        return None
    return row["result"]


def collect(rows, max_contracts=20, output_path=None):
    REQUEST_COUNTS.clear()
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started = time.monotonic()
    valid_existing = [row for row in rows if row.get("expected_has_appeared") is True
                      and ADDRESS.fullmatch(row.get("address", ""))]
    contract_rows = [row for row in valid_existing if "contract" in row.get("category", "").lower()][:max_contracts]
    discovered, failures, verified, genesis, genesis_state_checks = [], [], [], [], []
    def snapshot(state="in_progress"):
        value = {"state": state, "started_at": started_at,
                 "collected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                 "duration_seconds": round(time.monotonic() - started, 3),
                 "http_request_count": sum(REQUEST_COUNTS.values()),
                 "http_requests_by_host": dict(REQUEST_COUNTS),
                 "network": "ethereum-mainnet", "chain_id": 1,
                 "methodology": "Creation candidates are discovered with a separate contract-creation lookup, never DUT first_seen, then verified from canonical receipt/block/archive-state records using independent public RPCs. Deployment controls measure deployment agreement, not proof that no earlier prefunding or address mention existed.",
                 "creation_candidates": discovered, "deployment_controls": verified,
                 "genesis_controls": genesis, "genesis_state_checks": genesis_state_checks,
                 "by_address": {item["address"]: item for item in verified + genesis},
                 "failures": failures}
        if output_path is not None:
            Path(output_path).write_text(json.dumps(value, indent=2) + "\n")
        return value
    previous = {}
    if output_path is not None and Path(output_path).exists():
        try:
            previous = {item["address"]: item for item in json.loads(Path(output_path).read_text()).get("creation_candidates", [])}
        except (ValueError, KeyError):
            pass
    discovered.extend(dict(previous[row["address"].lower()], id=row["id"], label=row.get("label", ""))
                      for row in contract_rows if row["address"].lower() in previous)
    pending = [row for row in contract_rows if row["address"].lower() not in previous]
    for offset in range(0, len(pending), 5):
        chunk = pending[offset:offset+5]
        candidates = {}
        try:
            query = urlencode({"module": "contract", "action": "getcontractcreation",
                               "contractaddresses": ",".join(row["address"] for row in chunk)})
            response = request("https://api.routescan.io/v2/network/mainnet/evm/1/etherscan?" + query)
            if response.get("status") != "1" or not isinstance(response.get("result"), list):
                raise ValueError("Invalid creation lookup")
            candidates = {item["contractAddress"].lower(): item["txHash"] for item in response["result"]}
        except Exception as error:
            failures.append({"stage": "batch_creation_discovery", "error": type(error).__name__, "http_status": getattr(error, "code", None)})
        for row in chunk:
            address = row["address"].lower()
            try:
                tx_hash = candidates.get(address)
                discovery_source = "routescan_contract_creation_lookup"
                if tx_hash is None:
                    info = request("https://eth.blockscout.com/api/v2/addresses/" + address)
                    tx_hash = info.get("creation_transaction_hash")
                    discovery_source = "blockscout_candidate_creation_hash"
                if not isinstance(tx_hash, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash):
                    raise ValueError("No candidate creation transaction")
                discovered.append({"id": row["id"], "address": address, "label": row.get("label", ""),
                                   "transaction_hash": tx_hash, "discovery_source": discovery_source,
                                   "reference_urls": row.get("reference_urls", [])})
            except Exception as error:
                failures.append({"id": row["id"], "address": address, "stage": "creation_discovery",
                                 "error": type(error).__name__, "http_status": getattr(error, "code", None)})
        snapshot()
        print(json.dumps({"creation_candidates_saved": len(discovered)}), flush=True)
    if discovered:
        receipt_source, receipts = rpc_batch([("eth_getTransactionReceipt", [item["transaction_hash"]]) for item in discovered])
        receipt_items = []
        for item, response in zip(discovered, receipts):
            receipt = result(response)
            receipt_matches = isinstance(receipt, dict) and isinstance(receipt.get("contractAddress"), str) and receipt["contractAddress"].lower() == item["address"]
            # Pre-Byzantium receipts use a post-state root instead of status.
            legacy_success = isinstance(receipt, dict) and receipt.get("status") is None and "root" in receipt and receipt_matches
            if not isinstance(receipt, dict) or not (receipt.get("status") == "0x1" or legacy_success):
                failures.append({"id": item["id"], "stage": "receipt", "error": "Receipt unavailable or unsuccessful"})
                continue
            item["block_number"] = int(receipt["blockNumber"], 16)
            item["block_hash"] = receipt["blockHash"]
            item["direct_creation_receipt_matches"] = receipt_matches
            item["receipt_status"] = receipt.get("status")
            item["legacy_receipt_post_state_root"] = receipt.get("root") if legacy_success else None
            receipt_items.append(item)
        if receipt_items:
            block_source, blocks = rpc_batch([("eth_getBlockByNumber", [hex(item["block_number"]), False]) for item in receipt_items])
            code_calls = []
            for item in receipt_items:
                code_calls += [("eth_getCode", [item["address"], hex(item["block_number"] - 1)]),
                               ("eth_getCode", [item["address"], hex(item["block_number"])])]
            code_source, codes = rpc_batch(code_calls)
            for i, (item, block_row) in enumerate(zip(receipt_items, blocks)):
                block = result(block_row)
                before, after = result(codes[2*i]), result(codes[2*i+1])
                block_valid = isinstance(block, dict) and block.get("hash") == item["block_hash"]
                transition = before == "0x" and isinstance(after, str) and after != "0x"
                if not block_valid or not (transition or item["direct_creation_receipt_matches"]):
                    failures.append({"id": item["id"], "stage": "canonical_verification", "error": "Deployment not independently verified"})
                    continue
                timestamp = int(block["timestamp"], 16)
                item.update({"reference_kind": "contract_deployment", "timestamp": timestamp,
                             "timestamp_iso": datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z"),
                             "discovery_source": item.get("discovery_source", "blockscout_candidate_creation_hash"),
                             "receipt_source": receipt_source, "block_source": block_source,
                             "archive_code_source": code_source, "code_empty_before": (before == "0x") if before is not None else None,
                             "code_present_at_deployment": (isinstance(after, str) and after != "0x") if after is not None else None,
                             "archive_code_errors": [row.get("error") for row in codes[2*i:2*i+2] if "error" in row],
                             "code_transition_verified": transition,
                             "is_proven_first_address_appearance": False,
                             "scoring_note": "Compare first_txn to this independently verified deployment timestamp as deployment agreement, not universal wallet-age accuracy."})
                item["reference_urls"] = item["reference_urls"] + ["https://etherscan.io/tx/" + item["transaction_hash"]]
                verified.append(item)
                snapshot()

    eoa_rows = [row for row in valid_existing if "contract" not in row.get("category", "").lower()]
    if eoa_rows:
        source, balances = rpc_batch([("eth_getBalance", [row["address"], "0x0"]) for row in eoa_rows])
        for row, response in zip(eoa_rows, balances):
            balance = result(response)
            genesis_state_checks.append({"id": row["id"], "address": row["address"].lower(),
                                         "rpc_source": source,
                                         "balance_wei": str(int(balance, 16)) if isinstance(balance, str) else None,
                                         "error": response.get("error")})
            if isinstance(balance, str) and int(balance, 16) > 0:
                genesis.append({"id": row["id"], "address": row["address"].lower(), "label": row.get("label", ""),
                                "reference_kind": "genesis_allocation", "block_number": 0,
                                "genesis_balance_wei": str(int(balance, 16)), "rpc_source": source,
                                "timestamp": None, "reference_urls": [GENESIS_URL],
                                "scoring_note": "Address existed in genesis state; later first_txn does not represent first on-chain appearance. Header timestamp 0 is not used as a wallet-age date."})
    return snapshot("complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("addresses.json"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("references.json"))
    parser.add_argument("--max-contracts", type=int, default=20)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    rows = dataset if isinstance(dataset, list) else dataset.get("addresses", dataset.get("cases", []))
    output = collect(rows, args.max_contracts, args.output)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"deployment_controls": len(output["deployment_controls"]),
                      "genesis_controls": len(output["genesis_controls"]), "failures": len(output["failures"])}))


if __name__ == "__main__":
    main()
