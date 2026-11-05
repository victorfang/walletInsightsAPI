# Architecture and code map

The repository serves two separate Ethereum APIs from one Python standard-library
HTTP process. Their data models and databases are independent. The complete
HTTP contract is in [openapi.json](../openapi.json).

## Entry points

| File or package | Responsibility |
| --- | --- |
| `wallet_insights/__main__.py` | Starts the combined HTTP service through `wallet_age_days.__main__` |
| `wallet_age_days/__main__.py` | Parses server options, builds both services and closes their caches on shutdown |
| `wallet_age_days/server.py` | GET/POST routing, two-field input contract, optional incoming API key, JSON responses |
| `check_wallet_age_days.py` | One-shot age CLI; prints an integer or JSON-string error |
| `check_wallet_insights.py` | One-shot Insights CLI; prints its structured response |
| `examples/withdrawal_age_gate.py` | Example client triage policy; never approves or submits withdrawals |

`check_eth_wallet.py`, `check_address_age.py`, and `python3 -m eth_wallet_check`
are compatibility entry points. Historical broader-checker code remains under
`eth_wallet_check/` for benchmark reproducibility. The active HTTP service is
`wallet_age_days/server.py`, not the historical server module.

## walletAgeDays request path

1. `wallet_age_days/service.py` validates the selector and address with a full
   regex match, then normalizes the EVM address.
2. `wallet_age_days/store.py` reads the `(coverage namespace, address)` epoch.
   A hit updates LRU recency and computes completed days using the current time.
3. On a miss, `wallet_age_days/providers.py` supplies independent indexed source
   jobs. The service races them within its shared application deadline.
4. A successful numeric result requires a finalized verified observation. The
   earliest qualifying epoch found is saved with `MIN(epoch)` semantics.
5. The HTTP success body is one integer. Unknown, unobserved, invalid and
   unsupported results use distinct HTTP errors rather than fabricated ages.

The epoch has no TTL while retained. It is still an LRU cache entry and can be
replaced when capacity is reached. Eviction means the next request must query
providers again. A finalized observation is durable evidence; it does not prove
that every earlier activity category or EVM chain was searched.

The current namespace is `evm:1:v1`, covering Ethereum mainnet. Age selectors
including `eth`, `evm`, and `polygon` choose this same family today, but only
Ethereum history is searched. Extending covered chains must change the namespace
or introduce explicit coverage tracking. See [WALLET_AGE_DESIGN.md](../WALLET_AGE_DESIGN.md).

## WalletInsights request path

`wallet_insights/factory.py` loads explicit configuration and builds the RPC,
AnChain-label and cache adapters. `wallet_insights/service.py` accepts only actual
Ethereum selectors (`eth`, `ethereum`, `eth-mainnet`) and coordinates two components:

- `wallet_insights/rpc.py` obtains chain ID and head-block identity, then batches
  native balance, raw nonce, account code and fixed USDC/USDT/DAI `balanceOf`
  calls at that block height. One coherent provider snapshot is selected; fields
  from different provider blocks are not merged.
- `wallet_insights/anchain.py` requests AnChain's ETH address-label endpoint. It
  preserves attribution and OSINT provenance. It does not fetch or invent a
  risk score, safe verdict, ownership proof or withdrawal approval.

Labels and RPC reads run concurrently. Configured RPC providers are preferred,
with delayed or failed attempts triggering hedged fallback work. A response can
be `complete`, `partial`, or `unavailable`. HTTP 200 alone does not imply that
all risk information is present: clients must inspect component statuses and
nullable fields. Missing balances stay null rather than becoming zero, and
missing labels stay null rather than becoming an empty safe-looking list.

Balances and nonce use decimal strings to preserve integer precision. Code kind
is descriptive (`empty_code`, `delegated`, `contract_code`): EIP-7702 means that
code presence is not an EOA-versus-contract safety test, and nonce is not an
exact total or outgoing transaction count.

## Deadlines, transport and failure handling

The active services reuse `eth_wallet_check/deadline.py` and
`eth_wallet_check/http.py`. A process-wide executor has 24 workers and no pending
job backlog. Excess work is rejected promptly. The application budget is 2.6
seconds, with headroom reserved for cache writes and response serialization.
Each provider HTTP timeout is capped by the remaining deadline.

A deadline bounds how long the caller waits, not arbitrary DNS, operating-system
stalls, or internet transit. Running threads cannot be forcibly interrupted;
late workers cannot write cache entries or mutate a returned response. The
services return partial/unavailable evidence rather than extending provider
waiting indefinitely.

The JSON transport limits response size, sanitizes provider errors, and rejects
redirects for authenticated requests. Private provider URLs and credentials are
not response fields. `/health` and `/openapi.json` are public; the age and Insights
routes can require the independent local `WALLET_CHECK_API_KEY`.

## SQLite LRU storage

| Database | Value | Freshness | Default capacity |
| --- | --- | --- | --- |
| `data/wallet-age-days.sqlite3` | First-observed epoch per namespace/address | No TTL while retained | 1,000,000 rows |
| `data/wallet-insights.sqlite3` | Snapshot or AnChain label component | Snapshot 15 s; labels 300 s | 1,000,000 rows across both components |

Successful reads and writes advance a persisted sequence. Full-cache insertion
atomically removes the least recently used entry; eviction is based on access,
not blockchain age or insertion time. Expired Insights entries are reclaimed
before fresh entries. Reads update recency without changing capture or expiry
times. An Insights address can occupy two rows.

Both caches use WAL, indexed eviction and bounded per-connection memory. A 50 ms
SQLite lock wait lets services bypass a busy cache rather than block indefinitely.
Migration preserves values in older schemas and adds deterministic initial
recency ordering. A tighter capacity on an embedded cache constructor persists
for all connections; reopening cannot silently enlarge that database's limit.
`peek()` and `size()` do not promote entries; monitoring uses `peek()`.

Use SQLite's backup API, or stop the service before copying a database. A live
WAL database can also have `-wal` and `-shm` files. Runtime databases are local
state and are excluded from version control.

## Development and validation

```bash
python3 -m unittest discover -s tests -q
```

The unit suite uses only the standard library and temporary databases. HTTP tests
bind local ephemeral ports. They cover input rejection, deadline behavior,
partial results, provider parsing, credential handling, precision, fixed TTLs,
LRU eviction, migration, concurrency and the separation of the two endpoints.
Live benchmarks are separate commands and can consume upstream API quota.
See [PROVIDERS.md](PROVIDERS.md) and [BENCHMARKS.md](BENCHMARKS.md).
