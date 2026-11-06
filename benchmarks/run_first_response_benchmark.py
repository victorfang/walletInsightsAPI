#!/usr/bin/env python3
"""Live paired 100-address HTTP benchmark; never changes production policy.

The candidate uses the same real provider adapters and isolated persistent LRU.
Credentials are loaded in memory; artifacts contain provider names, never URLs.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import platform
import random
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))
from analyze_benchmark import latency
from eth_wallet_check.deadline import BoundedExecutor
from eth_wallet_check.http import JSONHTTP, SourceError
from wallet_age_days.config import settings
from wallet_age_days.factory import build_service
from wallet_age_days.server import WalletAgeHTTPServer
from wallet_age_days.store import EpochStore
from first_response_policy import FirstResponseAgeService

USED = {"existing_eoa", "existing_contract"}


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_error(exc):
    message = str(exc) if isinstance(exc, SourceError) else ""
    if message.startswith("HTTP ") and message[5:].isdigit():
        return message.replace(" ", "_").lower()
    if "deadline" in message.lower():
        return "deadline"
    if "cancel" in message.lower():
        return "cancelled"
    if message.startswith("Network failure"):
        return "network_error"
    return type(exc).__name__


class Clock:
    def __init__(self):
        self.local = threading.local()

    def __call__(self):
        self.local.last = time.time()
        return self.local.last


class Meter(JSONHTTP):
    def __init__(self, endpoints, timeout):
        super().__init__(timeout=timeout)
        self.names = dict((url, name) for name, url in endpoints)
        self.events, self.jobs = [], []
        self.lock, self.local = threading.Lock(), threading.local()
        self.request_starts = {}

    def wrap(self, request_id, job_name, fn):
        def run():
            self.local.identity = (request_id, job_name)
            started = time.perf_counter()
            event = {"request_id": request_id, "job": job_name, "state": "running"}
            with self.lock:
                self.jobs.append(event)
            try:
                value = fn()
                with self.lock:
                    event["state"] = "empty" if value is None else "verified" if value.finalized else "provisional"
                    if value is not None:
                        event.update(epoch=value.epoch, finalized=value.finalized)
                return value
            except Exception as exc:
                with self.lock:
                    event.update(state="error", error=safe_error(exc))
                raise
            finally:
                with self.lock:
                    event["seconds"] = time.perf_counter() - started
                    event["completed_after_request_seconds"] = time.perf_counter() - self.request_starts[request_id]
                self.local.identity = (None, None)
        return run

    def __call__(self, url, payload=None):
        parsed = urlsplit(url)
        source = self.names.get(url, {"api.etherscan.io": "etherscan", "api.routescan.io": "routescan",
            "eth.blockscout.com": "blockscout", "api.blockcypher.com": "blockcypher"}.get(parsed.hostname, "other"))
        request_id, job_name = getattr(self.local, "identity", (None, None))
        methods = [r.get("method") for r in payload] if isinstance(payload, list) else [payload.get("method")] if isinstance(payload, dict) else []
        event = {"request_id": request_id, "job": job_name, "provider": source, "methods": methods,
                 "action": parse_qs(parsed.query).get("action", [None])[0], "finished": False}
        started = time.perf_counter()
        with self.lock:
            self.events.append(event)
        try:
            value = super().__call__(url, payload)
            with self.lock:
                event["json_returned"] = True
            return value
        except Exception as exc:
            with self.lock:
                event.update(json_returned=False, error=safe_error(exc))
            raise
        finally:
            with self.lock:
                event.update(finished=True, seconds=time.perf_counter()-started)

    def snapshot(self, request_id=None):
        with self.lock:
            return ([dict(r) for r in self.events if request_id is None or r["request_id"] == request_id],
                    [dict(r) for r in self.jobs if request_id is None or r["request_id"] == request_id])

    def wait(self, request_id, timeout=3):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            events, jobs = self.snapshot(request_id)
            if all(e["finished"] for e in events) and all(j["state"] != "running" for j in jobs):
                return True
            time.sleep(.01)
        return False


class Sources:
    def __init__(self, sources, meter):
        self.sources, self.meter, self.active = sources, meter, None

    def jobs(self, address):
        return [(name, self.meter.wrap(self.active, name, fn)) for name, fn in self.sources.jobs(address)]


class Tracked:
    def __init__(self, service, meter):
        self.service, self.meter = service, meter
        self.sources = Sources(service.sources, meter)
        self.service.sources = self.sources
        self.active, self.results = None, {}
        self.clock = Clock()
        self.service.clock = self.clock

    def __getattr__(self, key):
        return getattr(self.service, key)

    def check(self, blockchain, address):
        try:
            result = self.service.check(blockchain, address)
        except Exception:
            if hasattr(self.service, "trace_for"):
                trace = self.service.trace_for(address)
                if trace:
                    self.results[self.active] = {"benchmark_token": trace["token"], "response_reason": trace["response_reason"]}
            raise
        self.results[self.active] = {"epoch": result.epoch, "as_of": int(self.clock.local.last),
            "evidence": result.evidence, "positive_sources": result.positive_sources,
            "benchmark_token": getattr(result, "benchmark_token", None),
            "response_reason": getattr(result, "response_reason", None)}
        return result


def arm(name, output, config, env_file, hard=2.6):
    executor = BoundedExecutor(max_workers=24)
    meter = Meter(config["endpoints"], hard)
    production = build_service(output / (name+".sqlite3"), env_file=env_file, http=meter, executor=executor)
    if name == "baseline":
        service = production
    else:
        service = FirstResponseAgeService(production.sources, production.store, meter,
            soft_deadline=1.0, hard_deadline=hard, executor=executor, refine=True)
    tracked = Tracked(service, meter)
    server = WalletAgeHTTPServer(("127.0.0.1", 0), tracked)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return {"name": name, "service": tracked, "meter": meter, "executor": executor,
            "server": server, "thread": thread, "hard": hard,
            "url": f"http://127.0.0.1:{server.server_port}/walletAgeDays?"}


def execute(arm, case, phase):
    name, service, meter = arm["name"], arm["service"], arm["meter"]
    request_id = f"{name}/{phase}/{case['id']}"
    service.active = service.sources.active = request_id
    before = service.store.get(service.namespace, case["address"].lower()) if case["expected_valid_format"] else None
    started = time.perf_counter()
    meter.request_starts[request_id] = started
    status, body, headers, error = None, None, {}, None
    try:
        try:
            response = urlopen(arm["url"]+urlencode({"blockchain":"ETH", "address":case["address"]}), timeout=arm["hard"]+1)
        except HTTPError as exc:
            response = exc
        with response:
            status, body = response.status, json.load(response)
            headers = {k.lower():v for k,v in response.headers.items() if k.lower().startswith("x-wallet-age-")}
    except Exception as exc:
        error = type(exc).__name__
    seconds = time.perf_counter()-started
    measured = service.results.get(request_id, {})
    events, jobs = meter.snapshot(request_id)
    initial_epoch = measured.get("epoch")
    row = {"arm":name, "phase":phase, "id":case["id"], "category":case["category"],
        "address":case["address"], "measured_at":stamp(), "seconds":seconds, "status":status, "body":body,
        "headers":headers, "transport_error":error, "cache_absent_before":before is None,
        "initial_epoch":initial_epoch, "as_of":measured.get("as_of"), "response_reason":measured.get("response_reason"),
        "upstream_attempts_at_response":len(events), "jobs_at_response":jobs,
        "formula_correct":body == max(0,(measured["as_of"]-initial_epoch)//86400) if status == 200 and type(body) is int and initial_epoch else None}
    token = measured.get("benchmark_token")
    if name != "baseline":
        service.drain(timeout=arm["hard"]+.5)
        if token:
            row["candidate_trace"] = service.snapshot(token)
    meter.wait(request_id, timeout=arm["hard"]+.5)
    final_epoch = service.store.get(service.namespace, case["address"].lower()) if case["expected_valid_format"] else None
    events, jobs = meter.snapshot(request_id)
    row.update(final_epoch=final_epoch, upstream_attempts_after_drain=len(events), jobs_after_drain=jobs,
        late_earlier_epoch=initial_epoch is not None and final_epoch is not None and final_epoch < initial_epoch,
        late_improvement_seconds=initial_epoch-final_epoch if initial_epoch and final_epoch else None,
        unfinished_jobs=sum(j["state"] == "running" for j in jobs))
    return row


def cohort(rows):
    return {"inputs":len(rows), "latency_seconds":latency([r["seconds"] for r in rows]),
        "statuses":dict(Counter(str(r["status"]) for r in rows)),
        "successful_ages":sum(r["status"] == 200 and type(r["body"]) is int for r in rows),
        "over_three_seconds":sum(r["seconds"] > 3 for r in rows),
        "cache_hits":sum(r["headers"].get("x-wallet-age-cache") == "hit" for r in rows),
        "source_evidence":dict(Counter(r["headers"].get("x-wallet-age-evidence", "none") for r in rows)),
        "response_reasons":dict(Counter(r.get("response_reason") or "none" for r in rows)),
        "transport_errors":sum(r["transport_error"] is not None for r in rows),
        "late_earlier_epochs":sum(r["late_earlier_epoch"] for r in rows),
        "formula_correct":sum(r["formula_correct"] is True for r in rows),
        "upstream_attempts":sum(r["upstream_attempts_after_drain"] for r in rows)}


def timing_window_analysis(rows):
    """Describe observed extra-window events without inventing a replay result."""
    cold=[r for r in rows if r["arm"]=="candidate_2_6" and r["phase"]=="cold"]
    availability,earlier=[],[]
    for r in cold:
        observations=[o for o in r.get("candidate_trace",{}).get("observations",[]) if o.get("state")=="verified"]
        before=[o["epoch"] for o in observations if o["seconds"]<=2.45]
        extra=[o["epoch"] for o in observations if 2.45<o["seconds"]<=2.6]
        if extra and not before: availability.append(r["id"])
        if extra and before and min(extra)<min(before): earlier.append(r["id"])
    return {"baseline_source_wait_seconds":2.45,"candidate_hard_deadline_seconds":2.6,
        "interpretation":"Post-hoc observed-window accounting, not a counterfactual HTTP benchmark",
        "only_verified_positive_after_2_45_ids":availability,"earlier_minimum_after_2_45_ids":earlier,
        "http_responses_over_2_45":sum(r["seconds"]>2.45 for r in cold),
        "successful_http_responses_over_2_45":sum(r["status"]==200 and r["seconds"]>2.45 for r in cold),
        "initial_cache_bypass":sum(r["headers"].get("x-wallet-age-cache")=="bypass" for r in cold),
        "positive_responses_without_retained_epoch":sum(r["status"]==200 and r["final_epoch"] is None for r in cold),
        "additional_client_attempts_after_response":sum(r["upstream_attempts_after_drain"]-r.get("upstream_attempts_at_response",0) for r in cold),
        "refinement_not_admitted_after_early_return":sum(r.get("response_reason") in {"two_usable_providers","one_provider_soft_deadline"}
            and not r.get("candidate_trace",{}).get("refinement_admitted") for r in cold)}


def attempt_accounting(rows,events):
    """Separate preflight rejection from potential network I/O; not billing."""
    result={}
    for name in sorted({r["arm"] for r in rows}):
        part=[e for e in events if (e.get("request_id") or "").startswith(name+"/cold/")]
        post=[]
        for r in rows:
            if r["arm"]!=name or r["phase"]!="cold":continue
            own=[e for e in part if e["request_id"]==f"{name}/cold/{r['id']}"]
            post.extend(own[r.get("upstream_attempts_at_response",0):])
        local=sum(e.get("error") in {"cancelled","deadline"} for e in part)
        postlocal=sum(e.get("error") in {"cancelled","deadline"} for e in post)
        result[name]={"client_attempts":len(part),"preflight_cancelled_or_expired":local,
            "attempts_not_known_locally_rejected":len(part)-local,
            "attempts_logged_after_response_snapshot":len(post),
            "after_response_preflight_rejections":postlocal,
            "after_response_not_known_locally_rejected":len(post)-postlocal,
            "interpretation":"Not invoice counts or proof of HTTP dispatch; network errors can include DNS/connection failure before sending HTTP"}
    return result


def summarize(rows, references, metadata, events, jobs):
    arms = {}
    for name in sorted({r["arm"] for r in rows}):
        cold = [r for r in rows if r["arm"] == name and r["phase"] == "cold"]
        warm = [r for r in rows if r["arm"] == name and r["phase"] == "warm"]
        controls = []
        by_id = {r["id"]:r for r in cold}
        for ref in references["creation_candidates"]:
            r = by_id.get(ref["id"])
            if r is None: continue
            controls.append({"id":r["id"], "label":ref["label"], "reference_epoch":ref["timestamp"],
                "initial_epoch":r["initial_epoch"], "final_epoch":r["final_epoch"],
                "initial_difference_seconds":None if r["initial_epoch"] is None else r["initial_epoch"]-ref["timestamp"],
                "final_difference_seconds":None if r["final_epoch"] is None else r["final_epoch"]-ref["timestamp"]})
        arms[name] = {"overall_cold":cohort(cold), "valid_cold":cohort([r for r in cold if r["category"] != "malformed"]),
            "known_used_cold":cohort([r for r in cold if r["category"] in USED]),
            "successful_cold":cohort([r for r in cold if r["status"] == 200]),
            "synthetic_cold":cohort([r for r in cold if r["category"] == "generated_valid"]),
            "malformed_cold":cohort([r for r in cold if r["category"] == "malformed"]), "positive_cache_warm":cohort(warm),
            "syntax":{"malformed_correct":sum(r["status"]==400 and r["body"]=="invalid" for r in cold if r["category"]=="malformed"),
                      "malformed_upstream_attempts":sum(r["upstream_attempts_after_drain"] for r in cold if r["category"]=="malformed"),
                      "synthetic_zero_ages":sum(r["status"]==200 and r["body"]==0 for r in cold if r["category"]=="generated_valid")},
            "deployment_controls":controls,
            "deployment_agreement":{"eligible":len(controls), "initial_exact":sum(r["initial_difference_seconds"]==0 for r in controls),
                "initial_missing":sum(r["initial_epoch"] is None for r in controls),
                "initial_later":sum(r["initial_difference_seconds"] is not None and r["initial_difference_seconds"]>0 for r in controls),
                "initial_earlier":sum(r["initial_difference_seconds"] is not None and r["initial_difference_seconds"]<0 for r in controls),
                "final_exact":sum(r["final_difference_seconds"]==0 for r in controls)}}
    baseline = {r["id"]:r for r in rows if r["arm"]=="baseline" and r["phase"]=="cold"}
    candidate = [r for r in rows if r["arm"]=="candidate_2_6" and r["phase"]=="cold"]
    pairs=[]
    for r in candidate:
        b=baseline[r["id"]]
        pairs.append({"id":r["id"], "category":r["category"], "baseline_status":b["status"], "candidate_status":r["status"],
            "latency_change_seconds":r["seconds"]-b["seconds"],
            "initial_integer_age_matches":r["body"]==b["body"] if r["status"]==b["status"]==200 else None,
            "initial_epoch_change_seconds":None if r["initial_epoch"] is None or b["initial_epoch"] is None else r["initial_epoch"]-b["initial_epoch"],
            "final_epoch_change_seconds":None if r["final_epoch"] is None or b["final_epoch"] is None else r["final_epoch"]-b["final_epoch"]})
    by_provider={}
    for provider in sorted({r["provider"] for r in events}):
        part=[r for r in events if r["provider"]==provider]
        by_provider[provider]={"attempts":len(part), "json_returned":sum(r.get("json_returned") is True for r in part),
            "latency_seconds":latency([r["seconds"] for r in part]), "errors":dict(Counter(r["error"] for r in part if r.get("error")))}
    return {"metadata":metadata, "arms":arms, "paired_comparison":pairs,
        "paired_both_successful":sum(p["baseline_status"]==p["candidate_status"]==200 for p in pairs),
        "paired_initial_candidate_later":sum(p["initial_epoch_change_seconds"] is not None and p["initial_epoch_change_seconds"]>0 for p in pairs),
        "paired_initial_candidate_earlier":sum(p["initial_epoch_change_seconds"] is not None and p["initial_epoch_change_seconds"]<0 for p in pairs),
        "paired_initial_epoch_matches":sum(p["initial_epoch_change_seconds"]==0 for p in pairs),
        "paired_final_epoch_matches":sum(p["final_epoch_change_seconds"]==0 for p in pairs),
        "paired_initial_integer_age_matches":sum(p["initial_integer_age_matches"] is True for p in pairs),
        "timing_window_analysis":timing_window_analysis(rows),
        "http_client_attempt_accounting":attempt_accounting(rows,events),
        "providers_during_algorithm_runs":by_provider, "source_job_states":dict(Counter(j["state"] for j in jobs))}


def fmt(value): return "—" if value is None else f"{value:.4f}"


def report(path, summary, rows):
    lines=["# walletAgeDays: first-two / one-second policy benchmark", "", f"Completed: {summary['metadata']['finished_at']}", "",
        "Actual local HTTP requests used live configured Alchemy/Etherscan/QuickNode plus existing public sources. "
        "Production was not changed or deployed. Both primary arms used the unchanged 100-address corpus, separate empty LRU databases, "
        "and interleaved randomized arm order. The candidate has a 1-second soft deadline, 2.6-second hard deadline, "
        "no exact timestamp-agreement requirement, and bounded background MIN refinement until its original hard deadline.", "",
        "## Response latency and availability", "", "Fast invalid/error responses are reported separately from successful ages and the 40 known-used controls.", "",
        "| Policy / cohort | Inputs | Integer ages | Median s | p95 s | Maximum s | >3 s |", "|---|---:|---:|---:|---:|---:|---:|"]
    for arm_name,a in summary["arms"].items():
        for c in ["overall_cold","valid_cold","known_used_cold","successful_cold","positive_cache_warm"]:
            v=a[c];t=v["latency_seconds"]
            lines.append(f"| {arm_name} / {c} | {v['inputs']} | {v['successful_ages']} | {fmt(t['median'])} | {fmt(t['p95'])} | {fmt(t['max'])} | {v['over_three_seconds']} |")
    lines += ["", "The 5-second sensitivity arm, when present, uses only 30 inputs: the first ten EOAs, ten contracts and ten synthetics from the same corpus. "
        "It is a real additional run, not a replay, but its sample and provider conditions differ from the full primary runs. "
        "Provider caches cannot be cleared, and these localhost measurements exclude remote-client transit and concurrent-load capacity.", "",
        "## Result quality and refinement", "", "| Policy | Known-used ages | Initial deployment exact / eligible | Missing deployment ages | Later than deployment | Earlier than deployment | Late earlier cache updates |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name,a in summary["arms"].items():
        k=a["known_used_cold"];d=a["deployment_agreement"]
        lines.append(f"| {name} | {k['successful_ages']}/{k['inputs']} | {d['initial_exact']}/{d['eligible']} | {d['initial_missing']} | {d['initial_later']} | {d['initial_earlier']} | {a['overall_cold']['late_earlier_epochs']} |")
    lines += ["", f"Paired successful initial responses: {summary['paired_both_successful']}. Candidate returned a later epoch in {summary['paired_initial_candidate_later']} pairs and an earlier epoch in {summary['paired_initial_candidate_earlier']} pairs.", "",
        f"Initial epochs matched the current policy in {summary['paired_initial_epoch_matches']}/{summary['paired_both_successful']} successful pairs; initial integer ages matched in {summary['paired_initial_integer_age_matches']}/{summary['paired_both_successful']}. After bounded background refinement, cached epochs matched in {summary['paired_final_epoch_matches']}/{summary['paired_both_successful']}. This is agreement with the current observed result, not universal accuracy.", "",
        "Deployment timestamps are independent receipt/block/archive-code controls; they are not proof that an address never received prefunding or appeared earlier. "
        "Existing EOAs prove prior use but have no independently established exact first-use timestamp. Synthetic addresses are unobserved candidates, not proven never-used addresses. "
        "Consequently no universal age-accuracy or fraud-detection percentage is claimed.", "", "## Stop reasons, outcomes and provider work", ""]
    for name,a in summary["arms"].items():
        v=a["overall_cold"]
        lines += [f"- **{name}:** HTTP outcomes `{json.dumps(v['statuses'],sort_keys=True)}`; stop reasons `{json.dumps(v['response_reasons'],sort_keys=True)}`; "
                  f"evidence `{json.dumps(v['source_evidence'],sort_keys=True)}`; total attributed upstream attempts including refinement: {v['upstream_attempts']}.",
                  f"  Malformed correct: {a['syntax']['malformed_correct']}; malformed upstream calls: {a['syntax']['malformed_upstream_attempts']}; synthetic zero-day ages: {a['syntax']['synthetic_zero_ages']}."]
    lines += ["", "Two independent providers count only after all their group jobs have settled successfully and supplied a finalized positive observation. "
        "Alchemy incoming/outgoing count as one provider. A partly completed provider can contribute an earlier verified epoch to the minimum, but cannot count as a second provider. "
        "The candidate intentionally omits the current policy's exact-agreement and Alchemy-settled guard. "
        "On a single valid observation after one second it returns immediately; one second is a soft deadline, not a guaranteed response time. "
        "No observation yields scoped not_found only if all histories were empty, otherwise unknown; neither becomes age zero.", "",
        "Background refinement does not delay the initial response. The runner drains it before the next cold lookup and warm request to avoid overlapping benchmark traffic. "
        "Drain time is excluded from response latency but included in total wall time and API-attempt accounting. Only positive cached records are warmed; uncached errors are not re-labeled cache hits. "
        "The network lock prevents overlap with the separate vendor benchmark.", ""]
    primary=summary["arms"].get("candidate_2_6",{})
    reasons=primary.get("known_used_cold",{}).get("response_reasons",{})
    w=summary["timing_window_analysis"]
    lines += [f"The primary candidate's {primary.get('known_used_cold',{}).get('successful_ages',0)} successful known-used responses stopped on the single-provider soft-deadline branch {reasons.get('one_provider_soft_deadline',0)} times and the two-completed-provider branch {reasons.get('two_usable_providers',0)} times. The observed speedup therefore does not demonstrate a fast two-provider path. A provider's raw JSON response and a completed usable provider group are different events.", "",
        "## Deadline-window accounting", "",
        "The baseline's 2.6-second application budget reserves 150 ms for persistence, leaving about 2.45 s of source waiting. The experimental candidate uses its full 2.6-second hard deadline and guards persistence against that deadline. This 150 ms difference can affect availability; the comparison is not an isolated stopping-rule experiment.", "",
        f"Post-hoc inspection of the primary candidate's actual traces found {len(w['only_verified_positive_after_2_45_ids'])} cases whose only verified positive arrived after 2.45 s and {len(w['earlier_minimum_after_2_45_ids'])} cases whose minimum improved during 2.45–2.6 s. {w['http_responses_over_2_45']} HTTP responses exceeded 2.45 s, of which {w['successful_http_responses_over_2_45']} were successful ages. Initial cache bypasses: {w['initial_cache_bypass']}; positive responses without a retained epoch: {w['positive_responses_without_retained_epoch']}. These are observed-window counts, not a counterfactual HTTP run.", "",
        f"Background refinement initiated {w['additional_client_attempts_after_response']} additional attributed HTTP-client attempts after the primary candidate's full response was received. Discovery jobs were not restarted, but already-started jobs could continue necessary verification RPC calls. Already sent requests may still be charged. Client attempts include local errors/cancellation and are not an invoice or billable-request count. Rejected refinement admissions after early returns: {w['refinement_not_admitted_after_early_return']}.", "",
        "Cache warm timings use these small isolated databases after population, not a new one-million-row benchmark. The LRU capacity remains one million; prior dedicated capacity tests are separate.", "", "## Reproduction and raw evidence", "",
        "```bash", "python3 benchmarks/run_first_response_benchmark.py --env-file .env --output benchmarks/run-first-response-new", "```", "",
        "Artifacts: results.jsonl, summary.json, metadata.json, upstream-events.json, source-jobs.json. No credentials or configured URLs are recorded. "
        "SQLite files are isolated experiment state and excluded from publication.", "", "## All cold results", "",
        "| Policy | ID | Category | HTTP | Seconds | Initial epoch | Refined epoch |", "|---|---|---|---:|---:|---:|---:|"]
    for r in sorted([r for r in rows if r["phase"]=="cold"],key=lambda r:(r["arm"],r["id"])):
        lines.append(f"| {r['arm']} | {r['id']} | {r['category']} | {r['status']} | {r['seconds']:.4f} | {r['initial_epoch']} | {r['final_epoch']} |")
    lines += ["", "## HTTP-client attempt accounting", "",
        "The meter records entry before the HTTP client's deadline/cancellation preflight. Locally cancelled/expired entries did not dispatch HTTP. Other entries may still fail during DNS/connect and are therefore potential network attempts, not proved wire requests or billable calls.","",
        "| Policy | Client attempts | Local preflight rejection | Not known locally rejected | Attempts logged after response snapshot | Post-response local rejection | Post-response not known locally rejected |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for name,a in summary["http_client_attempt_accounting"].items():
        lines.append(f"| {name} | {a['client_attempts']} | {a['preflight_cancelled_or_expired']} | {a['attempts_not_known_locally_rejected']} | {a['attempts_logged_after_response_snapshot']} | {a['after_response_preflight_rejections']} | {a['after_response_not_known_locally_rejected']} |")
    path.write_text("\n".join(lines)+"\n")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--env-file",default=".env")
    parser.add_argument("--seed",type=int,default=20261105)
    parser.add_argument("--minimum-start-interval",type=float,default=1.05)
    parser.add_argument("--sensitivity-count",type=int,default=30,choices=[0,30])
    args=parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()): parser.error("Use a fresh empty output directory")
    args.output.mkdir(parents=True,exist_ok=True)
    dataset=ROOT/"benchmarks/addresses.json"
    cases=json.loads(dataset.read_text())["cases"]
    assert len(cases)==100 and len({r["address"].lower() for r in cases})==100
    references=json.loads((ROOT/"benchmarks/references.json").read_text())
    config=settings(args.env_file)
    runtime=sorted((ROOT/"wallet_age_days").glob("*.py"))+[ROOT/"eth_wallet_check/http.py",ROOT/"benchmarks/first_response_policy.py"]
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime}
    metadata={"started_at":stamp(),"seed":args.seed,"dataset_sha256":hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "python":sys.version,"platform":platform.platform(),"clock":"perf_counter elapsed; epoch UTC seconds", "coverage":"ethereum",
        "paid_configured":[n for n,_ in config["endpoints"] if n in {"alchemy","quicknode"}],"etherscan_configured":bool(config["etherscan_key"]),
        "soft_deadline_seconds":1.0,"primary_hard_deadline_seconds":2.6,"sensitivity_hard_deadline_seconds":5.0,
        "minimum_start_interval_seconds":args.minimum_start_interval,"runtime_sha256":hashes,"initial_cache_rows":{},"pre_request_cold_misses":{},
        "network_phase_lock":"exclusive; separate vendor benchmark must finish first"}
    arms={};rows=[];events=[];jobs=[]
    with open("/private/tmp/wallet-age-benchmark-network.lock","a+") as lock:
        print(json.dumps({"state":"waiting_for_exclusive_network_phase"}),flush=True)
        fcntl.flock(lock,fcntl.LOCK_EX)
        loop_started=time.perf_counter();last_valid_started=0
        try:
            arms={n:arm(n,args.output,config,args.env_file) for n in ["baseline","candidate_2_6"]}
            if args.sensitivity_count: arms["candidate_5_0"]=arm("candidate_5_0",args.output,config,args.env_file,5.0)
            metadata["initial_cache_rows"]={n:a["service"].store.size() for n,a in arms.items()}
            rng=random.Random(args.seed);shuffled=list(cases);rng.shuffle(shuffled)
            with (args.output/"results.jsonl").open("w",buffering=1) as out:
                for index,case in enumerate(shuffled,1):
                    order=["baseline","candidate_2_6"];rng.shuffle(order)
                    for name in order:
                        if case["expected_valid_format"]:
                            time.sleep(max(0,args.minimum_start_interval-(time.perf_counter()-last_valid_started)))
                            last_valid_started=time.perf_counter()
                        row=execute(arms[name],case,"cold");rows.append(row);out.write(json.dumps(row)+"\n")
                        assert row["cache_absent_before"]
                        if row["status"]==200 and row["final_epoch"] is not None:
                            warm=execute(arms[name],case,"warm");rows.append(warm);out.write(json.dumps(warm)+"\n")
                    if index%10==0: print(json.dumps({"completed_primary_cases":index,"cold_requests":sum(r["phase"]=="cold" for r in rows),"elapsed_wall_seconds":round(time.perf_counter()-loop_started,1)}),flush=True)
                if args.sensitivity_count:
                    selection=[]
                    for category in ["existing_eoa","existing_contract","generated_valid"]:
                        selection.extend([c for c in cases if c["category"]==category][:10])
                    for index,case in enumerate(selection,1):
                        time.sleep(max(0,args.minimum_start_interval-(time.perf_counter()-last_valid_started)));last_valid_started=time.perf_counter()
                        row=execute(arms["candidate_5_0"],case,"cold");rows.append(row);out.write(json.dumps(row)+"\n")
                        if row["status"]==200 and row["final_epoch"] is not None:
                            warm=execute(arms["candidate_5_0"],case,"warm");rows.append(warm);out.write(json.dumps(warm)+"\n")
                        if index%10==0: print(json.dumps({"completed_sensitivity_cases":index}),flush=True)
            for a in arms.values():
                ev,jo=a["meter"].snapshot();events.extend(ev);jobs.extend(jo)
            metadata.update(finished_at=stamp(),loop_wall_seconds=time.perf_counter()-loop_started,
                final_cache_rows={n:a["service"].store.size() for n,a in arms.items()},
                pre_request_cold_misses={n:sum(r["arm"]==n and r["phase"]=="cold" and r["cache_absent_before"] and r["category"]!="malformed" for r in rows) for n in arms},
                runtime_unchanged=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items()),
                unfinished_http_calls=sum(not e["finished"] for e in events),unfinished_jobs=sum(j["state"]=="running" for j in jobs),
                forbidden_methods=sorted({m for e in events for m in e["methods"] if m in {"eth_getBalance","eth_getCode","eth_getTransactionCount"}}))
            result=summarize(rows,references,metadata,events,jobs)
            for name,value in [("metadata.json",metadata),("summary.json",result),("upstream-events.json",events),("source-jobs.json",jobs)]:
                (args.output/name).write_text(json.dumps(value,indent=2)+"\n")
            report(args.output/"REPORT.md",result,rows)
            assert metadata["runtime_unchanged"] and not metadata["forbidden_methods"]
            assert all(r["formula_correct"] for r in rows if r["status"]==200)
            print(json.dumps({"state":"complete","output":str(args.output),"arms":result["arms"]}),flush=True)
        finally:
            for a in arms.values():
                a["server"].shutdown();a["server"].server_close();a["thread"].join()
                a["service"].close();a["executor"].shutdown(wait=False,cancel_futures=True)
            fcntl.flock(lock,fcntl.LOCK_UN)


if __name__=="__main__": main()
