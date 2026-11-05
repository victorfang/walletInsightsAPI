#!/usr/bin/env python3
"""Run the unchanged 100-address corpus once, with no application-cache hits.

Production factory + real local HTTP. Provider jobs and HTTP calls are attributed
by address/job even when they finish after the response. Never records URLs/keys.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
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
from eth_wallet_check.http import JSONHTTP, SourceError
from wallet_age_days.factory import build_service
from wallet_age_days.server import WalletAgeHTTPServer

USED = {'existing_eoa', 'existing_contract'}


def stamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def safe_error(exc):
    message = str(exc) if isinstance(exc, SourceError) else ''
    if message.startswith('HTTP ') and message[5:].isdigit():
        return message.replace(' ', '_').lower()
    if 'deadline' in message.lower():
        return 'deadline'
    if message.startswith('Network failure'):
        return 'network_error'
    return type(exc).__name__


class Meter(JSONHTTP):
    def __init__(self, endpoints):
        super().__init__(timeout=2.6)
        self.names = {url: name for name, url in endpoints}
        self.events, self.jobs = [], []
        self.lock = threading.Lock()
        self.local = threading.local()

    def traced_job(self, case_id, name, fn):
        def run():
            self.local.identity = (case_id, name)
            started = time.perf_counter()
            event = {'case_id': case_id, 'job': name, 'state': 'running'}
            with self.lock:
                self.jobs.append(event)
            try:
                value = fn()
                with self.lock:
                    event['state'] = ('empty' if value is None else
                                      'verified_observation' if value.finalized else 'unverified_observation')
                    if value is not None:
                        event.update(epoch=value.epoch, finalized=value.finalized)
                return value
            except Exception as exc:
                with self.lock:
                    event.update(state='error', error=safe_error(exc))
                raise
            finally:
                with self.lock:
                    event['seconds'] = time.perf_counter() - started
                self.local.identity = (None, None)
        return run

    def __call__(self, url, payload=None):
        parsed = urlsplit(url)
        source = self.names.get(url, {'api.routescan.io': 'routescan', 'eth.blockscout.com': 'blockscout',
                                     'api.blockcypher.com': 'blockcypher'}.get(parsed.hostname, 'other'))
        methods = ([item.get('method') for item in payload] if isinstance(payload, list) else
                   [payload.get('method')] if isinstance(payload, dict) else [])
        case_id, job = getattr(self.local, 'identity', (None, None))
        event = {'case_id': case_id, 'job': job, 'source': source, 'methods': methods,
                 'action': parse_qs(parsed.query).get('action', [None])[0], 'finished': False}
        remaining = self._request_context.deadline - time.monotonic()
        event['deadline_expired_before_call'] = remaining <= 0
        started = time.perf_counter()
        with self.lock:
            self.events.append(event)
        try:
            value = super().__call__(url, payload)
            with self.lock:
                event['json_returned'] = True
            return value
        except Exception as exc:
            with self.lock:
                event.update(json_returned=False, error=safe_error(exc))
            raise
        finally:
            with self.lock:
                event.update(finished=True, seconds=time.perf_counter() - started)

    def snapshot(self):
        with self.lock:
            return ([dict(row) for row in self.events], [dict(row) for row in self.jobs])


class Sources:
    def __init__(self, original, meter, cases):
        self.original, self.meter = original, meter
        self.identifiers = {row['address'].lower(): row['id'] for row in cases}

    def jobs(self, address):
        return [(name, self.meter.traced_job(self.identifiers[address], name, fn))
                for name, fn in self.original.jobs(address)]


class Clock:
    last = None

    def __call__(self):
        self.last = time.time()
        return self.last


def summarize(rows, references, metadata, events, jobs):
    cohorts = {'overall_100': rows,
               'valid_uncached_80': [r for r in rows if r['category'] != 'malformed'],
               'known_used_40': [r for r in rows if r['category'] in USED],
               'successful_age_lookups': [r for r in rows if r['status'] == 200],
               'synthetic_40': [r for r in rows if r['category'] == 'generated_valid'],
               'malformed_20': [r for r in rows if r['category'] == 'malformed']}
    metrics = {}
    for name, cohort in cohorts.items():
        metrics[name] = {'inputs': len(cohort), 'latency_seconds': latency([r['seconds'] for r in cohort]),
                         'http_statuses': dict(Counter(str(r['status']) for r in cohort)),
                         'integer_ages': sum(r['status'] == 200 and type(r['body']) is int for r in cohort),
                         'within_3_seconds': sum(r['seconds'] <= 3 for r in cohort),
                         'transport_errors': sum(r['transport_error'] is not None for r in cohort),
                         'cache_hits': sum(r['headers'].get('x-wallet-age-cache') == 'hit' for r in cohort)}
    by_id = {r['id']: r for r in rows}
    controls = []
    for ref in references['creation_candidates']:
        row = by_id[ref['id']]
        epoch = row['epoch'] if row['status'] == 200 else None
        controls.append({'id': ref['id'], 'label': ref['label'], 'reference_epoch': ref['timestamp'],
                         'epoch': epoch, 'difference_seconds': None if epoch is None else epoch-ref['timestamp']})
    by_provider = {}
    for provider in sorted({e['source'] for e in events}):
        part = [e for e in events if e['source'] == provider]
        by_provider[provider] = {'http_client_attempts': len(part),
                                 'json_returned': sum(e.get('json_returned') is True for e in part),
                                 'errors': dict(Counter(e['error'] for e in part if e.get('error'))),
                                 'locally_expired_attempts': sum(e['deadline_expired_before_call'] for e in part)}
    return {'metadata': metadata, 'cohorts': metrics, 'deployment_controls': controls,
            'deployment_agreement': {'eligible': len(controls),
                                     'exact': sum(r['difference_seconds'] == 0 for r in controls),
                                     'missing': sum(r['epoch'] is None for r in controls),
                                     'later': sum(r['difference_seconds'] is not None and r['difference_seconds'] > 0 for r in controls),
                                     'earlier': sum(r['difference_seconds'] is not None and r['difference_seconds'] < 0 for r in controls)},
            'syntax': {'valid_inputs': len(cohorts['valid_uncached_80']),
                       'valid_accepted': sum(r['status'] in {200, 404, 503} for r in cohorts['valid_uncached_80']),
                       'malformed_inputs': len(cohorts['malformed_20']),
                       'malformed_correct': sum(r['status'] == 400 and r['body'] == 'invalid' for r in cohorts['malformed_20']),
                       'malformed_upstream_calls': sum(e['case_id'] in {r['id'] for r in cohorts['malformed_20']} for e in events)},
            'age_formula': {'successful': len(cohorts['successful_age_lookups']),
                            'correct': sum(r['formula_correct'] is True for r in cohorts['successful_age_lookups'])},
            'providers': by_provider, 'source_job_states': dict(Counter(j['state'] for j in jobs)),
            'verified_observations_by_index': dict(Counter(j['job'].split('_', 1)[0]
                for j in jobs if j['state'] == 'verified_observation')),
            'synthetic_zero_ages': sum(r['status'] == 200 and r['body'] == 0 for r in cohorts['synthetic_40'])}


def fmt(value):
    return '—' if value is None else f'{value:.4f}'


def write_report(path, result, rows):
    m, cohorts = result['metadata'], result['cohorts']
    known, overall = cohorts['known_used_40'], cohorts['overall_100']
    controls = result['deployment_agreement']
    lines = ['# walletAgeDays: 100 uncached inputs and overall results', '', f"Completed: {m['finished_at']}", '',
             f"**Known-used age availability: {known['integer_ages']}/{known['inputs']}.** "
             f"Their uncached median was **{fmt(known['latency_seconds']['median'])} s**, "
             f"p95 **{fmt(known['latency_seconds']['p95'])} s**, maximum **{fmt(known['latency_seconds']['max'])} s**. "
             f"Overall **{overall['within_3_seconds']}/{overall['inputs']}** requests finished within three seconds.", '',
             'This is a new live run of the same 100 distinct inputs, with a fresh isolated application cache. '
             'It contains no warm or alias passes. Provider caches cannot be cleared by this benchmark. '
             'The real production factory and local HTTP endpoint return bare integer ages on success.', '',
             '## Uncached latency', '',
             'Fast errors and local invalid-input rejections are included in the overall figures. '
             'Successful-age latency and availability are shown separately so they cannot be masked by those responses.', '',
             '| Cohort | Inputs | Integer ages | Mean s | Median s | p95 s | p99 s | Max s | ≤3 s |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    labels = {'overall_100': 'Overall', 'valid_uncached_80': 'All valid, uncached', 'known_used_40': 'Known-used controls',
              'successful_age_lookups': 'Successful age lookups', 'synthetic_40': 'Synthetic valid candidates', 'malformed_20': 'Malformed inputs'}
    for name, values in cohorts.items():
        t = values['latency_seconds']
        lines.append(f"| {labels[name]} | {values['inputs']} | {values['integer_ages']} | {fmt(t['mean'])} | {fmt(t['median'])} | "
                     f"{fmt(t['p95'])} | {fmt(t['p99'])} | {fmt(t['max'])} | {values['within_3_seconds']}/{values['inputs']} |")
    lines += ['', '## Overall outcomes and accuracy', '', '| Cohort | HTTP outcomes |', '|---|---|']
    for name in ('overall_100', 'known_used_40', 'synthetic_40', 'malformed_20'):
        lines.append(f"| {labels[name]} | `{json.dumps(cohorts[name]['http_statuses'], sort_keys=True)}` |")
    syntax, formula = result['syntax'], result['age_formula']
    lines += ['', f"- Syntax: {syntax['valid_accepted']}/{syntax['valid_inputs']} valid strings accepted; "
              f"{syntax['malformed_correct']}/{syntax['malformed_inputs']} malformed inputs correctly returned `400 \"invalid\"`. "
              f"Malformed inputs caused {syntax['malformed_upstream_calls']} provider calls.",
              f"- Known-used age availability: {known['integer_ages']}/{known['inputs']}; missing ages count against availability.",
              f"- Epoch-to-days arithmetic: {formula['correct']}/{formula['successful']} successful responses matched "
              '`floor((as_of - saved_epoch) / 86400)`. Arithmetic agreement does not establish that an epoch is globally earliest.',
              f"- Deployment agreement: {controls['exact']}/{controls['eligible']} controls matched; "
              f"{controls['missing']} missing, {controls['later']} later and {controls['earlier']} earlier than the reference deployment.",
              f"- Synthetic candidates returned {result['synthetic_zero_ages']} zero-day ages. A `404` is scoped no-observation; "
              '`503` is unavailable or incomplete evidence. Neither proves the address never appeared.',
              f"- Transport failures: {overall['transport_errors']}. No single universal wallet-age accuracy percentage is asserted.", '',
              'The 20 contract references were independently checked using deployment receipts, canonical blocks and archive code transitions. '
              'Matching deployment time is not proof that no prefunding or earlier address mention existed. '
              'The 20 additional existing-account controls in the historical `existing_eoa` cohort prove prior use '
              'but do not provide independent first-use timestamps. Account-type labels are historical corpus labels '
              'and were not independently revalidated in this run. '
              'Synthetic addresses are deterministic unobserved candidates, not independently proven never-used addresses.', '',
              '## Cache and measurement checks', '',
              f"- Initial application-cache rows: **{m['initial_cache_rows']}**. Final rows: **{m['final_cache_rows']}**.",
              f"- Every valid input was absent immediately before its request: **{m['pre_request_misses']}/80**.",
              f"- Observed cache-hit response headers: **{overall['cache_hits']}**.",
              f"- Requests: **100**, concurrency: **1**, request retries: **0**, shuffle seed: **{m['seed']}**.",
              f"- Sequential API time: **{m['api_seconds']:.3f} s**; measured loop wall time including {m['pause_seconds']} s pauses: "
              f"**{m['loop_wall_seconds']:.3f} s**. Pauses and cache-proof reads are excluded from response latency.",
              f"- Paid providers configured: **{', '.join(m['paid_configured']) or 'none; public sources only'}**.",
              f"- Runtime source files unchanged during run: **{m['runtime_unchanged']}**.",
              f"- Enrichment RPC methods (balance/nonce/code): **{len(m['forbidden_methods'])}**.",
              f"- Unfinished HTTP calls at capture: **{m['unfinished_http_calls']}**; unfinished source jobs: **{m['unfinished_jobs']}**.", '',
              'The 2.6-second application budget bounds waiting for sources. It cannot guarantee complete provider data or '
              'bound arbitrary operating-system stalls and internet propagation. This measures localhost HTTP including full JSON body read, '
              'not remote-client transit, server startup, cold OS disk caches, or concurrent-load capacity.', '',
              '## Provider observations', '',
              '| Provider | HTTP-client attempts | JSON returned | Error categories | Locally expired attempts |',
              '|---|---:|---:|---|---:|']
    for name, values in result['providers'].items():
        lines.append(f"| {name} | {values['http_client_attempts']} | {values['json_returned']} | "
                     f"`{json.dumps(values['errors'], sort_keys=True)}` | {values['locally_expired_attempts']} |")
    rate_limits = '; '.join(f"{name}: {values['errors'].get('http_429', 0)}/{values['http_client_attempts']} HTTP 429"
                           for name, values in result['providers'].items() if name in {'blockscout', 'blockcypher'})
    concentration = ('Only one indexed provider supplied verified observations, so independent indexed-fallback success '
                     'remains unverified by this run.' if len(result['verified_observations_by_index']) == 1 else
                     'Multiple indexed providers supplied verified observations; this run does not inject provider failures.' if
                     len(result['verified_observations_by_index']) > 1 else 'No indexed provider supplied a verified observation.')
    lines += ['', f"Source-job final states: `{json.dumps(result['source_job_states'], sort_keys=True)}`.", '',
              f"Verified observations by indexed provider: `{json.dumps(result['verified_observations_by_index'], sort_keys=True)}`. "
              f"Provider availability must be assessed separately from response latency. {rate_limits}. "
              f"{concentration} Synthetic-candidate outcomes are included in the overall outcome table; errors are not counted as usable ages.", '',
              'An HTTP JSON response is not necessarily a usable history result. Source jobs are reported separately as verified observation, '
              'unverified observation, empty, or error. Attempts include calls locally rejected after their deadline. '
              'HTTP calls and jobs carry the originating case ID, so late work is not attributed to the next address. '
              'Raw rows preserve the job snapshot at response time; final job states can include late results which the API did not use.', '',
              '## Deployment controls', '', '| Contract | Returned epoch | Reference epoch | Difference s |', '|---|---:|---:|---:|']
    for c in result['deployment_controls']:
        lines.append(f"| {c['label']} | {c['epoch'] if c['epoch'] is not None else 'missing'} | {c['reference_epoch']} | "
                     f"{c['difference_seconds'] if c['difference_seconds'] is not None else '—'} |")
    previous_path = ROOT / 'benchmarks/run-wallet-age-days-2026-11-05/results.jsonl'
    if previous_path.exists():
        old = [json.loads(line) for line in previous_path.read_text().splitlines()]
        old_known = [r for r in old if r['phase'] == 'cold' and r['category'] in USED]
        old_t = latency([r['seconds'] for r in old_known])
        lines += ['', '## Previous run comparison', '',
                  '| Same 40 known-used controls | Ages returned | Median s | p95 s | Max s |', '|---|---:|---:|---:|---:|',
                  f"| Prior cold run | {sum(r['status'] == 200 for r in old_known)}/40 | {fmt(old_t['median'])} | {fmt(old_t['p95'])} | {fmt(old_t['max'])} |",
                  f"| This cold run | {known['integer_ages']}/40 | {fmt(known['latency_seconds']['median'])} | {fmt(known['latency_seconds']['p95'])} | {fmt(known['latency_seconds']['max'])} |", '',
                  'These runs use the same corpus and API. Public-provider conditions and upstream caches vary; '
                  'differences are observations, not an isolated causal performance improvement.']
    lines += ['', '## Reproduce and inspect', '',
              f"- Dataset SHA-256: `{m['dataset_sha256']}`.",
              f"- Python: `{m['python'].splitlines()[0]}`.",
              '- Files: `results.jsonl`, `summary.json`, `metadata.json`, `upstream-events.json`, `source-jobs.json`.',
              '- Ethereum mainnet is the only searched chain. Reported ages are lower bounds from verified observed activity within covered histories.',
              '- The sample is not representative of all Ethereum wallets: 18 transaction senders came from one block and popular contracts were chosen deliberately.', '',
              '```bash', 'python3 benchmarks/run_uncached_wallet_age_benchmark.py --output benchmarks/run-uncached-new', '```', '',
              '## All 100 inputs', '',
              '| ID | Address | Cohort | Seconds | HTTP | Result | Saved first epoch |', '|---|---|---|---:|---:|---|---:|']
    for row in sorted(rows, key=lambda r: r['id']):
        address_literal = json.dumps(row['address'], ensure_ascii=True)
        lines.append(f"| {row['id']} | `{address_literal}` | {row['category']} | {row['seconds']:.4f} | "
                     f"{row['status']} | `{json.dumps(row['body'])}` | {row['epoch'] if row['epoch'] is not None else '—'} |")
    path.write_text('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pause', type=float, default=0.15)
    parser.add_argument('--seed', type=int, default=20261105)
    args = parser.parse_args()
    if args.pause < 0 or args.pause > 5:
        parser.error('--pause must be from 0 to 5 seconds')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output directory must be fresh and empty')
    args.output.mkdir(parents=True, exist_ok=True)
    dataset = ROOT / 'benchmarks/addresses.json'
    cases = json.loads(dataset.read_text())['cases']
    assert len(cases) == 100 and len({c['address'].lower() for c in cases}) == 100
    refs = json.loads((ROOT / 'benchmarks/references.json').read_text())
    endpoints = rpc_endpoints()
    meter = Meter(endpoints)
    service = build_service(args.output / 'epochs.sqlite3', http=meter)
    service.sources = Sources(service.sources, meter, cases)
    clock = Clock()
    service.clock = clock
    runtime = sorted((ROOT / 'wallet_age_days').glob('*.py')) + [ROOT / 'eth_wallet_check' / n for n in ('config.py', 'http.py', 'rpc.py', 'deadline.py')]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime}
    meta = {'started_at': stamp(), 'python': sys.version, 'seed': args.seed, 'pause_seconds': args.pause,
            'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
            'paid_configured': [n for n, _ in endpoints if n in {'alchemy', 'quicknode'}],
            'coverage': service.coverage, 'namespace': service.namespace, 'runtime_sha256': hashes,
            'initial_cache_rows': service.store.size(), 'pre_request_misses': 0}
    assert meta['initial_cache_rows'] == 0
    rows = []
    shuffled = list(cases)
    random.Random(args.seed).shuffle(shuffled)
    (args.output / 'metadata.json').write_text(json.dumps(meta, indent=2) + '\n')
    try:
        with WalletAgeHTTPServer(('127.0.0.1', 0), service) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}/walletAgeDays?'
            loop_start = time.perf_counter()
            with (args.output / 'results.jsonl').open('w', buffering=1) as out:
                for index, case in enumerate(shuffled, 1):
                    before = service.store.get(service.namespace, case['address'].lower()) if case['expected_valid_format'] else None
                    assert before is None
                    meta['pre_request_misses'] += int(case['expected_valid_format'])
                    clock.last = None
                    started = time.perf_counter()
                    status, body, headers, error = None, None, {}, None
                    try:
                        try:
                            response = urlopen(base + urlencode({'blockchain': 'ETH', 'address': case['address']}), timeout=4)
                        except HTTPError as exc:
                            response = exc
                        with response:
                            status = response.status
                            headers = {k.lower(): v for k, v in response.headers.items() if k.lower().startswith('x-wallet-age-')}
                            body = json.load(response)
                    except Exception as exc:
                        error = type(exc).__name__
                    seconds = time.perf_counter() - started
                    as_of = None if clock.last is None else int(clock.last)
                    epoch = service.store.get(service.namespace, case['address'].lower()) if case['expected_valid_format'] else None
                    _http, jobs = meter.snapshot()
                    row = {'id': case['id'], 'label': case['label'], 'category': case['category'], 'address': case['address'],
                           'phase': 'uncached', 'measured_at': stamp(), 'seconds': seconds, 'status': status, 'body': body,
                           'headers': headers, 'transport_error': error, 'cache_absent_before': before is None,
                           'epoch': epoch, 'as_of': as_of,
                           'formula_correct': body == (as_of-epoch)//86400 if status == 200 and type(body) is int and epoch is not None and as_of is not None else None,
                           'source_jobs_at_response': [j for j in jobs if j['case_id'] == case['id']]}
                    assert headers.get('x-wallet-age-cache') != 'hit'
                    rows.append(row)
                    out.write(json.dumps(row) + '\n')
                    if index % 20 == 0:
                        print(json.dumps({'completed': index, 'ages_returned': sum(r['status']==200 for r in rows),
                                          'max_seconds': max(r['seconds'] for r in rows)}), flush=True)
                    if args.pause:
                        time.sleep(args.pause)
            meta['loop_wall_seconds'] = time.perf_counter() - loop_start
            server.shutdown()
            worker.join()
        # Late provider work is bounded, attributed to the original input, and
        # never counted as a returned age. Drain only for artifact completeness.
        drain_start = time.monotonic()
        while time.monotonic() - drain_start < 5:
            events, jobs = meter.snapshot()
            if all(e['finished'] for e in events) and all(j['state'] != 'running' for j in jobs):
                break
            time.sleep(0.1)
        events, jobs = meter.snapshot()
        meta.update(finished_at=stamp(), final_cache_rows=service.store.size(), api_seconds=sum(r['seconds'] for r in rows),
                    runtime_unchanged=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in hashes.items()),
                    unfinished_http_calls=sum(not e['finished'] for e in events), unfinished_jobs=sum(j['state']=='running' for j in jobs),
                    forbidden_methods=sorted({method for e in events for method in e['methods'] if method in {'eth_getBalance','eth_getCode','eth_getTransactionCount'}}))
        result = summarize(rows, refs, meta, events, jobs)
        for name, value in [('metadata.json', meta), ('summary.json', result), ('upstream-events.json', events), ('source-jobs.json', jobs)]:
            (args.output / name).write_text(json.dumps(value, indent=2) + '\n')
        write_report(args.output / 'REPORT.md', result, rows)
        assert meta['runtime_unchanged'] and not meta['forbidden_methods']
        assert all(r['formula_correct'] for r in rows if r['status'] == 200)
        print(json.dumps({'output': str(args.output), 'cohorts': result['cohorts'], 'deployment_agreement': result['deployment_agreement']}), flush=True)
    finally:
        service.close()


if __name__ == '__main__':
    main()
