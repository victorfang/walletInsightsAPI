# V2 architecture

Implemented November 6, 2026. V2 is an age-only aiohttp implementation with a small version-routing compatibility edge. The existing V1 implementation and its original entry points remain available. This work does not deploy or replace the hosted demo Site. The [README](README.md) covers startup, API examples and product intent.

## Four runtime files

| File | Responsibility | Lines in the initial implementation |
| --- | --- | ---: |
| [app.py](app.py) | Settings, HTTP/authentication, version dispatch, lazy V1 compatibility and lifecycle | 254 |
| [service.py](service.py) | Validation, cache-first age decisions, deadlines and bounded refinement | 155 |
| [feeds.py](feeds.py) | Provider formats, indexed discovery and canonical finalized-event verification | 250 |
| [cache.py](cache.py) | SQLite schema, minimum epochs and atomic persisted LRU | 115 |

The implementation totals **774 physical lines**, including comments and blank lines. The earlier 400–600-line figure was a planning estimate; legacy/version compatibility and cleanup account for additional code. Tests and benchmark tools live outside V2. Runtime files do not include a plugin framework, generic cache-payload layer or extra wrappers.

```text
app → service → feeds
              → cache
  └→ existing V1 factories only for compatibility routes
```

The age service, feeds and cache import no V1 code. Only `app.py` lazily imports the original factories and runs synchronous legacy checks off the event loop. This narrow exception to the original isolation proposal implements the subsequently requested backward compatibility without copying the old service.

The app uses Python 3.11+, pinned [aiohttp==3.14.5](requirements.txt), and standard-library SQLite/TOML. One shared `ClientSession` pools outbound connections. SQLite transactions run through `asyncio.to_thread`, with serialized writes and no extra database dependency. Background work is tracked and canceled at shutdown before the cache and client close.

## Versioning and compatibility

| Path | Selected implementation |
| --- | --- |
| `/walletAgeDays`, `/latest/walletAgeDays` | `[api].latest`; `v2` by default |
| `/v2/walletAgeDays` | V2 permanently pinned |
| `/v1/walletAgeDays` | Original V1 checker |
| `/WalletInsights`, `/latest/WalletInsights`, `/v1/WalletInsights` | Original V1 Insights contract |
| `/v2/WalletInsights` | Explicit `501` coming-soon error |

`[api].latest = "latest"` selects the newest registered compatible release; V2 is currently newest. An explicit version can override that setting for rollback. `X-API-Version` identifies the selected implementation. Inputs remain exactly `blockchain` and `address`; age success is a bare integer and errors remain the existing JSON strings/statuses. No envelope or renamed field is introduced. Existing age/Insights headers are retained. Health and OpenAPI are public, also under version prefixes. The root V1 OpenAPI file stays intact; pinned specifications set their server base to `/v1` or `/v2`, so generated clients keep the selected version.

Promoting a later implementation to latest requires passing the published contract checks. Explicit version routes remain available; a breaking contract needs its own version and migration policy. Legacy Insights remains the latest available Insights implementation until a replacement is deliberately introduced. Its original labels and response shape are preserved for compatibility; no label calls occur on the V2 age path.

## Configuration

[config.toml](config.toml) owns public URLs, enabled chains, aliases/scopes, provider selection, time budgets, concurrency limits and database capacity. [.env.example](.env.example) lists optional secret names; the existing root `.env` can be reused with `--env-file .env`. Process environment overrides file values. Secret-file lines are read as data, never evaluated as shell code.

Provider URLs use HTTPS; authenticated redirects are disabled. Alchemy keys are substituted into a configured endpoint, Etherscan keys into its configured parameter, and QuickNode's optional token into `x-token`. Private endpoint URLs can be supplied entirely through environment variables. Incoming `WALLET_CHECK_API_KEY` authentication is separate from provider authentication. Configuration is loaded once; database paths resolve relative to the configuration file.

The supplied history sources are Alchemy, Etherscan, Routescan and Blockscout. RPC verification uses QuickNode, Alchemy, BlockPI and PublicNode. Missing optional credentials disable the affected feed. Ordinary RPC nodes verify events and blocks but do not provide general address-history discovery. Provider-specific formats remain small explicit adapters in `feeds.py`, not executable parsing rules in TOML.

## Age request flow

1. Validate chain selector and address. Ethereum requires a full-string `0x[0-9a-fA-F]{40}` match before lowercasing. Invalid inputs make no provider calls.
2. Look up `(chain, address, coverage_version)` in the age database. A cache hit returns completed days, `(now - epoch) // 86400`, and advances LRU recency with no upstream work.
3. For missing chain history, start eligible provider groups concurrently. A cache miss means uncached, not necessarily a new wallet.
4. Verify discovered events against canonical finalized block/receipt data. Persist the smallest valid epoch; errors and empty responses are not age zero.
5. Return after two usable independent history groups, or one after the one-second soft deadline. If all groups finish sooner, return without an artificial wait. Individual RPC replies and duplicate company feeds do not create additional history-provider votes.
6. Already-started lookups may refine the cached minimum within the original 2.6-second hard budget. Incomplete provider groups can still contribute verified lower-bound observations at the final decision, but do not count as complete early-return votes.

