# WalletAgeDays — V2

**Fast address-history signals for security and anti-fraud review.** This small API helps DeFi clients screen withdrawal destinations, adjust outflow limits and send suspicious cases to their risk team. It is designed for risk triage rather than exact portfolio accounting.

Clients can make their first authenticated staging call using the [client quick start](../docs/CLIENT_QUICKSTART.md).

V2 is implemented as four Python files with aiohttp. It runs locally or on your server; this change does not update the deployed demo Site. See the [architecture](ARCHITECTURE.md), [public configuration](config.toml) and [dependency pin](requirements.txt).

For client staging, use the [Railway and Route 53 deployment guide](DEPLOY_RAILWAY.md). The repository includes a Dockerfile; Railway can manage HTTPS for `api-staging.anchainai.com`.

## Run

Use Python 3.11 or newer, from the repository root:

```bash
python3 -m venv v2/.venv
v2/.venv/bin/python -m pip install -r v2/requirements.txt
v2/.venv/bin/python -m v2.app --env-file .env
```

This reuses your existing root `.env` without modifying it. Runtime dependency: `aiohttp==3.14.5` and its dependencies; SQLite and TOML support come with Python. The default address is `http://127.0.0.1:8787`; use `--host` and `--port` to change it. Stop the old service first if it already occupies that port.

