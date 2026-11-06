# Benchmark results and reproduction

The fixed [100-input corpus](../benchmarks/addresses.json) contains 40 known-used
addresses, 40 synthetic format-valid candidates, and 20 malformed strings. It is
not a representative random sample or a labeled fraud-detection dataset.
Eighteen existing-account controls were transaction senders from one block;
popular contracts were chosen deliberately. Historical account-type labels were
not independently revalidated.

## V2 aiohttp service — November 6

The [V2 report](../benchmarks/run-v2-final-2026-11-06/REPORT.md) uses the same
100 inputs, a fresh V2 SQLite LRU and the pinned runtime. All 40 known-used
controls returned ages; all 20 malformed inputs returned `400`; all 40 synthetic
candidates returned `503 unknown`. No absence or age-zero claim is made for them.

| V2 cohort | Median | p95 | Maximum |
| --- | ---: | ---: | ---: |
| Known-used uncached, 40 | 0.723 s | 0.996 s | 1.004 s |
| All uncached, 100 | 0.693 s | 0.979 s | 1.017 s |
| Positive cache repeats, 40 | 0.790 ms | 1.756 ms | 5.781 ms |

Exact deployment-epoch agreement was 17/20 both initially and after refinement;
one other epoch improved in the background. Formula checks passed 80/80 successful
cold/warm results. These are observed-history controls, not universal age or fraud
accuracy. Provider caches cannot be reset, and this run followed a qualification
run, so it is not a controlled speed comparison with earlier implementations.

The [V2 million-entry SQLite check](../benchmarks/v2-cache-million.md) verifies
the full capacity, LRU replacement, epoch minimums and recency after reopening.
Its synthetic local hit median/p95 were 0.124/0.172 ms; the database was 151 MiB.
It does not measure HTTP or provider latency.

## V1 stopping-policy experiment — November 5

The [new one-second soft-deadline comparison and vendor study](../benchmarks/FIRST_RESPONSE_BENCHMARK_2026-11-05.md)
uses the persistent LRU and current configured providers. Production remains unchanged.
Both primary arms ran all 100 cold inputs with separate empty databases.

| Known-used controls, 40 addresses | Current policy | Experimental first-response policy |
| --- | ---: | ---: |
| Ages returned | 40/40 | 40/40 |
| Median uncached HTTP | 1.478 s | 1.182 s |
| p95 uncached HTTP | 2.475 s | 1.473 s |
| Maximum uncached HTTP | 2.476 s | 1.763 s |

The candidate's 40 positive cached repeats had median 3.322 ms, p95 5.044 ms and
zero upstream calls. Four initial epochs were later than baseline, including one
55-day result that refined to 71 days. Background MIN updates corrected all four.
All positive candidate replies used the one-provider soft-deadline path; the
two-provider branch did not trigger in this live sample. Both policies matched
17/20 deployment controls, so exact universal age accuracy is not established.
The separate vendor study made 200 requests across ten vendor/workload cohorts.

## Historical runs and storage measurements

| Measurement | Median | p95 | Maximum | Coverage / agreement |
| --- | --- | --- | --- | --- |
| walletAgeDays uncached, 40 known-used controls, pre-LRU | 1.374 s | 1.800 s | 1.946 s | 40/40 returned ages; 20/20 deployment controls matched |
| WalletInsights uncached, 80 valid inputs, pre-LRU | 0.685 s | 0.807 s | 0.884 s | 80/80 complete state snapshots and available AnChain labels |
| WalletInsights immediate warm hits, pre-LRU | 3.315 ms | 4.4 ms | 9.613 ms | 80/80 both components cached |
| Current LRU age store, actual 1M records | 1.071 ms | 1.822 ms | 15.132 ms | Read timings include persisted LRU touches |

The historical HTTP runs in this table **predate the LRU recency index and writes**. They
are historical network/API measurements, not a fresh end-to-end benchmark of the
current LRU implementation. Current LRU tests and the million-row experiment
verify the new storage behavior. No old report or raw response was rewritten to
pretend it used the new cache policy.

The initial LRU revision passed **308 standard-library tests**, covering migration,
recency, concurrent capacity, TTL preservation and both endpoint contracts.

The application uses a 2.6-second budget, reserving time for persistence and JSON
serialization. Observed requests met the three-second target in these runs;
that is not a guarantee of complete upstream data, remote internet latency,
concurrent-load capacity or arbitrary OS/DNS stalls.

## walletAgeDays uncached run

[Full report and all 100 cases](../benchmarks/run-uncached-2026-11-05/REPORT.md).

- An isolated cache started empty; all 80 valid inputs were absent before lookup,
  and no response hit the application cache.
