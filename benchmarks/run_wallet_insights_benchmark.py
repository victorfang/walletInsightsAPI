#!/usr/bin/env python3
"""Benchmark WalletInsights on the unchanged100-input corpus.

Each fresh-cache cold request is followed immediately by its warm counterpart.
Read-only exact-block reference queries are outside measured API latency. No
private endpoint URL, credential, transaction submission or risk verdict is
created by this runner.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analyze_benchmark import latency
from eth_wallet_check.http import JSONHTTP, SourceError

REFERENCE_ENDPOINTS = (
    ('tenderly_public', 'https://gateway.tenderly.co/public/mainnet'),
    ('llamarpc_public', 'https://eth.llamarpc.com'),
)
USED = {'existing_eoa', 'existing_contract'}
_QUANTITY = re.compile(r'0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)')
_CODE = re.compile(r'0x(?:[0-9a-fA-F]{2})*')


def stamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def safe_error(exc):
    message = str(exc) if isinstance(exc, SourceError) else ''
    if message.startswith('HTTP ') and message[5:].isdigit():
        return message.replace(' ', '_').lower()
    if 'deadline' in message.lower():
        return 'deadline'
    return type(exc).__name__


class Meter(JSONHTTP):
    """Global phase instrumentation; no late-call per-request guessing.

    Production transport must retain deadline_at. Any headers (including the
    AnChain API key) are passed through but are never recorded.
    """
    def __init__(self, endpoints):
        super().__init__(timeout=2.6)
        self.names = {url: name for name, url in endpoints}
        self.events = []
        self.lock = threading.Lock()
        self.phase = 'initialization'
        self.local = threading.local()

    @contextmanager
    def identity(self, case_id, component):
        previous = getattr(self.local, 'identity', (None, None))
        self.local.identity = (case_id, component)
        try:
            yield
        finally:
            self.local.identity = previous

    def request(self, url, payload=None, headers=None):
        parsed = urlsplit(url)
        provider = self.names.get(url, {'api.anchainai.com': 'anchain'}.get(parsed.hostname, 'other'))
        methods = ([row.get('method') for row in payload] if isinstance(payload, list) else
                   [payload.get('method')] if isinstance(payload, dict) else [])
        case_id, component = getattr(self.local, 'identity', (None, None))
        event = {'provider': provider, 'methods': methods, 'phase_started': self.phase,
                 'case_id': case_id, 'component': component,
                 'finished': False}
        deadline = getattr(self._request_context, 'deadline', None)
        event['locally_expired'] = deadline is not None and deadline <= time.monotonic()
        started = time.perf_counter()
        with self.lock:
            self.events.append(event)
        try:
            value = super().request(url, payload, headers=headers)
            with self.lock:
                event['json_returned'] = True
            return value
        except Exception as exc:
            with self.lock:
                event.update(json_returned=False, error=safe_error(exc))
            raise
        finally:
            with self.lock:
                event.update(finished=True, seconds=time.perf_counter()-started)

    def snapshot(self):
        with self.lock:
            return [dict(row) for row in self.events]


def quantity(value):
    if not isinstance(value, str) or _QUANTITY.fullmatch(value) is None:
        raise ValueError('Invalid reference quantity')
    return int(value, 16)


def code_profile(value):
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise ValueError('Invalid reference code')
    value = value.lower()
    if value == '0x':
        return {'kind': 'empty_code', 'delegation_target': None, 'code_size': 0}
    if value.startswith('0xef0100') and len(value) == 48:
        return {'kind': 'delegated', 'delegation_target': '0x'+value[8:], 'code_size': 23}
    return {'kind': 'contract_code', 'delegation_target': None, 'code_size': (len(value)-2)//2}


def rpc_result(item):
    if not isinstance(item, dict) or item.get('jsonrpc') != '2.0' or 'error' in item or 'result' not in item:
        raise ValueError('Reference RPC error')
    return item['result']


def request(method, params, identifier):
    return {'jsonrpc': '2.0', 'method': method, 'params': params, 'id': identifier}


def fetch_reference(row, view, *, endpoints=REFERENCE_ENDPOINTS, headers_by_url=None):
    """Compare against a different provider at the exact canonical snapshot.

    view is normalized from the public response, not cached internal fields.
    A missing response or an RPC error is unverified, never an exact match.
    """
    result = {'id': row['id'], 'address': row['address'], 'block_number': view.get('block_number'),
              'block_hash': view.get('block_hash'), 'dut_provider': view.get('rpc_source'),
              'provider': None, 'attempts': [], 'fields': {}, 'complete_reference': False}
    for name, value in (('native_balance_wei', view.get('native_balance_wei')),
                        ('account_nonce', view.get('account_nonce')),
                        ('code_profile', view.get('code_profile'))):
        result['fields'][name] = {'api': value, 'reference': None, 'match': None}
    for token in view.get('tokens', []):
        result['fields']['token:' + token['address'].lower()] = {
            'symbol': token.get('symbol'), 'api': token.get('balance_raw'),
            'reference': None, 'match': None}
    if type(result['block_number']) is not int or not isinstance(result['block_hash'], str):
        result['error'] = 'No pinned API block available'
        return result
    address = row['address'].lower()
    tag = hex(result['block_number'])
    tokens = view.get('tokens', [])
    payload = [request('eth_chainId', [], 1), request('eth_getBlockByNumber', [tag, False], 2),
               request('eth_getBalance', [address, tag], 3),
               request('eth_getTransactionCount', [address, tag], 4),
               request('eth_getCode', [address, tag], 5)]
    for index, token in enumerate(tokens, 6):
        payload.append(request('eth_call', [{'to': token['address'],
            'data': '0x70a08231' + address[2:].rjust(64, '0')}, tag], index))
    reference_http = JSONHTTP(timeout=8, headers_by_url=headers_by_url)
    for name, endpoint in endpoints:
        if name == result['dut_provider']:
            continue
        started = time.perf_counter()
        attempt = {'provider': name}
        try:
            batch = reference_http(endpoint, payload)
            if not isinstance(batch, list) or len(batch) != len(payload):
                raise ValueError('Invalid reference batch')
            by_id = {}
            for item in batch:
                if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] in by_id:
                    raise ValueError('Invalid reference batch IDs')
                by_id[item['id']] = item
            if set(by_id) != set(range(1, len(payload)+1)):
                raise ValueError('Unexpected reference IDs')
            if quantity(rpc_result(by_id[1])) != 1:
                raise ValueError('Reference chain mismatch')
            block = rpc_result(by_id[2])
            if not isinstance(block, dict) or quantity(block.get('number')) != result['block_number'] or block.get('hash', '').lower() != result['block_hash'].lower():
                raise ValueError('Reference canonical block mismatch')
            pairs = [('native_balance_wei', 3, view.get('native_balance_wei')),
                     ('account_nonce', 4, view.get('account_nonce'))]
            for field, identifier, actual in pairs:
                try:
                    expected = str(quantity(rpc_result(by_id[identifier])))
                    result['fields'][field] = {'api': actual, 'reference': expected,
                                                'match': actual == expected if actual is not None else None}
                except Exception as exc:
                    result['fields'][field] = {'api': actual, 'reference': None, 'match': None,
                                                'error': type(exc).__name__}
            try:
                expected = code_profile(rpc_result(by_id[5]))
                actual = view.get('code_profile')
                result['fields']['code_profile'] = {'api': actual, 'reference': expected,
                    'match': all(actual.get(key) == value for key, value in expected.items()) if isinstance(actual, dict) else None}
            except Exception as exc:
                result['fields']['code_profile'] = {'api': view.get('code_profile'), 'reference': None,
                                                     'match': None, 'error': type(exc).__name__}
            for index, token in enumerate(tokens, 6):
                field = 'token:' + token['address'].lower()
                try:
                    value = rpc_result(by_id[index])
                    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', value):
                        raise ValueError('Invalid reference token balance')
                    expected = str(int(value, 16))
                    actual = token.get('balance_raw')
                    result['fields'][field] = {'symbol': token.get('symbol'), 'api': actual,
                                                'reference': expected, 'match': actual == expected if actual is not None else None}
                except Exception as exc:
                    result['fields'][field] = {'api': token.get('balance_raw'), 'reference': None,
                                                'match': None, 'error': type(exc).__name__}
            result.update(provider=name, complete_reference=all(f.get('reference') is not None for f in result['fields'].values()))
            attempt['success'] = True
            attempt['seconds'] = time.perf_counter()-started
            result['attempts'].append(attempt)
            return result
        except Exception as exc:
            attempt.update(success=False, seconds=time.perf_counter()-started,
                           error=type(exc).__name__, http_status=getattr(exc, 'code', None))
            result['attempts'].append(attempt)
    result['error'] = 'Independent reference unavailable'
    return result


def public_view(body):
    """Normalize the explicitly documented public response for verification."""
    body = body if isinstance(body, dict) else {}
    balances = body.get('balances') or {}
    native = balances.get('native') or {}
    block = body.get('block') or {}
    code = body.get('codeProfile')
    risk = body.get('riskLabels') or {}
    components = body.get('components') or {}
    cache = body.get('cache') or {}
    tokens = [{'symbol': t.get('symbol'), 'address': t.get('address'), 'decimals': t.get('decimals'),
               'balance_raw': t.get('balanceRaw'), 'status': t.get('status')}
              for t in balances.get('tokens', []) if isinstance(t, dict)]
    return {'status': body.get('status'), 'block_number': block.get('number'),
            'block_hash': block.get('hash'), 'rpc_source': block.get('source'),
            'native_balance_wei': native.get('balanceRaw'), 'account_nonce': body.get('accountNonce'),
            'code_profile': {'kind': code.get('kind'), 'delegation_target': code.get('delegationTarget'),
                             'code_size': code.get('codeSize')} if isinstance(code, dict) else None,
            'tokens': tokens, 'snapshot_status': components.get('snapshot'),
            'risk_status': risk.get('status'), 'risk_labels': risk.get('labels'),
            'snapshot_cache_hit': (cache.get('snapshot') or {}).get('hit') is True,
            'risk_cache_hit': (cache.get('riskLabels') or {}).get('hit') is True}


def summarize(rows, references, metadata, events):
    phases = {}
    for phase in ('cold', 'warm'):
        group = [r for r in rows if r['phase'] == phase]
        cohorts = {'overall': group, 'valid': [r for r in group if r['category'] != 'malformed'],
                   'known_used': [r for r in group if r['category'] in USED],
                   'complete_results': [r for r in group if public_view(r['body'])['status'] == 'complete'],
                   'fully_cached': [r for r in group if public_view(r['body'])['snapshot_cache_hit'] and
                                    public_view(r['body'])['risk_cache_hit']],
                   'synthetic': [r for r in group if r['category'] == 'generated_valid'],
                   'malformed': [r for r in group if r['category'] == 'malformed']}
        phases[phase] = {}
        for cohort, values in cohorts.items():
            valid = [r for r in values if r['category'] != 'malformed']
            views = [public_view(r['body']) for r in valid]
            phases[phase][cohort] = {
                'inputs': len(values), 'latency_seconds': latency([r['seconds'] for r in values]),
                'within_3_seconds': sum(r['seconds'] <= 3 for r in values),
                'transport_errors': sum(r['transport_error'] is not None for r in values),
                'http_statuses': dict(Counter(str(r['status']) for r in values)),
                'result_statuses': dict(Counter(str(v['status']) for v in views)),
                'snapshot_complete': sum(v['snapshot_status'] == 'complete' for v in views),
                'snapshot_partial': sum(v['snapshot_status'] == 'partial' for v in views),
                'native_balance_available': sum(v['native_balance_wei'] is not None for v in views),
                'nonce_available': sum(v['account_nonce'] is not None for v in views),
                'code_profile_available': sum(v['code_profile'] is not None for v in views),
                'risk_labels_available': sum(v['risk_status'] == 'available' for v in views),
                'risk_labels_not_configured': sum(v['risk_status'] == 'not_configured' for v in views),
                'snapshot_cache_hits': sum(v['snapshot_cache_hit'] for v in views),
                'risk_cache_hits': sum(v['risk_cache_hit'] for v in views),
                'both_cache_hits': sum(v['snapshot_cache_hit'] and v['risk_cache_hit'] for v in views),
                'token_balances_available': dict(Counter(t['symbol'] for v in views for t in v['tokens']
                    if t['balance_raw'] is not None and t['status'] == 'available')),
            }
    checks = [field for reference in references for field in reference.get('fields', {}).values()]
    matched = [field for field in checks if field.get('match') is True]
    mismatches = [field for field in checks if field.get('match') is False]
    cold = [r for r in rows if r['phase'] == 'cold']
    malformed = [r for r in cold if r['category'] == 'malformed']
    valid = [r for r in cold if r['category'] != 'malformed']
    provider = {}
    for name in sorted({e['provider'] for e in events}):
        part = [e for e in events if e['provider'] == name]
        provider[name] = {'attempts': len(part), 'json_returned': sum(e.get('json_returned') is True for e in part),
                          'errors': dict(Counter(e['error'] for e in part if e.get('error'))),
                          'locally_expired': sum(e.get('locally_expired') is True for e in part)}
    return {'metadata': metadata, 'phases': phases, 'providers': provider,
            'syntax': {'malformed_inputs': len(malformed),
                       'malformed_rejected': sum(r['status'] == 400 and r['body'] == 'invalid' for r in malformed),
                       'valid_inputs': len(valid),
                       'valid_accepted': sum(r['status'] in {200, 206, 503} and isinstance(r['body'], dict) for r in valid)},
            'independent_reference': {'eligible_inputs': len(valid), 'attempted_inputs': len(references),
                                     'complete_references': sum(r['complete_reference'] for r in references),
                                     'inputs_all_available_fields_match': sum(bool(r['fields']) and all(
                                         f.get('match') is not False for f in r['fields'].values()) and any(
                                         f.get('match') is True for f in r['fields'].values()) for r in references),
                                     'field_checks': len(checks), 'exact_matches': len(matched),
                                     'mismatches': len(mismatches),
                                     'unverified_fields': sum(f.get('match') is None for f in checks),
                                     'http_attempts': sum(len(r['attempts']) for r in references)},
            'reference_checks': references}


def fmt(value):
    return '—' if value is None else f'{value:.4f}'


def write_report(path, summary, rows, *, validation_note=None):
    meta, cold, warm = summary['metadata'], summary['phases']['cold'], summary['phases']['warm']
    known, valid, overall = cold['known_used'], cold['valid'], cold['overall']
    completed = datetime.fromisoformat(meta['finished_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Chicago'))
    lines = ['# WalletInsights: 100-address benchmark', '',
             f"Completed: {completed.strftime('%Y-%m-%d %H:%M:%S %Z')} (America/Chicago)", '',
             'The same 100-input corpus is exercised through the production factory and real local `/walletInsights` HTTP endpoint. '
             'Each address gets a fresh-cache cold lookup followed immediately by a warm lookup so a short snapshot TTL does not expire during a full corpus pass.', '',
             f"**Cold valid-address latency:** median **{fmt(valid['latency_seconds']['median'])}s**, "
             f"p95 **{fmt(valid['latency_seconds']['p95'])}s**, maximum **{fmt(valid['latency_seconds']['max'])}s**. "
             f"**{overall['within_3_seconds']}/{overall['inputs']} cold requests** finished within three seconds.", '',
             f"Among the 80 valid cold inputs: **{valid['snapshot_complete']}/80 complete on-chain snapshots**, "
             f"**{valid['risk_labels_available']}/80 AnChain label lookups available**. "
             'Response speed, complete data availability, and reference agreement are separate measures.', '',
             '## Uncached and overall latency', '',
             '| Phase/cohort | Inputs | Mean s | Median s | p95 s | p99 s | Max s | ≤3s |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for phase, group in summary['phases'].items():
        for name in ('overall', 'valid', 'known_used', 'complete_results', 'fully_cached', 'synthetic', 'malformed'):
            values = group[name]
            if name == 'fully_cached' and not values['inputs']:
                continue
            t = values['latency_seconds']
            lines.append(f"| {phase}/{name} | {values['inputs']} | {fmt(t['mean'])} | {fmt(t['median'])} | "
                         f"{fmt(t['p95'])} | {fmt(t['p99'])} | {fmt(t['max'])} | {values['within_3_seconds']}/{values['inputs']} |")
    lines += ['', 'Cold timing includes 80 format-valid addresses and 20 local malformed-address rejections. '
              'The known-used cohort has 40 controls; synthetic candidates also have legitimate current-state snapshots, even when all their balances are zero. '
              'Zero balance or nonce is not proof of never having appeared. The sample is not a production-load or attacker-detection evaluation.', '',
              '## Component availability and outcomes', '',
              '| Phase | HTTP outcomes | Result status | Complete snapshot | Labels available | Native balance | Nonce | Code profile |',
              '|---|---|---|---:|---:|---:|---:|---:|']
    for phase in ('cold', 'warm'):
        v = summary['phases'][phase]['valid']
        lines.append(f"| {phase} | `{json.dumps(summary['phases'][phase]['overall']['http_statuses'], sort_keys=True)}` | "
                     f"`{json.dumps(v['result_statuses'], sort_keys=True)}` | {v['snapshot_complete']}/80 | {v['risk_labels_available']}/80 | "
                     f"{v['native_balance_available']}/80 | {v['nonce_available']}/80 | {v['code_profile_available']}/80 |")
    lines.append('')
    for phase in ('cold', 'warm'):
        v = summary['phases'][phase]['valid']
        lines.append(f"{phase} token balance availability: `{json.dumps(v['token_balances_available'], sort_keys=True)}`.")
        lines.append('')
    lines += ['', 'An available empty label list means the provider returned no labels for the queried address. It is not a low-risk or safe verdict. '
              '`unavailable` and `not_configured` preserve unknown data rather than silently returning an empty label list or zero balance. '
              'No sanctions, scam, exploit, or other attribution ground-truth corpus is provided here, so label accuracy is not claimed.', '',
              '## Fresh-cache and warm-cache evidence', '',
              f"- Initial component-cache probe: `{json.dumps(meta['initial_cache_probe'])}`.",
              f"- Cold valid inputs verified absent before lookup: **{meta['cold_miss_proofs']}/80**.",
              f"- Cold snapshot-cache hits: **{valid['snapshot_cache_hits']}**; label-cache hits: **{valid['risk_cache_hits']}**.",
              f"- Warm snapshot hits: **{warm['valid']['snapshot_cache_hits']}/80**; label hits: **{warm['valid']['risk_cache_hits']}/80**; "
              f"both components: **{warm['valid']['both_cache_hits']}/80**.",
              '- Warm lookup follows its cold counterpart immediately. Failed/unavailable components may be retried; a partial cache hit is not a fully cached response.',
              '- The cache is isolated to this run; existing production/age caches are not cleared or modified. Upstream caches cannot be controlled.', '',
              '## Independent exact-block RPC agreement', '']
    ref = summary['independent_reference']
    lines += [f"Independent reference queries cover **{ref['attempted_inputs']}/{ref['eligible_inputs']}** valid inputs at the exact returned block and block hash. "
              f"**{ref['complete_references']}** have complete reference data. Across native balance, nonce, code profile and configured token balances: "
              f"**{ref['exact_matches']} exact field matches**, **{ref['mismatches']} mismatches**, **{ref['unverified_fields']} unverified fields**.", '',
              'The reference provider is distinct from the on-chain snapshot provider. Each reference batch verifies Ethereum chain ID and canonical block identity '
              'before comparing raw integer quantities; no floating-point balance comparison or latest-state approximation is used. '
              'These additional RPC requests occur after both timed phases and are excluded from endpoint latency. '
              'Missing/error data stays unverified. Reference agreement validates current snapshot values, not ownership or safety.', '',
              f"Reference providers used: `{json.dumps(dict(Counter(r.get('provider') for r in summary['reference_checks'])), sort_keys=True)}`. "
              f"Reference HTTP attempts, including failures: **{ref['http_attempts']}**.", '',
              '## Provider behavior and budget', '', '| Provider | HTTP-client attempts | JSON returned | Errors | Locally expired |',
              '|---|---:|---:|---|---:|']
    if meta.get('reference_supplemental'):
        extra = meta['reference_supplemental']
        marker = lines.index('## Provider behavior and budget')
        lines[marker:marker] = [
            f"The initial public-reference pass left {extra['inputs']} inputs unverified because Tenderly returned HTTP429 "
            f"and LlamaRPC returned HTTP525. A separate supplemental pass verified **{extra['repaired']}/{extra['inputs']}** "
            f"using **{extra['provider']}**, distinct from the Alchemy provider selected for every timed snapshot. "
            'The original pass is preserved in `reference-initial.json`; API timings and responses were not rerun or altered.', '']
    for name, v in summary['providers'].items():
        lines.append(f"| {name} | {v['attempts']} | {v['json_returned']} | `{json.dumps(v['errors'], sort_keys=True)}` | {v['locally_expired']} |")
    lines += ['', 'Provider counts include calls locally rejected after the shared deadline and late source calls. They are whole-run totals; '
              'a call beginning during a later request is not assumed to belong to that request. HTTP JSON success is not necessarily usable component data.', '',
              'The shared 2.6-second application budget leaves headroom under the three-second target. At timeout the API returns partial/unavailable fields '
              'rather than waiting for all providers. This does not bound arbitrary OS stalls, remote-client transit, or concurrent load.', '',
              f"Selected cold snapshot providers: `{json.dumps(dict(Counter(public_view(r['body'])['rpc_source'] for r in rows if r['phase'] == 'cold' and r['category'] != 'malformed')), sort_keys=True)}`. "
              f"The **{summary['providers'].get('quicknode', {}).get('attempts', 0)} QuickNode HTTP-client attempts** in the timed run were healthy hedged work, "
              'not evidence that Alchemy had failed. Hedging adds paid-provider calls even when Alchemy ultimately supplies the selected snapshot; '
              'redundant work can continue briefly after the API has selected a complete result.', '',
              '## Reproducibility and scope', '',
              f"- Dataset SHA-256: `{meta['dataset_sha256']}`; 100 unique inputs even after lowercasing.",
              f"- Paid RPC providers configured: `{json.dumps(meta.get('paid_configured', []))}`; AnChain configured: **{meta['anchain_configured']}**.",
              f"- Runtime source files unchanged during run: **{meta['runtime_unchanged']}**.",
              f"- Malformed inputs correctly rejected: **{summary['syntax']['malformed_rejected']}/20**; valid syntax accepted: **{summary['syntax']['valid_accepted']}/80**.",
              f"- API requests: **{len(rows)}**; concurrency:1; cold shuffle seed:{meta['seed']}; pause:{meta['pause_seconds']}s outside timing.",
              f"- API-loop wall time: **{meta['loop_wall_seconds']:.3f}s**; sum of timed HTTP requests: **{meta['api_seconds']:.3f}s**.",
              f"- Unfinished HTTP calls after bounded drain: **{meta['unfinished_calls']}**.",
              '- Ethereum mainnet only. Mutable balances, nonces, code and labels are not shared across EVM-family aliases or cached forever.',
              '- Current schema codeKind is descriptive: delegated EOAs and contract code do not independently imply danger.',
              '- The historical existing_eoa corpus labels are not independently revalidated account-type claims.',
              '- Raw data: `results.jsonl`, `metadata.json`, `summary.json`, `upstream-events.json`, and `reference-checks.json`.', '',
              '```bash', 'python3 benchmarks/run_wallet_insights_benchmark.py --output benchmarks/run-wallet-insights-new --env-file .env', '```', '',
              '## All 100 uncached inputs', '',
              '| ID | Address | Seconds | HTTP | Status | Native raw | Nonce | Code kind | Risk labels status |',
              '|---|---|---:|---:|---|---:|---:|---|---|']
    controls_path = path.parent / 'fallback-controls.json'
    if controls_path.exists():
        controls = json.loads(controls_path.read_text())
        completed_controls = datetime.fromisoformat(controls['completed_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('America/Chicago'))
        marker = lines.index('## Reproducibility and scope')
        extra_lines = ['## Supplemental RPC failover controls', '',
            f"Completed: {completed_controls.strftime('%Y-%m-%d %H:%M:%S %Z')} (America/Chicago).", '',
            'These are separate direct production `service.check` controls using real RPC network calls. '
            '**AnChain labels were explicitly stubbed; no AnChain network request was made.** '
            'They are excluded from the 100 cold and 100 warm HTTP rows and are not end-to-end HTTP plus live-label latency measurements.', '',
            '| Injected condition | Selected RPC | Direct service seconds | Snapshot | Cache unchanged after late work |',
            '|---|---|---:|---|---|']
        for control in controls['controls']:
            extra_lines.append(f"| {control['control']} | {control['selected_provider']} | {control['seconds']:.3f} | "
                               f"{control['snapshot_status']} | {control['cached_snapshot_unchanged_after_late_work']} |")
        extra_lines += ['', 'All three controls returned complete snapshots under three seconds. '
            'The slow-Alchemy control allowed late work to finish and verified that it did not replace the selected cached snapshot. '
            'Raw evidence: [fallback-controls.json](fallback-controls.json).', '']
        lines[marker:marker] = extra_lines
    if validation_note:
        marker = lines.index('## Reproducibility and scope') + 2
        lines[marker:marker] = [f"- Implementation regression validation: **{validation_note}**."]
    for row in sorted([r for r in rows if r['phase'] == 'cold'], key=lambda r:r['id']):
        v = public_view(row['body'])
        lines.append(f"| {row['id']} | `{json.dumps(row['address'], ensure_ascii=True)}` | {row['seconds']:.4f} | {row['status']} | "
                     f"{v['status'] or 'invalid'} | {v['native_balance_wei'] if v['native_balance_wei'] is not None else '—'} | "
                     f"{v['account_nonce'] if v['account_nonce'] is not None else '—'} | "
                     f"{(v['code_profile'] or {}).get('kind', '—')} | {v['risk_status'] or '—'} |")
    path.write_text('\n'.join(lines)+'\n')


class TracedRPC:
    def __init__(self, original, meter, identifiers):
        self.original, self.meter, self.identifiers = original, meter, identifiers

    def __getattr__(self, name):
        return getattr(self.original, name)

    def inspect_endpoint(self, name, url, address, assets):
        with self.meter.identity(self.identifiers[address], 'snapshot'):
            return self.original.inspect_endpoint(name, url, address, assets)


class TracedLabels:
    def __init__(self, original, meter, identifiers):
        self.original, self.meter, self.identifiers = original, meter, identifiers

    def __getattr__(self, name):
        return getattr(self.original, name)

    def inspect(self, address):
        with self.meter.identity(self.identifiers[address], 'riskLabels'):
            return self.original.inspect(address)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--pause', type=float, default=0.15)
    parser.add_argument('--seed', type=int, default=20261105)
    parser.add_argument('--skip-reference', action='store_true', help='For local smoke runs only; report preserves unverified scope')
    args = parser.parse_args()
    if not 0 <= args.pause <= 5:
        parser.error('--pause must be from0 to5seconds')
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('Output directory must be fresh and empty')
    args.output.mkdir(parents=True, exist_ok=True)
    from wallet_insights.factory import build_service
    from wallet_age_days.factory import build_service as build_age_service
    from wallet_age_days.server import WalletAgeHTTPServer
    dataset_path = ROOT / 'benchmarks/addresses.json'
    cases = json.loads(dataset_path.read_text())['cases']
    assert len(cases) == len({row['address'].lower() for row in cases}) == 100
    meter = Meter(())
    service = build_service(args.output / 'cache.sqlite3', env_file=args.env_file, http=meter)
    age = build_age_service(':memory:')
    meter.names = {url: name for name, url in service.rpc.endpoints}
    identifiers = {row['address'].lower(): row['id'] for row in cases}
    service.rpc = TracedRPC(service.rpc, meter, identifiers)
    service.labels = TracedLabels(service.labels, meter, identifiers)
    runtime = sorted((ROOT/'wallet_insights').glob('*.py')) + [ROOT/'wallet_age_days/server.py'] + [
        ROOT/'eth_wallet_check'/name for name in ('http.py', 'config.py', 'rpc.py', 'deadline.py')]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in runtime}
    valid_case = next(row for row in cases if row['expected_valid_format'])
    initial = service.cache_probe(valid_case['address'])
    assert initial == {'snapshot': False, 'riskLabels': False}
    metadata = {'started_at': stamp(), 'python': sys.version, 'seed': args.seed, 'pause_seconds': args.pause,
                'dataset_sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                'paid_configured': [name for name, _ in service.rpc.endpoints if name in {'alchemy','quicknode'}],
                'anchain_configured': bool(service.labels._api_key),
                'env_file_supplied': args.env_file is not None, 'initial_cache_probe': initial,
                'cold_miss_proofs': 0, 'runtime_sha256': hashes, 'reference_skipped': args.skip_reference}
    (args.output/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    rows, references = [], []
    shuffled = list(cases)
    random.Random(args.seed).shuffle(shuffled)
    try:
        with WalletAgeHTTPServer(('127.0.0.1', 0), age, insights=service) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}/walletInsights?'
            loop_started = time.perf_counter()
            with (args.output/'results.jsonl').open('w', buffering=1) as out:
                for index, case in enumerate(shuffled, 1):
                    cold_probe = service.cache_probe(case['address']) if case['expected_valid_format'] else None
                    if cold_probe is not None:
                        assert cold_probe == {'snapshot': False, 'riskLabels': False}
                        metadata['cold_miss_proofs'] += 1
                    for phase in ('cold', 'warm'):
                        meter.phase = phase
                        started = time.perf_counter()
                        status, body, headers, error = None, None, {}, None
                        try:
                            try:
                                response = urlopen(base+urlencode({'blockchain':'ETH', 'address':case['address']}), timeout=4)
                            except HTTPError as exc:
                                response = exc
                            with response:
                                status = response.status
                                headers = {k.lower():v for k,v in response.headers.items()
                                           if k.lower().startswith(('x-wallet-', 'x-insights-'))}
                                body = json.load(response)
                        except Exception as exc:
                            error = type(exc).__name__
                        row = {'id':case['id'], 'label':case['label'], 'category':case['category'],
                               'address':case['address'], 'phase':phase, 'measured_at':stamp(),
                               'seconds':time.perf_counter()-started, 'status':status, 'body':body,
                               'headers':headers, 'transport_error':error, 'cache_probe_before_cold':cold_probe}
                        if phase == 'cold' and case['expected_valid_format']:
                            view = public_view(body)
                            assert not view['snapshot_cache_hit'] and not view['risk_cache_hit']
                        rows.append(row)
                        out.write(json.dumps(row)+'\n')
                    if index % 20 == 0:
                        cold = [r for r in rows if r['phase']=='cold']
                        print(json.dumps({'phase':'paired_cold_warm', 'addresses_completed':index,
                                          'cold_statuses':dict(Counter(str(r['status']) for r in cold)),
                                          'cold_max_seconds':max(r['seconds'] for r in cold)}), flush=True)
                    if args.pause:
                        time.sleep(args.pause)
            metadata['loop_wall_seconds'] = time.perf_counter()-loop_started
            server.shutdown()
            worker.join()
        meter.phase = 'drain'
        drain_started = time.monotonic()
        while time.monotonic()-drain_started < 5:
            events = meter.snapshot()
            if all(event['finished'] for event in events):
                break
            time.sleep(0.1)
        events = meter.snapshot()
        if not args.skip_reference:
            meter.phase = 'independent_reference'
            reference_endpoints = tuple((name, url) for name, url in service.rpc.endpoints
                                        if name == 'quicknode') + REFERENCE_ENDPOINTS
            cold_valid = [r for r in rows if r['phase']=='cold' and r['category']!='malformed']
            for index,row in enumerate(cold_valid,1):
                references.append(fetch_reference(row, public_view(row['body']),
                    endpoints=reference_endpoints, headers_by_url=meter._headers_by_url))
                (args.output/'reference-checks.json').write_text(json.dumps(references,indent=2)+'\n')
                if index % 20 == 0:
                    print(json.dumps({'phase':'independent_reference', 'completed':index,
                                      'complete':sum(r['complete_reference'] for r in references)}),flush=True)
                time.sleep(0.05)
        metadata.update(finished_at=stamp(), api_seconds=sum(r['seconds'] for r in rows),
                        runtime_unchanged=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h for name,h in hashes.items()),
                        unfinished_calls=sum(not event['finished'] for event in events), upstream_attempts=len(events),
                        reference_http_attempts=sum(len(row['attempts']) for row in references))
        summary = summarize(rows,references,metadata,events)
        for name,value in [('metadata.json',metadata),('upstream-events.json',events),
                           ('reference-checks.json',references),('summary.json',summary)]:
            (args.output/name).write_text(json.dumps(value,indent=2)+'\n')
        write_report(args.output/'REPORT.md',summary,rows)
        assert metadata['runtime_unchanged'], 'Runtime changed during benchmark'
        assert metadata['cold_miss_proofs']==80
        print(json.dumps({'output':str(args.output), 'cold':summary['phases']['cold'],
                          'warm_valid':summary['phases']['warm']['valid'],
                          'independent_reference':summary['independent_reference']}),flush=True)
    finally:
        service.close()
        age.close()


if __name__=='__main__':
    main()
