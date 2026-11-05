#!/usr/bin/env python3
"""Write the completed benchmark's Markdown report from saved evidence only."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def validate_complete(summary, metadata):
    if not metadata.get("finished_at") or not summary.get("results", {}).get("run_finished"):
        raise ValueError("Benchmark has not finished; report generation is refused.")
    if metadata.get("production_unchanged") is not True:
        raise ValueError("Production source changed or was not verified unchanged during the run.")
    if summary.get("metadata", {}).get("finished_at") != metadata["finished_at"]:
        raise ValueError("Summary is stale; rerun analyze_benchmark.py after collection finishes.")
    if summary.get("results", {}).get("rows") != metadata.get("total_api_requests"):
        raise ValueError("Summary row count differs from completed metadata.")
    expected = summary.get("dataset", {}).get("cases")
    if expected != 100:
        raise ValueError("The report requires the requested 100-case corpus.")
    counts = summary.get("results", {}).get("phase_case_counts", {})
    for phase in ("detailed_cold", "detailed_warm", "basic_cold", "basic_warm"):
        if counts.get(phase) != expected:
            raise ValueError(f"Incomplete primary phase: {phase}")


def number(value, places=2):
    return "—" if value is None else f"{value:,.{places}f}"


def percent(value):
    return "—" if value is None else f"{100 * value:.2f}%"


def ratio(correct, total):
    return f"{correct}/{total} ({percent(correct / total)})" if total else "0/0 (not assessable)"


def utc(seconds):
    if type(seconds) is not int:
        return "—"
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def local_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M:%S %Z")


def elapsed(start, end):
    return (datetime.fromisoformat(end.replace("Z", "+00:00")) -
            datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()


def text(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def address_cell(value):
    # JSON escapes preserve malformed newlines, whitespace and Unicode. Escaped
    # pipe/backtick code points also keep the surrounding Markdown table valid.
    encoded = json.dumps(value, ensure_ascii=True).replace("|", "\\u007c").replace("`", "\\u0060")
    return "`" + encoded + "`"


def expected_state(case):
    if not case["expected_valid_format"]:
        return "invalid format"
    return "seen" if case.get("expected_has_appeared") is True else "unobserved candidate"


def predicted_state(case):
    if case.get("reported_has_appeared") is True:
        return "seen"
    if case.get("reported_has_appeared") is False:
        return "no observed activity"
    if case.get("detailed_cold_http_status") == 400:
        return "invalid format"
    return "unknown / unusable"


def source_totals(phases):
    totals = defaultdict(lambda: {"http_requests": 0, "rpc_methods": 0, "transport_errors": 0,
                                  "http_429": 0, "error_counts": Counter()})
    for phase in phases.values():
        for source, values in phase.get("upstream_by_source", {}).items():
            for field in ("http_requests", "rpc_methods", "transport_errors", "http_429"):
                totals[source][field] += values.get(field, 0)
            totals[source]["error_counts"].update(values.get("error_counts", {}))
    return dict(totals)


def link(label, path, output_dir):
    return f"[{label}]({Path(os.path.relpath(path, output_dir)).as_posix()})"


def render_report(summary, dataset, references, metadata, run, output_dir,
                  dataset_path=HERE / "addresses.json", references_path=HERE / "references.json",
                  balance_verification=None):
    phases = summary["phase_metrics"]
    accuracy = summary["phase_accuracy"]["detailed_cold"]
    used = accuracy["known_used_recall"]
    synthetic = accuracy["synthetic_candidate_agreement"]
    valid = accuracy["valid_dataset_agreement"]
    age = summary["age_analysis"]
    deployment = age["deployment_comparison"]
    coverage = age["first_txn_coverage"]["all_known_used"]
    detailed_cache = summary["cache_analysis"]["detailed"]
    basic_cache = summary["cache_analysis"]["basic"]
    cohort = summary["dataset"]["category_counts"]
    cases = summary["cases"]
    source_usage = source_totals(phases)
    total_429 = sum(values["http_429"] for values in source_usage.values())
    identifiable_timeouts = sum(count for values in source_usage.values()
                                for error, count in values["error_counts"].items()
                                if "timeout" in error.lower())
    total_upstream = sum(values["http_requests"] for values in source_usage.values())
    total_methods = sum(values["rpc_methods"] for values in source_usage.values())
    source_failures = sum(values["transport_errors"] for values in source_usage.values())
    paid = metadata.get("paid_providers_configured", [])
    lines = []
    add = lines.append
    add("# Ethereum address-check benchmark — 5 November 2026")
    add("")
    add(f"Run: **{local_time(metadata['started_at'])} → {local_time(metadata['finished_at'])}** "
        f"({number(elapsed(metadata['started_at'], metadata['finished_at']), 1)} seconds elapsed). "
        "Network: Ethereum mainnet. Time zone: America/Chicago.")
    add("")
    add("## Findings")
    add("")
    add(f"The detailed cold pass correctly handled format validation for **{accuracy['format_correct']}/{accuracy['unique_cases']} inputs**. "
        f"It identified **{used['correct']}/{used['eligible']} known-used addresses**, with "
        f"{used['incorrect_conclusive']} incorrect conclusive classifications and {used['unknown_or_unusable']} inconclusive or unusable results. "
        f"Generated-address results agreed with the synthetic expectation for **{synthetic['correct']}/{synthetic['eligible']} candidates**. "
        "Generated candidates are not absolute proof of never-used addresses.")
    add("")
    add(f"A first-activity timestamp was available for **{coverage['first_txn']['available']}/{coverage['n']} known-used addresses**. "
        f"Among **{deployment['eligible_controls']} independently verified contract-deployment controls**, "
        f"{deployment['timestamp_reported']} had a returned timestamp and {deployment['exact_agreement']} matched deployment exactly. "
        f"{deployment['later_than_deployment']} were later than deployment; {deployment['earlier_ambiguous']} were earlier, which can reflect earlier address activity. "
        "This measures deployment-timestamp agreement and first-activity coverage, not private-key creation age.")
    add("")
    detailed_cold = phases["detailed_cold"]["valid_latency_seconds"]
    detailed_warm = phases["detailed_warm"]["valid_latency_seconds"]
    basic_cold = phases["basic_cold"]["valid_latency_seconds"]
    add(f"For format-valid inputs, detailed cold median latency was **{number(detailed_cold['median'])} s**, "
        f"and detailed warm median latency was **{number(detailed_warm['median'] * 1000)} ms**. "
        f"The basic cold median was **{number(basic_cold['median'])} s**. "
        f"Detailed warm mean latency improved by **{number(detailed_cache['valid_mean_speedup'], 1)}×** over detailed cold mean latency. "
        f"The run observed **{total_429} upstream HTTP 429 responses**.")
    add("")
    add("## Corpus and expected outcomes")
    add("")
    add("| Cohort | Inputs | Expected outcome | Strength of reference |")
    add("| --- | ---: | --- | --- |")
    add(f"| Existing EOAs | {cohort.get('existing_eoa', 0)} | Seen before; not new | Public controls and canonical transaction-sender inclusion prove prior use, but not exact first-use time |")
    add(f"| Existing contracts | {cohort.get('existing_contract', 0)} | Seen before; not new | Canonical deployment controls; receipt/block/archive-code evidence collected separately |")
    add(f"| Generated valid strings | {cohort.get('generated_valid', 0)} | No observed activity expected | Last 20 bytes of SHA-256 seeds; no private keys generated or retained; absence is a probabilistic expectation |")
    add(f"| Malformed strings | {cohort.get('malformed', 0)} | HTTP 400; no provider lookup | Explicit syntax-invalid controls: wrong length/prefix, nonhex, whitespace, Unicode, empty strings, and related cases |")
    add("")
    add("All 100 raw inputs are distinct, and the 80 format-valid inputs also have distinct normalized cache keys. "
        "The EOA selection is a convenience sample, including recent transaction senders; popular contracts are deliberate controls. "
        "This is not a representative random sample of all Ethereum wallets. The 100 inputs and their evidence are included in the appendix and dataset artifact.")
    add("")
    add("Format accuracy is scored against the documented full-string policy: lowercase `0x` followed by exactly 40 ASCII hexadecimal characters. "
        "This is syntax validation; it does not validate the optional [EIP-55 mixed-case checksum](https://eips.ethereum.org/EIPS/eip-55) or establish ownership.")
    add("")
    add("## Method")
    add("")
    add("Requests used the production HTTP handler at `GET /api/analytics/address/check?proto=eth&address=...`, "
        "with an isolated SQLite cache and a loopback HTTP client. The detailed profile sent `details=true`; the basic profile sent `details=false`. "
        "Each profile performed a 100-input cold pass, a 100-input warm repeat, and additional repeat passes. "
        "Basic and detailed requests have separate cache keys, so the basic cold pass did not reuse detailed entries.")
    add("")
    add(f"The run used concurrency **{metadata.get('concurrency')}**, deterministic shuffle seed **{metadata.get('seed')}**, "
        f"a per-upstream-request timeout of **{metadata.get('upstream_timeout_seconds')} s**, "
        f"and a **{metadata.get('inter_uncached_request_pause_seconds')} s** pause after uncached upstream activity. "
        "Latency uses a monotonic clock and includes HTTP transport, handler work, JSON serialization, and upstream requests. "
        "Configured pauses are excluded. Cold means an empty application cache, not cleared upstream/provider caches. "
        "Phases ran sequentially rather than in randomized A/B order.")
    add("")
    add(f"The recorded source hashes were unchanged throughout the run: **{metadata['production_unchanged']}**. "
        f"The harness recorded **{summary['results']['rows']} API requests**, **{total_upstream} upstream HTTP requests**, "
        f"and **{total_methods} logical RPC method calls**. The benchmark submitted no blockchain transactions. "
        "This is a serial loopback functional/latency benchmark, not a production WAN, throughput, or concurrent-load benchmark.")
    add("")
    add(f"Observed cold-pass cache hits were **{phases['detailed_cold']['cache']['hits']} detailed** and "
        f"**{phases['basic_cold']['cache']['hits']} basic**. These counters verify that the application cache was cold for the tested request keys.")
    add("")
    add("Reference collection overlapped the timed run and shared public endpoint quotas. Its traffic was not included in the DUT request counts above, "
        "but it could affect observed throttling and latency. Endpoint state, provider caching, and rate limits were not experimentally isolated.")
    add("")
    add("## Accuracy and coverage")
    add("")
    add("The following primary scores use the detailed cold pass, deduplicated by input ID. "
        "Unknown results are kept separate and are not counted correct in the all-eligible agreement rate.")
    add("")
    add("| Measure | Eligible | Correct / agreeing | Incorrect conclusive | Unknown / unusable | Rate over all eligible |")
    add("| --- | ---: | ---: | ---: | ---: | ---: |")
    add(f"| Format validation | {accuracy['unique_cases']} | {accuracy['format_correct']} | {accuracy['unique_cases'] - accuracy['format_correct']} | — | {percent(accuracy['format_accuracy'])} |")
    for label, values in (("Known-used address recall", used), ("Synthetic-candidate agreement", synthetic), ("Combined valid-input agreement", valid)):
        add(f"| {label} | {values['eligible']} | {values['correct']} | {values['incorrect_conclusive']} | {values['unknown_or_unusable']} | {percent(values['agreement_including_unknown_as_not_correct'])} |")
    add("")
    add(f"Conditional agreement among conclusive valid results was **{percent(valid['agreement_among_conclusive'])}** "
        f"on **{valid['conclusive']}** results. This conditional percentage is shown alongside the all-eligible denominator, "
        "so missing results cannot inflate the headline. The combined valid-input metric includes synthetic expectations and should not be described as universally proven blockchain-history accuracy.")
    add("")
    add("| Primary phase | Format correct | Known-used correct / eligible | Synthetic agreeing / eligible | Valid unknown / unusable |")
    add("| --- | ---: | ---: | ---: | ---: |")
    for phase in ("detailed_cold", "detailed_warm", "basic_cold", "basic_warm"):
        values = summary["phase_accuracy"][phase]
        a, b = values["known_used_recall"], values["synthetic_candidate_agreement"]
        add(f"| `{phase}` | {values['format_correct']}/{values['unique_cases']} | {a['correct']}/{a['eligible']} | {b['correct']}/{b['eligible']} | {values['valid_dataset_agreement']['unknown_or_unusable']} |")
    add("")
    add("### Optional fields on known-used inputs")
    add("")
    add("| Cohort | N | Exact wei balance available | Historical count available | First timestamp available |")
    add("| --- | ---: | ---: | ---: | ---: |")
    for category, label in (("existing_eoa", "EOAs"), ("existing_contract", "Contracts"), ("all_known_used", "All known-used")):
        values = age["first_txn_coverage"][category]
        add(f"| {label} | {values['n']} | {values['balance_wei']['available']} | {values['txns_total']['available']} | {values['first_txn']['available']} |")
    add("")
    add("These are availability scores. "
        "Live balances/counts can move between observations and their coverage depends on the reporting provider. "
        "Native `balance` is a JSON number; exact precision is preserved by `balance_wei`. "
        "Nonce is not treated as a historical transaction count. Missing fields remain null. Historical counts were not independently graded for exactness.")
    add("")
    if balance_verification and balance_verification.get("finished_at") and balance_verification.get("chain_verified") is True:
        b = balance_verification
        add("### Independent exact-balance verification")
        add("")
        add(f"An additional audit queried `{text(b.get('reference_provider'))}` at each detailed-cold result's **exact cached state block**, "
            f"rather than comparing balances at different moving heads. It independently verified **{b.get('verified_cases')}/{b.get('valid_cases')} format-valid inputs**: "
            f"**{b.get('exact_matches')} exact wei matches**, **{b.get('mismatches')} mismatches**, and **{b.get('unverified_cases')} unverified**. "
            "Comparison used integer wei strings, not the rounded native-number display field. Malformed inputs were excluded from the on-chain balance audit.")
        add("")
        add("| Cohort | Audited | Exact matches | Mismatches | Unverified |")
        add("| --- | ---: | ---: | ---: | ---: |")
        for category in ("existing_eoa", "existing_contract", "generated_valid"):
            checks = [item for item in b.get("checks", []) if item.get("category") == category]
            exact = sum(item.get("reference_matches_api") is True for item in checks)
            wrong = sum(item.get("reference_matches_api") is False for item in checks)
            add(f"| {category} | {len(checks)} | {exact} | {wrong} | {len(checks) - exact - wrong} |")
        add("")
        add(f"This audit used **{b.get('reference_http_requests')} additional HTTP requests** containing "
            f"**{b.get('reference_rpc_methods')} RPC methods** outside the measured DUT calls. "
            "It verifies the saved balance snapshots, not future balance freshness or historical transaction-count accuracy.")
        add("")
    add("## Age assessment")
    add("")
    direct = sum(item.get("direct_creation_receipt_matches") is True for item in references.get("deployment_controls", []))
    transitions = sum(item.get("code_transition_verified") is True for item in references.get("deployment_controls", []))
    add(f"The independent control set contains **{len(references.get('deployment_controls', []))} verified deployments**: "
        f"**{direct}** direct-creation receipts and **{transitions}** verified empty-code-before/nonempty-code-at-deployment transitions. "
        "For factory-created contracts, the code transition supplies the creation evidence. Candidate creation transactions were discovered separately from DUT `first_txn`; "
        "canonical receipt, block hash/timestamp, and archive-state verification provide the on-chain check. "
        "A discovery index can also be a DUT fallback, so the claim is independently verified deployment time, not a wholly disjoint set of discovery services.")
    add("")
    add(f"Exact deployment agreement was **{ratio(deployment['exact_agreement'], deployment['eligible_controls'])}** over all eligible controls, "
        f"or **{ratio(deployment['exact_agreement'], deployment['timestamp_reported'])}** among controls with returned timestamps. "
        f"There were **{deployment['missing_first_txn']}** missing timestamps, **{deployment['later_than_deployment']}** later timestamps, "
        f"and **{deployment['earlier_ambiguous']}** earlier observations.")
    add("")
    if deployment["later_than_deployment"]:
        add("A timestamp later than verified deployment makes the address appear too young if `first_txn` is interpreted as creation or first-ever appearance. "
            "The first-transaction search can omit deployment/creation events under its documented category coverage. "
            "This is separate from correctly detecting that the address already exists.")
        add("")
        by_case = {item["id"]: item for item in cases}
        for control in deployment["cases"]:
            if control.get("error_seconds") is None or control["error_seconds"] <= 0:
                continue
            item = by_case[control["id"]]
            categories = ", ".join(item.get("first_txn_scope", [])) or "none reported"
            add(f"**{text(control.get('label', control['id']))} ({control['id']}):** verified deployment was "
                f"{utc(control['reference_timestamp'])}; returned `first_txn` was {utc(control['reported_first_txn'])}. "
                f"The offset was **{number(control['error_seconds'], 0)} seconds ({number(control['error_days'], 4)} days)**. "
                f"Successful first-transaction categories were `{text(categories)}`. "
                "This is the earliest result across the categories that returned usable data, not a guaranteed complete-history minimum.")
            add("")
            failures = item.get("failed_upstream_actions", [])
            if failures:
                add("Recorded upstream failures for this check:")
                add("")
                for failure in failures:
                    action = failure.get("action") or "address/state request"
                    add(f"- `{text(failure.get('source'))}` / `{text(action)}`: `{text(failure.get('error'))}`")
                add("")
            related_warnings = [warning for warning in item.get("warnings", [])
                                if "unavailable" in warning.lower() or "rate limit" in warning.lower()
                                or "incomplete" in warning.lower()]
            if related_warnings:
                add("The response also warned: " + "; ".join(f"`{text(warning)}`" for warning in related_warnings) + ".")
                add("")
        add("These cases show that classification can remain correct while the optional age estimate becomes too young after partial history failures. "
            "The conclusive classification and its partial optional age are stored together for 24 hours, so a warm hit can preserve that partial age.")
        add("")
    add("A timestamp earlier than deployment is not automatically wrong: prefunding or other earlier activity can involve the eventual contract address. "
        "Deployment agreement also does not prove the absence of earlier prefunding. "
        "EOA private keys can be generated offline long before first activity, so exact key-generation age cannot be established from this API or this benchmark. "
        "See [Ethereum account creation](https://ethereum.org/developers/docs/accounts/) for the distinction between key generation and on-chain accounts.")
    add("")
    bounds = age["eoa_positive_proof_bounds"]
    add(f"For **{bounds['eligible']} EOAs** with canonical transaction inclusion evidence, the proof is an upper bound on first use. "
        f"**{bounds['timestamp_reported']}** had returned first timestamps; **{bounds['reported_first_later_than_known_prior_use']}** were later than an independently known prior transaction. "
        "The benchmark does not grade the remaining EOA timestamps as exact first-use dates.")
    add("")
    genesis_checks = references.get("genesis_state_checks", [])
    successful_genesis = sum(item.get("error") is None and item.get("balance_wei") is not None for item in genesis_checks)
    positive_genesis = sum(item.get("error") is None and int(item.get("balance_wei") or "0") > 0 for item in genesis_checks)
    add(f"Genesis-state checks were available for **{successful_genesis}/{len(genesis_checks)} EOAs**; **{positive_genesis}** had a positive genesis balance in those checks. "
        f"The run therefore supplied **{age['genesis_controls']['eligible']} positive genesis controls**. "
        "No synthetic age is assigned from a zero-valued genesis header timestamp.")
    add("")
    add("### Contract timestamp controls")
    add("")
    add("Error is `reported first_txn − deployment timestamp`; positive error means an age estimate that is too young relative to deployment. "
        "Negative error denotes an earlier observation and remains ambiguous.")
    add("")
    add("| ID | Contract | Verified deployment UTC | Reported first activity UTC | Error seconds | Assessment |")
    add("| --- | --- | --- | --- | ---: | --- |")
    for item in deployment["cases"]:
        label = {"exact_deployment_agreement": "Exact deployment match", "missing_first_txn": "Missing",
                 "later_than_deployment_reported_age_too_young": "Later; age too young",
                 "earlier_than_deployment_ambiguous_earlier_observation": "Earlier; ambiguous"}.get(item["assessment"], item["assessment"])
        add(f"| {item['id']} | {text(item.get('label', ''))} | {utc(item['reference_timestamp'])} | {utc(item['reported_first_txn'])} | {number(item['error_seconds'], 0)} | {label} |")
    add("")
    add("## Latency")
    add("")
    add("All values in the following tables are **milliseconds**, measured at the loopback HTTP client. "
        "Percentiles use Hyndman–Fan type 7 linear interpolation at `(n − 1) × p`. "
        "Valid and malformed inputs are separated to avoid letting fast syntax rejections dilute provider-backed latency.")
    add("")
    add("| Profile | Phase | Cohort | N | Mean ms | p50 ms | p95 ms | p99 ms | Max ms |")
    add("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for profile in ("detailed", "basic"):
        for phase in ("cold", "warm"):
            values = phases[profile + "_" + phase]
            for label, measure in (("Valid addresses", values["valid_latency_seconds"]),
                                   ("Malformed strings", values["by_category"]["malformed"]["latency_seconds"])):
                add(f"| {profile.title()} | {phase.title()} | {label} | {measure['n']} | " +
                    " | ".join(number(measure[key] * 1000 if measure[key] is not None else None) for key in ("mean", "median", "p95", "p99", "max")) + " |")
    add("")
    add("### Additional repeat passes")
    add("")
    add("The harness also repeated inputs eligible after the initial cold pass. Malformed strings are fast local rejections and are not cache hits; "
        "their inclusion in a repeat phase should not be mistaken for cached upstream work.")
    add("")
    add("| Profile | Additional passes | Total requests | Valid cache hits | Malformed local rejections | Hit mean ms | Hit p95 ms | Upstream HTTP requests |")
    add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for profile in ("detailed", "basic"):
        values = phases.get(profile + "_warm_extra", {})
        hit_stats = values.get("cache", {}).get("hit_latency_seconds", {})
        add(f"| {profile.title()} | {metadata.get('additional_cache_hit_passes')} | {values.get('requests', 0)} | {values.get('cache', {}).get('hits', 0)} | "
            f"{values.get('by_category', {}).get('malformed', {}).get('requests', 0)} | "
            f"{number(hit_stats.get('mean') * 1000 if hit_stats.get('mean') is not None else None)} | "
            f"{number(hit_stats.get('p95') * 1000 if hit_stats.get('p95') is not None else None)} | {values.get('upstream_http_requests', 0)} |")
    add("")
    add("## Cache behavior and 24-hour lifetime")
    add("")
    add("| Profile | Conclusive valid cold results | Warm valid hits | Hits with zero upstream requests | Stats unchanged on repeat | Mean speedup |")
    add("| --- | ---: | ---: | ---: | ---: | ---: |")
    for profile, values in (("Detailed", detailed_cache), ("Basic", basic_cache)):
        add(f"| {profile} | {values['conclusive_valid_cold']} | {values['warm_hits']}/{values['valid_paired_cases']} | {values['warm_hits_with_zero_upstream']} | "
            f"{values['stable_stats_on_repeat']}/{values['conclusive_valid_cold']} | {number(values['valid_mean_speedup'], 1)}× |")
    add("")
    all_hits = sum(values["cache"]["hits"] for values in phases.values())
    bad_hits = sum(values["cache"]["hits_with_upstream_requests"] for values in phases.values())
    add(f"Across all phases, **{all_hits} responses were marked cache hits**, and **{bad_hits} cache hits made an upstream request**. "
        "Conclusive valid results are stored in SQLite for 86,400 seconds. Reads preserve the original observation and expiry times. "
        "Malformed inputs are rejected locally on each call; unknown results are not cached. "
        "A cached conclusive result can still contain null optional fields after a provider failure.")
    add("")
    add("The 24-hour expiry boundary is covered by the project's injected-clock regression tests. "
        "This benchmark did not wait 24 wall-clock hours. Reported warm timings measure cache reuse within the same run; "
        "the SQLite persistence and exact expiry tests are separate evidence.")
    add("")
    add("## Sources, resilience, and request cost")
    add("")
    add("| Source | Upstream HTTP requests | Logical RPC methods | Transport errors | HTTP 429 |")
    add("| --- | ---: | ---: | ---: | ---: |")
    for source, values in sorted(source_usage.items()):
        add(f"| {text(source)} | {values['http_requests']} | {values['rpc_methods']} | {values['transport_errors']} | {values['http_429']} |")
    add(f"| **Total** | **{total_upstream}** | **{total_methods}** | **{source_failures}** | **{total_429}** |")
    add("")
    add(f"There were **{identifiable_timeouts} identifiable timeout errors** in recorded transport error strings. "
        "Some network failures are wrapped as `URLError` without their underlying cause, so that count is not proof that no other timeout occurred. "
        "JSON-level empty-history status values are not counted as transport errors. Source fallback tests and actual source usage "
        "should be distinguished: using a fallback in this run does not establish behavior under every possible outage.")
    add("")
    errors = Counter()
    for values in source_usage.values():
        errors.update(values["error_counts"])
    if errors:
        add("Recorded transport error messages:")
        add("")
        for error, count in sorted(errors.items()):
            add(f"- `{text(error)}`: {count}")
        add("")
    if paid:
        add("Configured paid providers were **" + ", ".join(text(value) for value in paid) + "**. "
            "Logical method counts are recorded; actual billed credits and monetary cost were not measured.")
    else:
        add("**Alchemy and QuickNode were not configured in the benchmark environment.** "
            "These timings exercise the free/public fallback path, not the user's paid endpoints. "
            "No paid-provider latency, billing-credit, or monetary-cost claim is made. Batch HTTP requests can contain multiple billable RPC methods.")
    add("")
    comparison = summary.get("profile_comparison", {})
    if comparison:
        add(f"The detailed cold pass made **{comparison['detailed_cold_upstream_http_requests']} upstream HTTP requests**, "
            f"versus **{comparison['basic_cold_upstream_http_requests']}** in the basic cold pass "
            f"(**{comparison['http_requests_saved_basic']} fewer** in the basic pass). "
            f"**{comparison['basic_known_used_without_history_calls']} known-used basic checks** completed without public-history calls. "
            f"The observed valid-input mean latency ratio was **{number(comparison['valid_mean_latency_speedup_detailed_over_basic'], 2)}×** detailed/basic. "
            "Rate limits and upstream caches can differ between sequential phases, so this is not a controlled causal cost estimate.")
        add("")
    add("## Recommendations")
    add("")
    add("1. Use the basic check for routine new-address screening and request details only when a workflow needs age/count information.")
    add("2. Treat `new_address` as a scoped no-observed-activity result. Preserve unknown/null outcomes and consult warnings rather than converting provider failures into new wallets.")
    add("3. Expose an age-completeness indicator. When required history categories fail, return null or explicitly flag `first_txn` as partial. Include a verified contract-creation receipt/block timestamp as an earliest-activity candidate when contract age is needed.")
    add("4. Keep the 24-hour classification cache, but allow partial optional age/count enrichment to refresh sooner instead of pinning a too-young or missing age for the entire classification TTL. Keep EOA key-generation age outside the API's claims.")
    add("5. Rerun this corpus with the existing paid Alchemy/QuickNode URLs configured before making production-latency or paid-cost claims. Add concurrent and WAN tests separately if those are deployment requirements.")
    add("6. Extend the corpus with old drained EOAs, token-only recipients, failed-transaction-only histories, genesis allocations, pre-funded contract addresses, delegated EOAs, and recently created addresses with independently recorded funding times.")
    add("")
    add("## Evidence and reproduction")
    add("")
    for label, path in (("100-input dataset", dataset_path),
                        ("Raw timed HTTP results (JSONL)", run / "results.jsonl"),
                        ("Machine-readable summary", run / "summary.json"),
                        ("Run metadata and source hashes", run / "metadata.json"),
                        ("Independent age reference evidence", references_path),
                        ("Benchmark runner", HERE / "run_benchmark.py"),
                        ("Summary calculator", HERE / "analyze_benchmark.py"),
                        ("Report generator", HERE / "write_report.py"),
                        ("API OpenAPI contract", ROOT / "openapi.json")):
        add("- " + link(label, path, output_dir))
    if balance_verification:
        add("- " + link("Independent exact-balance audit", run / "balance-verification.json", output_dir))
        add("- " + link("Balance verification script", HERE / "verify_balances.py", output_dir))
    if (run / "final-regression-tests.txt").exists():
        add("- " + link("Application and benchmark-analysis test results", run / "final-regression-tests.txt", output_dir))
    add("")
    add(f"Dataset SHA-256: `{metadata.get('dataset_sha256')}`. "
        "Per-file production hashes and the environment/provider order are recorded in the metadata artifact. "
        "The report was generated from saved results and did not contact providers.")
    add("")
    add("Use a fresh output directory for each timing run. To reduce quota contamination in a follow-up study, collect reference controls separately from the timed run.")
    add("")
    add("```bash")
    add(f"cd {ROOT}")
    add("python3 benchmarks/reference_age.py --output benchmarks/references-new.json")
    add("python3 benchmarks/run_benchmark.py --output benchmarks/run-new --timeout 5 --pause 0.3 --warm-repeats 3")
    add("python3 benchmarks/verify_balances.py --run benchmarks/run-new")
    add("python3 benchmarks/analyze_benchmark.py --run benchmarks/run-new --references benchmarks/references-new.json")
    add("python3 benchmarks/write_report.py --run benchmarks/run-new --references benchmarks/references-new.json --output benchmarks/BENCHMARK_REPORT_NEW.md")
    add("```")
    add("")
    add("## Appendix: all 100 inputs")
    add("")
    add("Addresses are JSON-escaped inside table cells so malformed whitespace and Unicode remain visible. "
        "The first-activity column uses the detailed cold result. Cold/warm durations below are milliseconds for the detailed profile. "
        "An em dash means missing or not applicable, not zero age.")
    add("")
    add("| ID | Cohort | Input address (JSON) | Expected | Observed | First activity UTC | Cold ms | Warm ms |")
    add("| --- | --- | --- | --- | --- | --- | ---: | ---: |")
    by_id = {item["id"]: item for item in cases}
    dataset_cases = dataset["cases"] if isinstance(dataset, dict) else dataset
    for original in dataset_cases:
        item = by_id[original["id"]]
        cold_ms = item["detailed_cold_seconds"] * 1000 if item.get("detailed_cold_seconds") is not None else None
        warm_ms = item["detailed_warm_seconds"] * 1000 if item.get("detailed_warm_seconds") is not None else None
        add(f"| {item['id']} | {text(item['category'])} | {address_cell(item['address'])} | {expected_state(item)} | {predicted_state(item)} | "
            f"{utc(item.get('first_txn'))} | {number(cold_ms)} | {number(warm_ms)} |")
    add("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=HERE / "addresses.json")
    parser.add_argument("--references", type=Path, default=HERE / "references.json")
    parser.add_argument("--output", type=Path, default=HERE / "BENCHMARK_REPORT_2026-11-05.md")
    args = parser.parse_args()
    summary = json.loads((args.run / "summary.json").read_text())
    metadata = json.loads((args.run / "metadata.json").read_text())
    validate_complete(summary, metadata)
    dataset = json.loads(args.dataset.read_text())
    references = json.loads(args.references.read_text())
    balance_path = args.run / "balance-verification.json"
    balance_verification = json.loads(balance_path.read_text()) if balance_path.exists() else None
    if hashlib.sha256(args.dataset.read_bytes()).hexdigest() != metadata.get("dataset_sha256"):
        parser.error("Dataset differs from the benchmark's recorded SHA-256.")
    if references.get("collected_at") != summary.get("reference_summary", {}).get("collected_at"):
        parser.error("Reference evidence and summary differ; rerun analyze_benchmark.py.")
    report = render_report(summary, dataset, references, metadata, args.run.resolve(), args.output.resolve().parent,
                           args.dataset.resolve(), args.references.resolve(), balance_verification)
    args.output.write_text(report)
    print(json.dumps({"report": str(args.output), "bytes": len(report.encode()), "cases": len(summary["cases"])}))


if __name__ == "__main__":
    main()