- All 100 requests finished within three seconds. Overall median was 0.472 s,
  which includes fast local invalid-input rejections and unknown responses.
  Successful-age latency is therefore reported separately above.
- Results: 40 integer ages, 40 synthetic-candidate `503 "unknown"` responses,
  and 20 correctly rejected malformed inputs.
- Epoch-to-days arithmetic matched 40/40 successful responses.
- All 20 independent contract-deployment timestamps matched. Deployment agreement
  does not prove that no prefunding or earlier address mention existed. Exact
  first-use ground truth is not supplied for the other existing accounts.
- Every verified indexed observation in this run came from Routescan. Blockscout
  returned 160 HTTP 429 errors and BlockCypher returned 65. Successful independent
  indexed fallback diversity was not demonstrated by that run.

Synthetic candidates have no independently proven complete absence history.
`404` means no observation was found in queried coverage; `503` means unavailable
or incomplete evidence. Neither is a zero-day age or proof of a fresh private key.

## WalletInsights cold and warm run

[Full report and all 100 cold cases](../benchmarks/run-wallet-insights-2026-11-05/REPORT.md).

The service used configured Alchemy, QuickNode and AnChain providers. Each cold
request was followed immediately by its warm counterpart to avoid expiration of
the 15-second snapshot cache during a whole-corpus pass. All 200 HTTP requests
finished within three seconds; all 20 malformed inputs were rejected without
provider calls.

All 80 valid cold responses contained native ETH, USDC, USDT and DAI balances,
raw nonce, code profile and available AnChain labels. Independent exact-block
verification matched **480/480 fields** across 80 addresses, with no mismatch or
unverified field. The selected timed snapshot provider was Alchemy; independent
references used Tenderly for 53 addresses and QuickNode for 27 after public
reference throttles. Initial reference evidence was preserved.

Label availability is not label accuracy or a safe verdict. No sanctions, scam,
exploit or attacker ground-truth set was used. An `unaffiliated` or empty label
result must not be interpreted as safe.

The run recorded 160 Alchemy, 80 AnChain and 138 QuickNode HTTP-client attempts.
QuickNode work included healthy hedges rather than only failures. Batch calls can
contain several billable methods, and upstream pricing is not estimated here.
Supplemental RPC-only controls exercised Alchemy failure, both paid providers
failing, and a delayed primary. They used stubbed labels and are not additional
full HTTP/live-label benchmark results.

## Current one-million-entry LRU store

[Measured artifact](../benchmarks/wallet-age-days-lru-million.json) and
[reproduction script](../benchmarks/benchmark_wallet_age_store.py).

A real 1,000,000-row age store used **136.28 MiB on disk** and **20.59 MiB peak
benchmark-process RSS**. Reads including durable recency updates had a 1.071 ms
median and 1.822 ms p95. Full-capacity insertion took 1.176 ms. The new entry was
stored, the least recently used entry was evicted, a recently read entry was
retained, and the row count remained one million.

The OS filesystem cache was warm. RSS excludes OS page cache and is not full
server RAM. Bulk-load throughput is not per-request insertion throughput. Older
66.30 MiB compact-store and 279 MiB evidence-store measurements used different
schemas/policies and must not be treated as current LRU capacity figures.

## Reproduce

Run from the repository root. Output directories/files must be fresh. Live
benchmarks make real provider calls and may consume paid quota or encounter
rate limits. They do not submit blockchain transactions.

```bash
# Current code, 100 uncached inputs; export age RPC credentials separately if wanted.
python3 benchmarks/run_uncached_wallet_age_benchmark.py \
  --output benchmarks/run-uncached-new

# Current code, 100 cold + 100 immediate-warm Insights requests and references.
python3 benchmarks/run_wallet_insights_benchmark.py \
  --output benchmarks/run-wallet-insights-new --env-file .env

# Offline LRU storage test; Unix resource module required by this script.
python3 benchmarks/benchmark_wallet_age_store.py \
  --db /tmp/wallet-age-lru-new.sqlite3 \
  --output /tmp/wallet-age-lru-new.json

# Standard-library unit and local HTTP tests; no paid credentials needed.
python3 -m unittest discover -s tests -q
```

Raw API rows, provider events, metadata, summaries and reference checks accompany
the reports. Runtime hashes and the dataset hash support auditability. Upstream
provider caches cannot be cleared by the harness. Timings include the local HTTP
response body read but exclude deliberate pauses and independent reference calls.

Benchmark timestamps are technical UTC values in raw artifacts. The Insights
human-readable report also gives America/Chicago time. The runs above were
collected on November 5, 2026. See [PROVIDERS.md](PROVIDERS.md) for key handling
and [ARCHITECTURE.md](ARCHITECTURE.md) for the current code paths and cache design.
