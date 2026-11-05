#!/usr/bin/env python3
"""Summarize saved HTTP benchmark results without contacting any provider.

Classification agreement, missing results, and timestamp coverage are separate.
Deployment references measure deployment agreement, not proof of first-ever use.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics


HERE = Path(__file__).resolve().parent
USED = {"existing_eoa", "existing_contract"}
VALID = USED | {"generated_valid"}


def load_jsonl(path):
    """Permit an unfinished final line, but never silently ignore corrupt rows."""
    lines = path.read_text().splitlines(keepends=True)
    records, skipped = [], 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not line.endswith(("\n", "\r")):
                skipped += 1
            else:
                raise ValueError(f"Invalid JSON at line {index + 1}") from None
    return records, skipped


def quantile(values, probability):
    """Hyndman-Fan type 7: linear interpolation at (n - 1) * probability."""
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def latency(values):
    values = [float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
    if not values:
        return {"n": 0, **{key: None for key in ("min", "mean", "median", "p90", "p95", "p99", "max")}}
    return {
        "n": len(values), "min": min(values), "mean": statistics.fmean(values),
        "median": statistics.median(values), "p90": quantile(values, 0.90),
        "p95": quantile(values, 0.95), "p99": quantile(values, 0.99), "max": max(values),
    }


def rate(numerator, denominator):
    return numerator / denominator if denominator else None


def record(row):
    data = row.get("response", {}).get("data", {})
    return next(iter(data.values()), {}) if isinstance(data, dict) else {}


def stats(row):
    value = record(row).get("stats", {})
    return value if isinstance(value, dict) else {}


def cache(row):
    value = row.get("response", {}).get("meta", {}).get("cache")
    return value if isinstance(value, dict) else {}


def conclusive(row):
    value = stats(row)
    return (row.get("http_status") == 200 and record(row).get("is_address_valid") is True
            and type(value.get("has_appeared")) is bool
            and type(value.get("new_address")) is bool
            and value["has_appeared"] is not value["new_address"])


def format_correct(row, case):
    if row.get("transport_error"):
        return False
    value = record(row)
    if case["expected_valid_format"]:
        return value.get("is_address_valid") is True
    # Empty address is rejected by query validation and therefore has no record.
    return row.get("http_status") == 400 and (
        not value or value.get("is_address_valid") is False
    )


def agreement(rows, cases):
    eligible = [row for row in rows if type(cases[row["id"]].get("expected_has_appeared")) is bool]
    known = [row for row in eligible if conclusive(row)]
    correct = [row for row in known
               if stats(row)["has_appeared"] is cases[row["id"]]["expected_has_appeared"]]
    return {
        "eligible": len(eligible), "conclusive": len(known), "correct": len(correct),
        "incorrect_conclusive": len(known) - len(correct),
        "unknown_or_unusable": len(eligible) - len(known),
        "agreement_including_unknown_as_not_correct": rate(len(correct), len(eligible)),
        "agreement_among_conclusive": rate(len(correct), len(known)),
        "conclusive_coverage": rate(len(known), len(eligible)),
    }


def accuracy(rows, cases):
    unique = {row["id"]: row for row in reversed(rows)}
    rows = list(unique.values())
    correct = sum(format_correct(row, cases[row["id"]]) for row in rows)
    used = [row for row in rows if cases[row["id"]]["category"] in USED]
    synthetic = [row for row in rows if cases[row["id"]]["category"] == "generated_valid"]
    return {
        "unique_cases": len(rows), "format_correct": correct,
        "format_accuracy": rate(correct, len(rows)),
        "known_used_recall": agreement(used, cases),
        "synthetic_candidate_agreement": agreement(synthetic, cases),
        "valid_dataset_agreement": agreement(used + synthetic, cases),
        "by_category": {
            category: {
                "n": len(selected),
                "format_correct": sum(format_correct(row, cases[row["id"]]) for row in selected),
                **agreement(selected, cases),
            }
            for category in sorted({cases[row["id"]]["category"] for row in rows})
            for selected in [[row for row in rows if cases[row["id"]]["category"] == category]]
        },
    }


def phase_metrics(rows, cases):
    valid = [row for row in rows if cases[row["id"]]["expected_valid_format"]]
    invalid = [row for row in rows if not cases[row["id"]]["expected_valid_format"]]
    hits = [row for row in rows if cache(row).get("hit") is True]
    valid_hits = [row for row in valid if cache(row).get("hit") is True]
    eligible = [row for row in valid if cache(row).get("stored") is True]
    events = [event for row in rows for event in row.get("upstream_events", [])]
    sources = {}
    for source in sorted({event["source"] for event in events}):
        selected = [event for event in events if event["source"] == source]
        sources[source] = {
            "http_requests": len(selected),
            "rpc_methods": sum(len(event.get("rpc_methods", [])) for event in selected),
            "transport_errors": sum(event.get("success") is False for event in selected),
            "http_429": sum(str(event.get("error", "")).startswith("HTTP 429") for event in selected),
            "error_counts": dict(Counter(event["error"] for event in selected if event.get("error"))),
            "returned_api_status_counts": dict(Counter(str(event["api_status"]) for event in selected if "api_status" in event)),
        }
    return {
        "requests": len(rows), "unique_cases": len({row["id"] for row in rows}),
        "http_status_counts": dict(Counter(str(row.get("http_status")) for row in rows)),
        "transport_error_counts": dict(Counter(row["transport_error"] for row in rows if row.get("transport_error"))),
        "latency_seconds": latency([row.get("latency_seconds") for row in rows]),
        "valid_latency_seconds": latency([row.get("latency_seconds") for row in valid]),
        "by_category": {
            category: {
                "requests": len(selected),
                "http_status_counts": dict(Counter(str(row.get("http_status")) for row in selected)),
                "latency_seconds": latency([row.get("latency_seconds") for row in selected]),
                "upstream_http_requests": sum(row.get("upstream_http_requests", 0) for row in selected),
                "upstream_rpc_methods": sum(row.get("upstream_rpc_methods", 0) for row in selected),
            }
            for category in sorted({cases[row["id"]]["category"] for row in rows})
            for selected in [[row for row in rows if cases[row["id"]]["category"] == category]]
        },
        "cache": {
            "hits": len(hits), "valid_requests": len(valid),
            "valid_cache_hit_rate": rate(len(valid_hits), len(valid)),
            "cacheable_valid_responses": len(eligible),
            "hit_rate_among_cacheable_valid_responses": rate(len(valid_hits), len(eligible)),
            "invalid_not_cacheable": len(invalid),
            "invalid_cache_hits": sum(cache(row).get("hit") is True for row in invalid),
            "invalid_with_upstream_requests": sum(row.get("upstream_http_requests", 0) > 0 for row in invalid),
            "hits_with_upstream_requests": sum(row.get("upstream_http_requests", 0) > 0 for row in hits),
            "hit_latency_seconds": latency([row.get("latency_seconds") for row in hits]),
        },
        "upstream_http_requests": sum(row.get("upstream_http_requests", 0) for row in rows),
        "upstream_rpc_methods": sum(row.get("upstream_rpc_methods", 0) for row in rows),
        "upstream_http_429": sum(str(event.get("error", "")).startswith("HTTP 429") for event in events),
        "upstream_by_source": sources,
        "reported_rpc_source_counts": dict(Counter(row.get("response", {}).get("meta", {}).get("sources", {}).get("rpc") or "none" for row in rows)),
        "reported_history_source_counts": dict(Counter(row.get("response", {}).get("meta", {}).get("sources", {}).get("history") or "none" for row in rows)),
    }


def optional_coverage(rows, cases):
    output = {}
    for category in sorted(VALID | {"all_known_used", "all_valid"}):
        selected = [row for row in rows if (
            cases[row["id"]]["category"] in USED if category == "all_known_used" else
            cases[row["id"]]["expected_valid_format"] if category == "all_valid" else
            cases[row["id"]]["category"] == category
        )]
        fields = ["balance_wei", "txns_total", "first_txn"]
        output[category] = {"n": len(selected), **{
            field: {"available": sum(stats(row).get(field) is not None for row in selected),
                    "coverage": rate(sum(stats(row).get(field) is not None for row in selected), len(selected))}
            for field in fields
        }}
    return output


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def age_analysis(rows, cases, references, started_at):
    by_id = {row["id"]: row for row in rows}
    by_address = references.get("by_address", {}) if references else {}
    controls = []
    genesis = []
    positive_bounds = []
    ignored_unverified = []
    at_time = timestamp(started_at)
    for case_id, case in cases.items():
        row = by_id.get(case_id)
        if row is None:
            continue
        observed = stats(row).get("first_txn")
        ref = by_address.get(case["address"].lower(), {})
        if ref.get("reference_kind") == "contract_deployment":
            expected = ref.get("timestamp")
            verified = (
                type(expected) is int and type(ref.get("block_number")) is int
                and bool(ref.get("receipt_source")) and bool(ref.get("block_source"))
                and (ref.get("direct_creation_receipt_matches") is True
                     or ref.get("code_transition_verified") is True)
            )
            if not verified:
                ignored_unverified.append(case_id)
                continue
            delta = observed - expected if type(observed) is int else None
            assessment = ("missing_first_txn" if delta is None else "exact_deployment_agreement" if delta == 0 else
                          "later_than_deployment_reported_age_too_young" if delta > 0 else
                          "earlier_than_deployment_ambiguous_earlier_observation")
            controls.append({
                "id": case_id, "address": case["address"], "label": case.get("label"),
                "reference_timestamp": expected, "reported_first_txn": observed,
                "error_seconds": delta, "error_days": delta / 86400 if delta is not None else None,
                "assessment": assessment,
                "reported_age_days_at_start": (at_time - observed) / 86400 if at_time is not None and type(observed) is int else None,
                "deployment_age_days_at_start": (at_time - expected) / 86400 if at_time is not None else None,
                "reference_transaction_hash": ref.get("transaction_hash"),
                "reference_block": ref.get("block_number"),
                "proven_first_address_appearance": ref.get("is_proven_first_address_appearance", False),
            })
        elif ref.get("reference_kind") == "genesis_allocation":
            first_block = stats(row).get("first_txn_block")
            genesis.append({"id": case_id, "address": case["address"], "label": case.get("label"),
                            "reported_first_txn": observed, "reported_first_block": first_block,
                            "reference_first_block": 0, "reference_timestamp": None,
                            "genesis_precedes_reported_first_block": type(first_block) is int and first_block > 0,
                            "first_occurrence_not_represented": first_block != 0,
                            "note": "Positive genesis allocation proves presence at block 0; no fabricated Unix age is assigned."})
        proof = case.get("proof", {})
        proof_time = proof.get("block_timestamp")
        if case.get("category") == "existing_eoa" and type(proof_time) is int:
            positive_bounds.append({
                "id": case_id, "proof_timestamp": proof_time, "reported_first_txn": observed,
                "has_timestamp": type(observed) is int,
                "reported_first_later_than_known_prior_use": type(observed) is int and observed > proof_time,
                "note": "Proof is a prior-use bound, not independently established first-use time.",
            })
    reported = [item for item in controls if item["error_seconds"] is not None]
    exact = [item for item in reported if item["error_seconds"] == 0]
    later = [item for item in reported if item["error_seconds"] > 0]
    earlier = [item for item in reported if item["error_seconds"] < 0]
    return {
        "first_txn_coverage": optional_coverage(rows, cases),
        "deployment_comparison": {
            "eligible_controls": len(controls), "timestamp_reported": len(reported),
            "missing_first_txn": len(controls) - len(reported), "exact_agreement": len(exact),
            "later_than_deployment": len(later), "earlier_ambiguous": len(earlier),
            "exact_agreement_all_controls": rate(len(exact), len(controls)),
            "exact_agreement_among_reported": rate(len(exact), len(reported)),
            "absolute_error_seconds_among_reported": latency([abs(item["error_seconds"]) for item in reported]),
            "too_young_error_seconds": latency([item["error_seconds"] for item in later]),
            "cases": controls,
            "ignored_unverified_reference_ids": ignored_unverified,
            "interpretation": "Deployment agreement is not a universal first-ever wallet-age accuracy metric; earlier prefunding can be legitimate.",
        },
        "genesis_controls": {
            "eligible": len(genesis),
            "first_occurrence_not_represented": sum(item["first_occurrence_not_represented"] for item in genesis),
            "cases": genesis,
        },
        "eoa_positive_proof_bounds": {
            "eligible": len(positive_bounds),
            "timestamp_reported": sum(item["has_timestamp"] for item in positive_bounds),
            "reported_first_later_than_known_prior_use": sum(item["reported_first_later_than_known_prior_use"] for item in positive_bounds),
            "cases": positive_bounds,
        },
    }


def summarize(rows, dataset, metadata, references=None, skipped_partial_lines=0):
    dataset_rows = dataset["cases"] if isinstance(dataset, dict) else dataset
    cases = {case["id"]: case for case in dataset_rows}
    unknown = sorted({row.get("id") for row in rows} - set(cases))
    if unknown:
        raise ValueError(f"Result IDs absent from dataset: {unknown}")
    phases = defaultdict(list)
    for row in rows:
        phases[row["phase"]].append(row)
    metrics = {phase: phase_metrics(selected, cases) for phase, selected in phases.items()}
    primary = {phase: {row["id"]: row for row in selected} for phase, selected in phases.items()}
    cache_analysis = {}
    for profile in ("detailed", "basic"):
        cold, warm = primary.get(profile + "_cold", {}), primary.get(profile + "_warm", {})
        paired = sorted(set(cold) & set(warm))
        valid_pairs = [case_id for case_id in paired if cases[case_id]["expected_valid_format"]]
        cached_pairs = [case_id for case_id in valid_pairs if conclusive(cold[case_id])]
        stable = [case_id for case_id in cached_pairs if stats(cold[case_id]) == stats(warm[case_id])]
        cache_analysis[profile] = {
            "paired_cases": len(paired), "valid_paired_cases": len(valid_pairs),
            "conclusive_valid_cold": len(cached_pairs), "stable_stats_on_repeat": len(stable),
            "stable_stats_fraction": rate(len(stable), len(cached_pairs)),
            "unstable_cached_ids": [case_id for case_id in cached_pairs if case_id not in stable],
            "classification_stable_all_valid": sum(
                (stats(cold[case_id]).get("has_appeared"), stats(cold[case_id]).get("new_address")) ==
                (stats(warm[case_id]).get("has_appeared"), stats(warm[case_id]).get("new_address"))
                for case_id in valid_pairs),
            "warm_hits": sum(cache(warm[case_id]).get("hit") is True for case_id in valid_pairs),
            "warm_hits_with_zero_upstream": sum(cache(warm[case_id]).get("hit") is True and warm[case_id].get("upstream_http_requests", 0) == 0 for case_id in valid_pairs),
            "cold_valid_mean_seconds": statistics.fmean(cold[case_id]["latency_seconds"] for case_id in valid_pairs) if valid_pairs else None,
            "warm_valid_mean_seconds": statistics.fmean(warm[case_id]["latency_seconds"] for case_id in valid_pairs) if valid_pairs else None,
        }
        before, after = cache_analysis[profile]["cold_valid_mean_seconds"], cache_analysis[profile]["warm_valid_mean_seconds"]
        cache_analysis[profile]["valid_mean_speedup"] = before / after if before is not None and after else None
    comparison = {}
    if "basic_cold" in metrics and "detailed_cold" in metrics:
        basic, detailed = metrics["basic_cold"], metrics["detailed_cold"]
        basic_mean = basic["valid_latency_seconds"]["mean"]
        detailed_mean = detailed["valid_latency_seconds"]["mean"]
        comparison = {
            "valid_mean_latency_speedup_detailed_over_basic": detailed_mean / basic_mean if detailed_mean is not None and basic_mean else None,
            "basic_cold_upstream_http_requests": basic["upstream_http_requests"],
            "detailed_cold_upstream_http_requests": detailed["upstream_http_requests"],
            "http_requests_saved_basic": detailed["upstream_http_requests"] - basic["upstream_http_requests"],
            "basic_cold_rpc_methods": basic["upstream_rpc_methods"],
            "detailed_cold_rpc_methods": detailed["upstream_rpc_methods"],
            "basic_known_used_without_history_calls": sum(
                cases[row["id"]]["category"] in USED and conclusive(row)
                and not any(event["source"] in {"blockscout", "routescan", "blockcypher"} for event in row.get("upstream_events", []))
                for row in phases["basic_cold"]),
            "note": "Phases ran sequentially, so public rate limits and upstream cache state can differ; this is an observed profile comparison, not a randomized causal estimate.",
        }
    detailed = list(primary.get("detailed_cold", {}).values())
    ages = age_analysis(detailed, cases, references or {}, metadata.get("started_at"))
    deployment_map = {item["id"]: item for item in ages["deployment_comparison"]["cases"]}
    genesis_map = {item["id"]: item for item in ages["genesis_controls"]["cases"]}
    assessments = []
    for case_id, case in cases.items():
        cold = primary.get("detailed_cold", {}).get(case_id, {})
        prediction = stats(cold)
        eligible = case.get("expected_valid_format") and type(case.get("expected_has_appeared")) is bool
        if not cold:
            result = "not_measured"
        elif not case["expected_valid_format"]:
            result = "correct_syntax_rejection" if format_correct(cold, case) else "incorrect_syntax_result"
        elif not conclusive(cold):
            result = "unknown_or_unusable"
        else:
            result = "correct" if prediction.get("has_appeared") is case.get("expected_has_appeared") else "incorrect"
        ref = deployment_map.get(case_id) or genesis_map.get(case_id) or {}
        assessments.append({
            "id": case_id, "address": case["address"], "label": case.get("label"), "category": case["category"],
            "expected_valid_format": case["expected_valid_format"],
            "expected_has_appeared": case.get("expected_has_appeared"), "expected_new_address": case.get("expected_new_address"),
            "ground_truth_level": case.get("ground_truth_level"),
            "reported_valid_format": record(cold).get("is_address_valid") if cold else None,
            "reported_has_appeared": prediction.get("has_appeared"), "reported_new_address": prediction.get("new_address"),
            "assessment": result, "classification_eligible": bool(eligible),
            "detailed_cold_http_status": cold.get("http_status"),
            "detailed_cold_seconds": cold.get("latency_seconds"),
            "detailed_warm_seconds": primary.get("detailed_warm", {}).get(case_id, {}).get("latency_seconds"),
            "basic_cold_seconds": primary.get("basic_cold", {}).get(case_id, {}).get("latency_seconds"),
            "basic_warm_seconds": primary.get("basic_warm", {}).get(case_id, {}).get("latency_seconds"),
            "first_txn": prediction.get("first_txn"), "first_txn_block": prediction.get("first_txn_block"),
            "first_txn_scope": prediction.get("first_txn_scope", []),
            "balance_wei": prediction.get("balance_wei"), "txns_total": prediction.get("txns_total"),
            "warnings": cold.get("response", {}).get("meta", {}).get("warnings", []),
            "history_scope": cold.get("response", {}).get("meta", {}).get("history_scope", []),
            "history_source": cold.get("response", {}).get("meta", {}).get("sources", {}).get("history"),
            "rpc_source": cold.get("response", {}).get("meta", {}).get("sources", {}).get("rpc"),
            "failed_upstream_actions": [
                {key: event.get(key) for key in ("source", "action", "error")}
                for event in cold.get("upstream_events", []) if event.get("error")
            ],
            "reference_kind": "contract_deployment" if case_id in deployment_map else "genesis_allocation" if case_id in genesis_map else None,
            "reference_timestamp": ref.get("reference_timestamp"),
            "age_error_seconds": ref.get("error_seconds"), "age_assessment": ref.get("assessment"),
        })
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metadata": metadata,
        "dataset": {"cases": len(cases), "category_counts": dict(Counter(case["category"] for case in cases.values())),
                    "format_valid": sum(case["expected_valid_format"] for case in cases.values())},
        "results": {"rows": len(rows), "skipped_incomplete_final_json_lines": skipped_partial_lines,
                    "run_finished": bool(metadata.get("finished_at")),
                    "phase_case_counts": {phase: len({row["id"] for row in selected}) for phase, selected in phases.items()}},
        "metric_definitions": {
            "quantiles": "Hyndman-Fan type 7 linear interpolation at (n-1)*p; all durations are seconds.",
            "format_accuracy": "Correct format judgments divided by unique cases in a phase; missing-address HTTP400 is a valid malformed rejection.",
            "classification": "Known-used recall is positive on-chain evidence; synthetic absence agreement is an expectation, not absolute proof. Unknown/unusable counts remain separate and are not counted correct in overall agreement.",
            "age": "Coverage is separate from deployment agreement. Earlier-than-deployment observations are ambiguous, and genesis controls have no invented Unix timestamp.",
            "latency": "HTTP client wall time includes local handler/serialization and upstream calls but excludes configured pauses. Empty application cache does not clear provider caches.",
            "cache": "Only conclusive valid results are cacheable; malformed inputs are local rejections. Cacheable results may still lack optional age/count details.",
            "upstream_errors": "Transport errors and HTTP429 are counted from request instrumentation; JSON-level api_status values are separately counted and are not all errors.",
            "phase_accuracy": "Repeated extra passes are deduplicated by case ID for accuracy; timing and request counts include every request.",
        },
        "phase_metrics": metrics,
        "phase_accuracy": {phase: accuracy(selected, cases) for phase, selected in phases.items()},
        "age_analysis": ages, "cache_analysis": cache_analysis, "profile_comparison": comparison,
        "reference_summary": {
            "available": bool(references), "collected_at": (references or {}).get("collected_at"),
            "deployment_controls": len((references or {}).get("deployment_controls", [])),
            "genesis_controls": len((references or {}).get("genesis_controls", [])),
            "failures": (references or {}).get("failures", []),
        },
        "cases": assessments,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=HERE / "addresses.json")
    parser.add_argument("--references", type=Path, default=HERE / "references.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows, skipped = load_jsonl(args.run / "results.jsonl")
    dataset = json.loads(args.dataset.read_text())
    metadata = json.loads((args.run / "metadata.json").read_text())
    references = json.loads(args.references.read_text()) if args.references.exists() else None
    summary = summarize(rows, dataset, metadata, references, skipped)
    output = args.output or args.run / "summary.json"
    output.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "rows": len(rows), "phases": summary["results"]["phase_case_counts"],
                      "run_finished": summary["results"]["run_finished"]}))


if __name__ == "__main__":
    main()