For a separate demo configuration, copy [`.env.example`](.env.example) to an ignored `v2/.env` and start with `--env-file v2/.env`. The example includes a public test key. For client testing, set one `WALLET_CLIENT_KEY_<ID>` variable per client and send its value in the existing `x-api-key` header. The existing `WALLET_CHECK_API_KEY` still works. Railway requires a configured key for data routes. A local run with no keys accepts anonymous calls. See [client setup and log filters](DEPLOY_RAILWAY.md#client-keys-and-access-logs).

## Calls and versions

| Route | Implementation |
| --- | --- |
| `/walletAgeDays` | Latest age API; currently V2 |
| `/latest/walletAgeDays` | Same latest alias |
| `/v2/walletAgeDays` | Pinned V2 |
| `/v1/walletAgeDays` | Original V1 checker |
| `/walletInsights`, `/latest/walletInsights`, `/v1/walletInsights` | Original V1 compatibility endpoint |
| `/v2/walletInsights` | `501`, `"coming soon contact info@anchain.ai"` |

`[api].latest = "latest"` selects the newest registered compatible age implementation automatically; currently V2. An explicit version can override this for rollback. The original `/WalletInsights` spelling is also accepted for existing callers. Pinned routes do not move when the latest alias changes. Future latest implementations must keep published inputs, response bodies and error semantics compatible; regression tests guard that contract. Existing Python/CLI entry points also remain available. Versioned OpenAPI documents use versioned server bases, so generated clients remain pinned.

The canonical Insights operations are `GET /walletInsights` (OpenAPI ID `walletInsights`) and `POST /walletInsights` (ID `walletInsightsPost`). Regenerating an SDK may change the corresponding method names. Previously generated clients can continue to call the uppercase HTTP alias.

These examples use the public key from `v2/.env.example`. Replace it with your configured key, or omit the header when incoming authentication is disabled.

```bash
curl -i 'http://127.0.0.1:8787/walletAgeDays?blockchain=ETH&address=0xd8da6bf26964af9d7eed9e03e53415d37aa96045' \
  -H 'x-api-key: demo_wallet_age_test_key_replace_me'

curl -i -X POST 'http://127.0.0.1:8787/latest/walletAgeDays' \
  -H 'x-api-key: demo_wallet_age_test_key_replace_me' \
  -H 'Content-Type: application/json' \
  -d '{"blockchain":"ETH","address":"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"}'
```

An illustrative response is below; the integer depends on the address, observed history and current time:

```http
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
X-API-Version: v2
X-Wallet-Age-Coverage: ethereum
X-Wallet-Age-Quality: lower-bound
Cache-Control: no-store

365
```

GET and POST accept exactly `blockchain` and `address`. Duplicate or extra fields are rejected. Successful age bodies remain bare integers; errors retain the existing JSON strings, including `400 "invalid"`, `404 "not_found"`, `503 "unknown"` and the unsupported-chain message above. Existing cache, finalized, evidence and source-count headers are preserved. [The browser API reference](https://walletinsightsapi-production.up.railway.app/docs) documents the two current client endpoints. All documentation URLs display the same latest reference. Its [OpenAPI schema](https://walletinsightsapi-production.up.railway.app/openapi.json?view=latest) contains their unversioned GET and POST operations. Existing versioned API and schema URLs remain available.

Railway serves the Scalar renderer and the AnChain favicon from `v2/static/`. The browser needs only the wallet API host. To update Scalar, copy `dist/browser/standalone.js` from the verified npm package, update the versioned filename in `docs.html`, and include the upstream license. Docker precompresses the bundle for browsers that accept gzip. [Scalar package 1.77.0](https://www.npmjs.com/package/@scalar/api-reference/v/1.77.0) · [MIT license](static/Scalar-LICENSE)

## Age semantics and provider setup

- **Ethereum first.** Current EVM-family aliases retain Ethereum-only coverage, which is reported explicitly. Broader chain coverage needs configured, validated feeds.
- **Persistent cache.** Store the earliest verified observed epoch in a one-million-entry SQLite LRU. On a hit, calculate `(now - epoch) // 86400` without an upstream request. Records have no TTL; earlier discoveries can reduce the saved epoch.
- **Bounded misses.** Query history sources concurrently. Return after two usable independent provider groups, or one after the one-second soft deadline. Already-started work may refine the minimum within the original 2.6-second budget. If all sources finish earlier, no artificial delay is added. The uncached response target is under three seconds; unavailable evidence can return an error.
- **One purpose.** V2 age does not request balances, nonce, code profiles or risk labels. Indexed token/NFT transfers may be used solely as first-activity evidence.

`0` means a verified first observation is less than 24 hours old. An unseen address, provider failure or incomplete history does not become `0`. Observed age can underestimate true age if earlier activity has not been discovered. A long history or large balance does not prove the current controller or withdrawal is safe; clients combine this signal with their other controls.

Public endpoints and budgets live in [config.toml](config.toml). Secrets/private URLs live in `.env` or the process environment; process settings take precedence. The loader reads simple `KEY=value` lines without executing shell commands. Database paths are relative to `config.toml`; `--env-file` is relative to the working directory.

| Provider | Role | Optional secret/configuration |
| --- | --- | --- |
| Alchemy | Transfer history and canonical RPC verification | `ALCHEMY_API_KEY` or `ALCHEMY_RPC_URL` |
| Etherscan | Indexed normal/creation history | `ETHERSCAN_API_KEY` |
| QuickNode | Canonical RPC verification | `QUICKNODE_ETH_URL` or `QUICKNODE_RPC_URL`; optional `QUICKNODE_ETH_AUTH_TOKEN` |
| Routescan, Blockscout | Public indexed-history fallbacks | No key in the supplied configuration |
| BlockPI, PublicNode | Public RPC verification fallbacks | No key in the supplied configuration |

Ordinary RPC nodes verify discovered events; they do not provide arbitrary address-history search. Missing paid credentials disable their feeds; free-source rate limits and incomplete history may still produce `unknown`. Provider errors and credentials are not returned or access-logged.

## Compatibility and later balance signals

V1 age and Insights are lazily loaded only when a legacy route is called. Their original bodies, labels and existing databases are retained for compatibility. This is the sole V1 integration at the HTTP edge; the V2 age service, feed adapters and cache remain independent. AnChain credentials are unnecessary for V2 age, but the legacy Insights endpoint continues to use its original configuration when called. Set `[legacy].enabled = false` to disable legacy checks if compatibility is not required.

A later V2 balance route may provide current native ETH and at most five configured token contracts for funding context. No automatic token enumeration, junk-token scanning or complete portfolio calculation is planned. Historical peak research concerns native ETH only.

An exact lifetime ETH peak remains conditional on a suitable source. A sampled historical maximum or maximum observed since tracking began must state its coverage and freshness; the [architecture](ARCHITECTURE.md#optional-later-balance-signals) records the provider study. New V2 balance signals will not call AnChain labels; clients can use AnChain's separate label API.

| V2 data route | Separate database | LRU capacity |
| --- | --- | ---: |
| `walletAgeDays` | `v2/data/wallet-age.sqlite3` | **1,000,000 entries** |
| Future balance signals | `v2/data/wallet-peak.sqlite3` | **100,000 entries**, reserved; not implemented |

Each route evicts its own least recently used entry when admitting a new key at capacity. Future balance/peak records need a refresh policy because balances and historical maxima can change. The reserved 100k limit does not change the frozen V1 Insights cache.

## Tests and benchmark

```bash
v2/.venv/bin/python -m unittest discover -s tests -p 'test_v2_*.py' -v
v2/.venv/bin/python -m unittest discover -s tests -q
v2/.venv/bin/python benchmarks/run_v2_benchmark.py \
  --env-file .env --output benchmarks/run-v2-new
```

The complete suite passes **428 tests**, including 84 V2 tests. Tests use local fixtures and need no provider credentials. The [V2 benchmark runner](../benchmarks/run_v2_benchmark.py) makes live calls, consumes provider quota, uses the same 100 addresses, and creates an isolated database and Markdown report in a fresh output directory. It records cold and warm HTTP timing, initial/refined epochs and deployment-control agreement.

The [November 6 V2 benchmark](../benchmarks/run-v2-final-2026-11-06/REPORT.md) returned ages for all 40 known-used controls, rejected all 20 malformed inputs and returned `503 "unknown"` for the 40 synthetic candidates. Known-used uncached median/p95 were **0.723/0.996 seconds**. Maximum across all 100 cold requests was **1.017 seconds**. The 40 positive cache repeats had median **0.790 ms**. The measured build predates per-client access logs. This is local HTTP with a fresh application cache, one client and provider caches outside our control; it is not a hosted latency SLA.

Exact deployment-epoch agreement was **17/20**, with one background epoch correction. Those controls do not establish universal first-appearance or fraud-detection accuracy. The raw results and source hashes are linked in the report. Earlier [V1 measurements](../benchmarks/FIRST_RESPONSE_BENCHMARK_2026-11-05.md) remain historical rather than a paired performance comparison.

The separate [one-million-entry capacity check](../benchmarks/v2-cache-million.md) passed capacity, minimum epochs, LRU replacement and persisted recency. Its SQLite file was about **151 MiB**, with median cache-hit time **0.124 ms** at capacity; this measures synthetic local database operations, not upstream age accuracy or full HTTP latency. Reproduce it with `v2/.venv/bin/python benchmarks/benchmark_v2_cache.py --entries 1000000 --output benchmarks/v2-cache-new.json`.