The uncached response target remains under three seconds, including an explicit `unknown` when evidence is unavailable. It is not a promise of a successful age result during upstream failures or an unlimited-load SLA. Concurrency is capped. No balance, portfolio, nonce, code-profile or label requests occur on the age path; indexed token/NFT transfers can supply first-activity evidence.

Age describes the earliest verified **observed** activity within the reported coverage. It can underestimate true age. An early response can later improve: the earlier V1-based policy experiment changed one result from 55 to 71 days after bounded refinement. See the [previous algorithm study](../benchmarks/FIRST_RESPONSE_BENCHMARK_2026-11-05.md); its measurements are not V2 results.

## One database per data route

| Data route | Database | LRU entry limit |
| --- | --- | ---: |
| V2 age | `v2/data/wallet-age.sqlite3` | **1,000,000** |
| Future V2 balance/peak signals | `v2/data/wallet-peak.sqlite3` | **100,000**, reserved; no implementation yet |

V1 compatibility factories retain their original independently maintained databases. They do not share V2 tables or quotas. The future 100k reservation does not alter V1 Insights behavior. SQLite WAL may create `-wal`/`-shm` sidecars beside each logical database.

The age schema is deliberately specific:

```text
first_seen(chain, address, coverage_version, epoch, last_access)
  primary key: (chain, address, coverage_version)
  index: last_access
state(id, access_counter, row_count)
```

- Verified positive epochs have **no TTL**. Later discoveries update with `MIN(existing_epoch, incoming_epoch)`.
- Successful reads and writes advance persisted access order. At capacity, a new key atomically replaces the least recently used row. Updating an existing key does not evict another row.
- The metadata count avoids a million-row scan per insert. Reopening with a smaller capacity trims the least recently used entries.
- The cap counts cache keys, not bytes or unique addresses across every future chain. Chain/coverage namespaces prevent Ethereum-only facts from proving broader coverage.
- Future balance records have their own schema, bounded payloads and refresh policy; current balances and historical maxima can change. LRU eviction also ends service-observed tracking unless its coverage can be rebuilt.

## Optional later balance signals

Security and anti-fraud screening remains the product purpose. A later balance route may return current native ETH plus at most five configured token contracts, for example USDC, USDT and WBTC. This is an illustrative operator-maintained allowlist, not automatic market ranking. Identify tokens by chain ID and contract address; keep decimals and allowlist version in configuration. Do not discover arbitrary tokens, scan NFTs or calculate a full portfolio. New V2 signals will leave labels to AnChain's separate API.

Historical peak research concerns native ETH only. Existing documentation does not establish a simple exact lifetime-peak call:

| Source | Documented capability | Implication |
| --- | --- | --- |
| [Alchemy `eth_getBalance`](https://www.alchemy.com/docs/chains/ethereum/ethereum-api-endpoints/eth-get-balance) | Balance at a selected block/tag | A snapshot, not a lifetime maximum |
| [Etherscan `balancehistory`](https://docs.etherscan.io/api-reference/endpoint/balancehistory) | Balance at a specified block; PRO endpoint | Repeated historical queries need a separate cost/latency study |
| [QuickNode `bb_getBalanceHistory`](https://www.quicknode.com/docs/ethereum/bb_getBalanceHistory) | Blockbook add-on with grouped transaction/flow history | No documented direct peak result; entitlement and reconstruction need validation |

An exact lifetime peak remains deferred. A later route may offer a maximum observed since tracking began or a maximum among historical samples, with coverage window, sampling interval where applicable, source and `asOf`. Neither is an exact all-time peak. Store raw balances as decimal strings and compare Python integers, since wei values may exceed SQLite's signed 64-bit range.

## Extension and evaluation

An EVM chain using the existing protocols needs chain/feed configuration plus capability tests before activation. An `evm` scope takes the minimum across its explicitly listed chains and queries missing coverage when the list expands. Current aliases report Ethereum-only coverage. BTC, Solana and TRON require family-specific validation/history adapters; changing a URL alone is insufficient.

The [V2 tests](../tests/test_v2_app.py) exercise version dispatch and wire compatibility; accompanying cache, service and feed tests cover LRU, minimum epochs, deadlines and verification using local fixtures. The [V2 benchmark runner](../benchmarks/run_v2_benchmark.py) reuses the same 100-address corpus with an isolated SQLite database. It reports uncached/cache HTTP latency, initial/refined epochs and agreement with independent deployment controls, without claiming universal age or fraud accuracy. New measured numbers belong in its generated report after the run is completed; existing V1 studies remain identified separately.
