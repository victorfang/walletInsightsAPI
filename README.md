# WalletInsights API

**V2 is implemented:** an age-first aiohttp service with latest-by-default routing, pinned `/v1` and `/v2` age routes, and a separate one-million-entry SQLite LRU. See the [V2 quick start and API](v2/README.md) and [architecture](v2/ARCHITECTURE.md). Legacy Insights remains available for backward compatibility. The documentation below describes the original V1 service; its Python/CLI entry points remain unchanged. V2 requires Python 3.11+ and its pinned aiohttp dependency.

Two separate endpoints share a small Python standard-library service:

- `walletAgeDays`: one integer, with a bounded LRU first-observation epoch cache and no TTL.
- `WalletInsights`: balances, raw nonce, code profile, and AnChain labels, with short independent component caches.

Python 3.10+ is sufficient for the original V1 runtime. Insights never adds fields
to the age response. V1 uses Python's standard library, including SQLite;
the new V2 runtime and full repository test suite require Python 3.11+ and aiohttp.
No Redis, Node.js or frontend build is needed.

## Tests and optional CI

After the [V2 dependency setup](v2/README.md#run), run
`v2/.venv/bin/python -m unittest discover -s tests -q` for the complete V1/V2 suite.
The current complete suite passes **419 tests**.
The [CI workflow template](docs/ci-tests.yml.example) runs Python 3.11 and 3.13
with the pinned dependency and no provider keys. To activate it, copy the template to
`.github/workflows/tests.yml` using a GitHub credential with workflow permission
or the repository web editor. The publishing credential did not have that
permission, so no active workflow was installed.

## Quick start

```bash
git clone https://github.com/victorfang/walletInsightsAPI.git
cd walletInsightsAPI
python3 -m wallet_insights --port 8787
```

This starts both endpoints with public RPC fallbacks. To enable AnChain labels
and configured paid providers for WalletInsights, copy `.env.example` to an
ignored local `.env`, replace or comment out its placeholders, then run:

```bash
python3 -m wallet_insights --port 8787 --env-file .env
```

`--env-file` configures both endpoints; shell settings take precedence over the
same file settings. Example RPC URL placeholders are ignored so they cannot mask
real Alchemy keys or QuickNode ETH URLs. A missing AnChain key
produces `riskLabels.status: "not_configured"`, never a safe verdict.

- [Code architecture and cache design](docs/ARCHITECTURE.md)
- [Third-party providers, API keys and configuration](docs/PROVIDERS.md)
- [Benchmark summary, raw evidence and reproduction](docs/BENCHMARKS.md)
- [V2 100-address benchmark](benchmarks/run-v2-final-2026-11-06/REPORT.md) and [V2 million-entry SQLite measurement](benchmarks/v2-cache-million.md)
- [New stopping-policy comparison and provider latency](benchmarks/FIRST_RESPONSE_BENCHMARK_2026-11-05.md)
- [Seven-chain EVM age research and live provider capabilities](EVM_WALLET_AGE_RESEARCH.md)
- [Concurrent age lookup, provider agreement and credential probe](docs/PROVIDER_AGREEMENT.md)
- [OpenAPI specification](openapi.json)

Run the full suite with `v2/.venv/bin/python -m unittest discover -s tests -q`. Live
benchmarks are separate commands and can consume provider quota. Historical
HTTP timings predate LRU; the current million-entry LRU store measurement is
identified separately in the benchmark documentation.

**walletAgeDays chain coverage is Ethereum mainnet only.** `eth`, `ethereum`,
`eth-mainnet`, `evm`, `polygon`, `matic`, `polygon-mainnet`, `matic-mainnet`,
`base`, `arbitrum`/`arb`, `optimism`/`op`, `bsc`/`bnb`, and `avalanche`/`avax`
select the same EVM address family. A `polygon` request currently searches
Ethereum history; it does not query Polygon history. The response header makes
this coverage explicit.
See the [EVM feasibility and cache design](WALLET_AGE_DESIGN.md).

## API

```bash
python3 -m wallet_age_days --port 8787
curl 'http://127.0.0.1:8787/walletAgeDays?blockchain=eth&address=0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045'
```

An illustrative successful response body is just:

```json
4056
```

The actual integer changes with time and the address's first observed epoch.
POST accepts the same two fields:

```bash
curl -X POST 'http://127.0.0.1:8787/walletAgeDays' \
  -H 'Content-Type: application/json' \
  -d '{"blockchain":"eth","address":"0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"}'
```

| Required input | Meaning |
| --- | --- |
| `blockchain` | Case-insensitive family selector; current aliases are listed above |
| `address` | EVM address matching `0x[0-9a-fA-F]{40}` with a full-string regex match |

Addresses are lowercased after validation. Regex validation checks format, not
EIP-55 checksum, ownership, or activity. Whitespace and ENS names are not accepted.
Extra fields and duplicate query parameters are invalid requests. The `walletAgeDays` endpoint has no
balance, nonce, count, or `details` fields and no response envelope.

| HTTP status | JSON response body | Meaning |
| --- | --- | --- |
| `200` | Nonnegative integer | Completed days since a finalized verified first observation |
| `400` | `"invalid"` | Address fails the EVM regex |
| `400` | `"invalid request"` | Missing, duplicate, malformed, or extra inputs |
| `401` | `"unauthorized"` | Local API key is required and missing or incorrect |
| `405` | `"method not allowed"` | Unsupported method |
| `404` | `"not_found"` | Completed source lookups did not find a usable observation |
| `501` | `"coming soon contact info@anchain.ai"` | Blockchain selector is not implemented |
| `503` | `"unknown"` | No usable epoch because of a deadline, provider error, or unavailable capacity |

An empty source result is scoped evidence, not proof that an address has never
appeared anywhere. Provider failures do not become `0` or `not_found`. An observed
event less than 24 hours old can correctly return `0`.

Metadata lives in headers, keeping the successful body an integer:

| Header | Values / meaning |
| --- | --- |
| `X-Wallet-Age-Coverage` | `ethereum` today; chains actually covered |
| `X-Wallet-Age-Cache` | `hit`, `miss`, or `bypass` |
| `X-Wallet-Age-Basis` | `first-observed-activity` |
| `X-Wallet-Age-Quality` | `lower-bound`; complete earliest history is not proven |
| `X-Wallet-Age-Finalized` | `true` on successful age responses |
| `Cache-Control` | `no-store`; the application owns epoch caching |

`/health` and `/openapi.json` are public. The [OpenAPI specification](openapi.json)
describes this local standalone service; it is not a deployment to
`data.anchainai.com`.

## WalletInsights

The [pre-LRU 100-address WalletInsights benchmark](benchmarks/run-wallet-insights-2026-11-05/REPORT.md)
used configured Alchemy, QuickNode, and AnChain providers. All 80 valid uncached
inputs returned complete snapshots and available labels: median **0.685 s**,
p95 **0.807 s**, maximum **0.884 s**. All 20 malformed inputs were rejected.
Independent exact-block checks matched **480/480 fields**. Label availability
is measured separately from label accuracy or safety; no labeled fraud truth
set is asserted. The report includes warm-cache timings and supplemental RPC
failure controls. These measurements predate the LRU recency index and writes.

`GET /WalletInsights` and `POST /WalletInsights` accept exactly `blockchain`
and `address`. Unlike the age endpoint's family aliases, this endpoint only
accepts actual Ethereum selectors: `eth`, `ethereum`, and `eth-mainnet`, case
insensitively. `EVM`, `Polygon`, and all other chains return `501` with the JSON
string `"coming soon contact info@anchain.ai"`. Balances and code must not be
reused across chains merely because the address is the same.

Start the combined service with explicitly selected credentials:

```bash
python3 -m wallet_insights --port 8787 --env-file .env
```

The `python3 -m wallet_age_days` startup also serves both endpoints and accepts
`--env-file`. The file is optional, only relevant configuration keys are read,
and its contents are never evaluated as shell code. Shell environment values
take precedence. Credentials are not returned or logged. AnChain credentials are
sent only to the official HTTPS `api.anchainai.com` origin; authenticated
redirects are rejected.

```bash
curl -sS 'http://127.0.0.1:8787/WalletInsights?blockchain=ETH&address=0xd8da6bf26964af9d7eed9e03e53415d37aa96045'

curl -sS -X POST 'http://127.0.0.1:8787/WalletInsights' \
  -H 'Content-Type: application/json' \
  -d '{"blockchain":"ETH","address":"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"}'

python3 check_wallet_insights.py ETH \
  0xd8da6bf26964af9d7eed9e03e53415d37aa96045 --env-file .env
```

Illustrative response values below describe the shape, not the live address
above. The [OpenAPI document](openapi.json) includes complete, partial and
unavailable examples.

```json
{
  "status": "complete",
  "blockchain": "eth",
  "chainId": 1,
  "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "checkedAt": "2026-11-05T20:00:00Z",
  "block": {
    "number": 26350000,
    "hash": "0x1111111111111111111111111111111111111111111111111111111111111111",
    "timestamp": 1793908800,
    "source": "alchemy"
  },
  "balances": {
    "native": {
      "symbol": "ETH",
      "decimals": 18,
      "balanceRaw": "1000000000000000000"
    },
    "tokens": [
      {
        "symbol": "USDC",
        "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "decimals": 6,
        "balanceRaw": "250000000",
        "status": "available"
      },
      {
        "symbol": "USDT",
        "address": "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "decimals": 6,
        "balanceRaw": "0",
        "status": "available"
      },
      {
        "symbol": "DAI",
        "address": "0x6b175474e89094c44da98b954eedeac495271d0f",
        "decimals": 18,
        "balanceRaw": "0",
        "status": "available"
      }
    ]
  },
  "accountNonce": "4",
  "codeProfile": {
    "kind": "empty_code",
    "codeSize": 0,
    "delegationTarget": null
  },
  "riskLabels": {
    "status": "available",
    "source": "anchain",
    "labels": [
      {
        "kind": "attribution",
        "category": "unaffiliated",
        "source": "anchain"
      }
    ],
    "attribution": {
      "category": [
        "unaffiliated"
      ],
      "detail": [],
      "information": []
    },
    "checkedAt": "2026-11-05T20:00:00Z",
    "providerVerdictTime": null
  },
  "components": {
    "snapshot": "complete",
    "riskLabels": "available"
  },
  "cache": {
    "snapshot": {
      "hit": false,
      "ttlSeconds": 15,
      "capturedAt": "2026-11-05T20:00:00Z",
      "expiresAt": "2026-11-05T20:00:15Z"
    },
    "riskLabels": {
      "hit": false,
      "ttlSeconds": 300,
      "capturedAt": "2026-11-05T20:00:00Z",
      "expiresAt": "2026-11-05T20:05:00Z"
    }
  },
  "warnings": []
}
```

| Component | Behavior |
| --- | --- |
| Balances | Native ETH plus fixed trusted Ethereum USDC, USDT and DAI contracts; raw decimal strings preserve precision |
| `accountNonce` | Raw nonce string; not total transactions or exact outgoing transaction count |
| `codeProfile` | `empty_code`, `delegated`, or `contract_code`, code size, and optional delegation target; descriptive, not a safety verdict |
| `riskLabels` | AnChain attribution and OSINT categories with `kind` and `source`; detail and link are preserved when provided |
| `block` | Shared block number, hash and timestamp for all snapshot reads; source is a provider name, not a private URL |
| `cache` | Separate hit, capture, expiry, and TTL for the state snapshot and labels |

EOAs can have delegation code and authorization processing can increment their
nonce under [EIP-7702](https://eips.ethereum.org/EIPS/eip-7702). Neither code
presence nor a high nonce identifies a malicious or trustworthy owner.
An `unaffiliated` label, an empty successful label list, or a source-specific
OSINT association does not establish safe or malicious status. This API does not
fetch a risk score or derive a safe verdict from labels.

HTTP `200` may be `complete` or `partial`; inspect `status`, `components`, and
nullable fields before making an outflow decision. A failed label lookup returns
`labels: null`, not an empty safe-looking list. Missing balances, nonce or code
remain null. When neither component is available, HTTP `503` returns the same
object with `status: "unavailable"`. An unexpected framework failure or absent
Insights handler can instead return the JSON string `"unknown"` with HTTP `503`;
clients must preserve that unresolved outcome. Request/authentication errors remain JSON
strings: `400 "invalid"`, `400 "invalid request"`, `401 "unauthorized"`, or
`501 "coming soon contact info@anchain.ai"`.

Insights responses include `X-Wallet-Insights-Coverage: ethereum` and
`X-Wallet-Insights-Cache: hit|mixed|miss`. A hit means both components were
cached; mixed means one was cached. `Cache-Control: no-store` leaves freshness
under the application cache. Age-specific headers belong to the age route.

Snapshots have a **15-second** TTL; successful AnChain labels have a separate
**300-second** TTL. Failed components are not cached. Snapshot keys include the
physical chain, address and configured asset set; labels use chain and address.
The Insights cache does not share the age database. Top-level `checkedAt` is the
response time; cached components retain their original `capturedAt`, `expiresAt`,
label check time, and chain block. `providerVerdictTime` is only populated when
AnChain supplies it.

Both systems use persistent **least recently used (LRU)** eviction with a default
hard limit of **1,000,000 entries per database**. For Insights, snapshots and
AnChain label results are separate entries in the same bounded database; one
address can therefore occupy two entries. A successful read or write makes that
entry most recently used. Expired Insights entries are removed before a fresh
entry is evicted for space. Reading a cached value updates recency without
extending its 15-second or 300-second expiration or changing its capture time.
At capacity, a new entry replaces the least recently used retained entry.

RPC reads are pinned and batched. Alchemy and QuickNode are preferred, followed
by PublicNode, 1RPC and BlockPI; delayed or failed attempts trigger hedged
fallback work. Labels run concurrently under the same **2.6-second application
budget**. Deadline responses preserve whatever completed data is available.
Complete third-party data and arbitrary internet/OS delays cannot be guaranteed
within three seconds. This endpoint does not query transaction history, wallet
age, arbitrary token lists or portfolio USD valuation.

| Additional configuration | Meaning |
| --- | --- |
| `WALLET_INSIGHTS_DB` | Separate mutable SQLite cache, default `data/wallet-insights.sqlite3` |
| `ALCHEMY_RPC_URL` / `ALCHEMY_API_KEY` | Optional paid Ethereum RPC; full URL takes precedence |
| `QUICKNODE_RPC_URL` | Optional paid Ethereum RPC URL |
| `QUICKNODE_ETH_URL` + `QUICKNODE_ETH_AUTH_TOKEN` | Alternative URL plus `x-token` authentication |
| `ANCHAIN_DATA_API_BASE_URL` | Official HTTPS `https://api.anchainai.com` origin only |
| `ANCHAIN_DATA_API_KEY` / `ANCHAIN_API_KEY` | AnChain credential; sent as `x-api-key` |
| `WALLET_CHECK_API_KEY` | Optional incoming local API authentication, configured through the shell |

When the AnChain credential is absent, the snapshot can still succeed while
`riskLabels.status` is `not_configured` and labels stay null. No risk-label
fallback is invented from ordinary RPC state. Client decisions should preserve
partial/unknown status, actual-chain coverage, and the observation times.

## Withdrawal destination screening use case

Use `walletAgeDays` before signing or broadcasting a withdrawal, as one input to
the client's fraud checks. A successful `0` means the earliest finalized
observation found is less than 24 hours old; it does not mean no history exists
or prove that a private key was just created. The cache saves the positive event
epoch, not the number zero, so its age increases without another provider call.
Very recent activity that cannot yet be finalized can return `503 "unknown"`.

Example: a compromised customer account adds a withdrawal destination. The API
returns `0`. The client's own beneficiary history says this destination is new
for that customer, and its session system detects a new device. Keep the
withdrawal pending and require strong reauthentication through an established
trusted channel or manual review. A familiar address can still be compromised;
an older age does not authorize a withdrawal.

An illustrative policy, with a configurable seven-day threshold:

| Evidence | Client action |
| --- | --- |
| `200` and age `0` through `6` | Additional verification for recent observed activity |
| `200` and age at least `7` | Continue existing fraud and KYT checks |
| Any age, destination newly added for this customer | Verify the new beneficiary |
| Account-takeover signal at any age | Keep pending for review |
| `404 "not_found"` | Additional verification for an unobserved destination |
| `503 "unknown"`, timeout, or wrong chain coverage | Keep pending; retry later within policy or review |
| `400 "invalid"` | Reject the malformed destination |

Seven days is a policy example, not a validated threshold for identifying a
hacker. Clients should calibrate it against their own labeled withdrawal
outcomes and false-positive tolerance. Legitimate customers also create new
addresses. Combine age with beneficiary novelty, withdrawal amount and velocity,
session/device changes, and existing address-risk screening.
[Wallet-screening context](https://www.chainalysis.com/glossary/wallet-screening/).

The runnable [client example](examples/withdrawal_age_gate.py) returns an action
and reason; it never sends or approves a withdrawal. Its decisions are client
policy output; the API itself still returns only an integer on success.

```bash
python3 examples/withdrawal_age_gate.py ETH \
  0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 --new-beneficiary
```

Flags such as new-beneficiary and account-risk must come from the client's
trusted backend, not from an end user's request. Persist the screening result,
API status, actual chain coverage, and decision with the withdrawal audit trail.
After additional verification, the client's own policy decides whether to allow,
limit, delay, or review the withdrawal. `continue_other_checks` is not approval.

For this use case, check the actual withdrawal network. Current coverage is only
Ethereum. The example rejects Polygon/EVM-family ambiguity for chain-specific
screening, and checks `X-Wallet-Age-Coverage` and finality headers. A family-wide
minimum age cannot establish first use on one particular chain.

The 100-address benchmark measures latency and historical controls, not fraud
detection accuracy. Its fabricated addresses returned unknown under public-feed
rate limits. Do not treat those unknowns as proven new addresses or old, safe
destinations. Never write `if not age` in client code: integer `0` is valid.

## Direct Python script

```bash
python3 check_wallet_age_days.py eth 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
```

The script shares the HTTP checker's implementation and cache. Both support
`--cache-db`; the script also accepts an upstream `--timeout` of at most 2.6
seconds. Configuration comes from the environment; `.env` files are not loaded
automatically.

For an existing Python application, the same implementation is importable:

```python
from wallet_age_days import walletAgeDays

days = walletAgeDays("eth", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
```

The function returns an integer or raises `wallet_age_days.service.WalletAgeError`
with the same status/message meanings as the HTTP contract. Script exit codes
are `0` for success, `2` for invalid input, `3` for unknown/unavailable, `4` for
not found, and `5` for an unsupported blockchain.

```bash
export ALCHEMY_RPC_URL='https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY'
export QUICKNODE_RPC_URL='https://YOUR_ENDPOINT.quiknode.pro/YOUR_TOKEN/'
export WALLET_AGE_DB='/path/to/wallet-age-days.sqlite3'
python3 -m wallet_age_days --port 8787
```

| Environment variable | Purpose |
| --- | --- |
| `ALCHEMY_RPC_URL` | Existing Ethereum mainnet Alchemy endpoint |
| `ALCHEMY_API_KEY` | Alternative key; the full RPC URL takes precedence |
| `QUICKNODE_RPC_URL` | Existing Ethereum mainnet QuickNode endpoint |
| `WALLET_AGE_QUICKNODE_HISTORY=1` | Opt in to indexed history when the QuickNode Token and NFT API v2 add-on is enabled |
| `WALLET_AGE_DB` | Epoch database; default `data/wallet-age-days.sqlite3` |
| `WALLET_CHECK_API_KEY` | Optional local credential; clients send `x-api-key` |

Paid endpoints are optional. Supply credentials through your shell or secret
manager. Private endpoint URLs are not returned by the API. When
`WALLET_CHECK_API_KEY` is set, age requests require that incoming header. The
default server listens on localhost; use an existing reverse proxy for remote
serving.

## Lookup and latency

1. Validate the two inputs and normalize the EVM address.
2. Read `(coverage namespace, address) → first_appearance_epoch` from SQLite.
3. A hit calculates the integer locally and makes zero provider calls.
4. A miss races small indexed-history jobs in a bounded worker pool under a
   shared 2.6-second application budget. HTTP timeouts use the remaining budget.
5. Return a finalized verified age or an explicit error. Unverified or
   provisional observations alone return `503 "unknown"` and are not cached.

The budget leaves headroom below the requested three-second response target.
It bounds provider waiting, not internet transit or operating-system stalls,
and cannot guarantee complete third-party data within that time. The service
does not scan blocks or paginate arbitrary full histories.

Indexed sources are Alchemy's ascending incoming/outgoing transfers when
configured, Routescan, Blockscout, and complete small BlockCypher histories.
QuickNode indexed history is optional because ordinary paid RPC does not imply
the required add-on is enabled. Newest-first truncated history is never used as
an oldest-event result.

Configured Alchemy/QuickNode RPC and PublicNode, 1RPC, and BlockPI verify discovered
events through receipts, canonical blocks and finality. Standard RPC does not
provide a universal address-birthday method. No balances or transaction totals
are fetched for this API.

Source coverage can omit token events, approvals, internal calls, genesis
allocations, or other activity. The result is age since the earliest observation
found in covered histories, not a guarantee of the globally first mention on
every blockchain. See [primary-source research and limitations](WALLET_AGE_DESIGN.md).

## One-million-entry LRU age cache

The [pre-LRU 100-input uncached benchmark](benchmarks/run-uncached-2026-11-05/REPORT.md)
used a fresh isolated cache and public sources only, with zero cache hits.
All 40 known-used addresses returned ages: median **1.374 seconds**, p95
**1.800 seconds**, maximum **1.946 seconds**. All 100 inputs finished within
three seconds. The other outcomes were 40 synthetic valid candidates returning
`503 "unknown"` and 20 malformed inputs returning `400 "invalid"`.
All 20 independent deployment controls matched. The report separates successful
age lookups from overall latency and includes provider rate limits, raw results,
and reproducible commands. The [previous cold/warm benchmark](benchmarks/run-wallet-age-days-2026-11-05/REPORT.md)
remains available for historical cache timing comparisons. These runs predate the LRU change.

The compact SQLite store keeps a namespace, normalized address and integer
epoch. It does not load a million-address dictionary into RAM or require a
separate database server. Every hit computes:

```python
age_days = (int(time.time()) - first_appearance_epoch) // 86_400
```

Epochs have no TTL, but entries can be evicted under LRU. For each retained
entry, an earlier verified observation can decrease the saved epoch; a later
observation cannot replace it. Unknown, invalid, missing and provisional results
are never stored as age facts. This API has no 24-hour result cache.

There is a **hard maximum of 1,000,000 entries**. A successful `get` or
`put_min` makes that entry most recently used. When a new address is inserted
into a full cache, the least recently used entry is removed and the new entry
is admitted. The victim is selected by last use, not its blockchain epoch or
insertion age. An evicted address triggers provider discovery on its next check,
which can incur API cost again. The store is a cache, not a permanent archive.

SQLite persists recency and serializes touch, eviction and admission across
processes. Existing databases migrate automatically while retaining their epochs;
legacy rows with no usage history receive deterministic tie-breaking order.
The full address set remains on disk rather than being loaded into a Python
million-entry dictionary.

The [current LRU million-row test](benchmarks/wallet-age-days-lru-million.json)
populated **1,000,000 entries** and verified full-cache replacement, retention of
a recently read entry, and an unchanged row count. It measured **136.28 MiB on
disk**, **20.59 MiB peak benchmark-process RSS**, and **1.07 ms median / 1.82 ms
p95** for reads including a persisted LRU update. Inserting at capacity took
**1.18 ms**. These are local store measurements with a warm OS filesystem cache,
not full-service or upstream latency measurements.

The [historical compact-store million-row test](benchmarks/wallet-age-days-million.json)
measured **66.30 MiB on disk**, **21.27 MiB peak benchmark-process RSS**, and
**0.478 ms median lookup** before LRU recency writes and its index were added.
Those are pre-LRU measurements, not current LRU storage or timing claims. The
experiment used a warm OS filesystem cache and did not measure full-service RAM
or upstream latency.

The current namespace is `evm:1:v1`: the covered EVM family contains Ethereum
chain ID 1. All supported aliases share it. Adding covered chains requires a new
namespace or explicit coverage tracking; an ETH-only hit must not become an
assertion of completed Polygon or all-EVM discovery.

Use SQLite's backup facility or stop the service before copying its database.
A running WAL database can also have `-wal` and `-shm` files. Age epochs need no
expiry job; cache capacity is maintained by LRU eviction.

## Validation and historical files

`wallet_age_days/` is the new execution path. Older `eth_wallet_check/` modules
and benchmark reports remain as historical references. Their response envelope,
balance/count fields and 24-hour cache are not this API's contract.

```bash
python3 -m unittest discover -s tests -q
```

The [earlier benchmark](benchmarks/run-fast-2026-11-05/REPORT.md) used a broader
checker and richer cache schema. Its latency and one-million-row storage figures
must not be presented as measurements of this rewritten checker or compact store.
