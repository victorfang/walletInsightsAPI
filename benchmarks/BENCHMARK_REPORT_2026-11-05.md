# Ethereum address-check benchmark — 5 November 2026

Run: **2026-11-05 12:31:00 CST → 2026-11-05 12:46:47 CST** (947.5 seconds elapsed). Network: Ethereum mainnet. Time zone: America/Chicago.

## Findings

The detailed cold pass correctly handled format validation for **100/100 inputs**. It identified **40/40 known-used addresses**, with 0 incorrect conclusive classifications and 0 inconclusive or unusable results. Generated-address results agreed with the synthetic expectation for **40/40 candidates**. Generated candidates are not absolute proof of never-used addresses.

A first-activity timestamp was available for **40/40 known-used addresses**. Among **20 independently verified contract-deployment controls**, 20 had a returned timestamp and 17 matched deployment exactly. 3 were later than deployment; 0 were earlier, which can reflect earlier address activity. This measures deployment-timestamp agreement and first-activity coverage, not private-key creation age.

For format-valid inputs, detailed cold median latency was **8.10 s**, and detailed warm median latency was **0.87 ms**. The basic cold median was **1.47 s**. Detailed warm mean latency improved by **9,215.9×** over detailed cold mean latency. The run observed **76 upstream HTTP 429 responses**.

## Corpus and expected outcomes

| Cohort | Inputs | Expected outcome | Strength of reference |
| --- | ---: | --- | --- |
| Existing EOAs | 20 | Seen before; not new | Public controls and canonical transaction-sender inclusion prove prior use, but not exact first-use time |
| Existing contracts | 20 | Seen before; not new | Canonical deployment controls; receipt/block/archive-code evidence collected separately |
| Generated valid strings | 40 | No observed activity expected | Last 20 bytes of SHA-256 seeds; no private keys generated or retained; absence is a probabilistic expectation |
| Malformed strings | 20 | HTTP 400; no provider lookup | Explicit syntax-invalid controls: wrong length/prefix, nonhex, whitespace, Unicode, empty strings, and related cases |

All 100 raw inputs are distinct, and the 80 format-valid inputs also have distinct normalized cache keys. The EOA selection is a convenience sample, including recent transaction senders; popular contracts are deliberate controls. This is not a representative random sample of all Ethereum wallets. The 100 inputs and their evidence are included in the appendix and dataset artifact.

Format accuracy is scored against the documented full-string policy: lowercase `0x` followed by exactly 40 ASCII hexadecimal characters. This is syntax validation; it does not validate the optional [EIP-55 mixed-case checksum](https://eips.ethereum.org/EIPS/eip-55) or establish ownership.

## Method

Requests used the production HTTP handler at `GET /api/analytics/address/check?proto=eth&address=...`, with an isolated SQLite cache and a loopback HTTP client. The detailed profile sent `details=true`; the basic profile sent `details=false`. Each profile performed a 100-input cold pass, a 100-input warm repeat, and additional repeat passes. Basic and detailed requests have separate cache keys, so the basic cold pass did not reuse detailed entries.

The run used concurrency **1**, deterministic shuffle seed **20261105**, a per-upstream-request timeout of **5 s**, and a **0.3 s** pause after uncached upstream activity. Latency uses a monotonic clock and includes HTTP transport, handler work, JSON serialization, and upstream requests. Configured pauses are excluded. Cold means an empty application cache, not cleared upstream/provider caches. Phases ran sequentially rather than in randomized A/B order.

The recorded source hashes were unchanged throughout the run: **True**. The harness recorded **1000 API requests**, **1282 upstream HTTP requests**, and **800 logical RPC method calls**. The benchmark submitted no blockchain transactions. This is a serial loopback functional/latency benchmark, not a production WAN, throughput, or concurrent-load benchmark.

Observed cold-pass cache hits were **0 detailed** and **0 basic**. These counters verify that the application cache was cold for the tested request keys.

Reference collection overlapped the timed run and shared public endpoint quotas. Its traffic was not included in the DUT request counts above, but it could affect observed throttling and latency. Endpoint state, provider caching, and rate limits were not experimentally isolated.

## Accuracy and coverage

The following primary scores use the detailed cold pass, deduplicated by input ID. Unknown results are kept separate and are not counted correct in the all-eligible agreement rate.

| Measure | Eligible | Correct / agreeing | Incorrect conclusive | Unknown / unusable | Rate over all eligible |
| --- | ---: | ---: | ---: | ---: | ---: |
| Format validation | 100 | 100 | 0 | — | 100.00% |
| Known-used address recall | 40 | 40 | 0 | 0 | 100.00% |
| Synthetic-candidate agreement | 40 | 40 | 0 | 0 | 100.00% |
| Combined valid-input agreement | 80 | 80 | 0 | 0 | 100.00% |

Conditional agreement among conclusive valid results was **100.00%** on **80** results. This conditional percentage is shown alongside the all-eligible denominator, so missing results cannot inflate the headline. The combined valid-input metric includes synthetic expectations and should not be described as universally proven blockchain-history accuracy.

| Primary phase | Format correct | Known-used correct / eligible | Synthetic agreeing / eligible | Valid unknown / unusable |
| --- | ---: | ---: | ---: | ---: |
| `detailed_cold` | 100/100 | 40/40 | 40/40 | 0 |
| `detailed_warm` | 100/100 | 40/40 | 40/40 | 0 |
| `basic_cold` | 100/100 | 40/40 | 40/40 | 0 |
| `basic_warm` | 100/100 | 40/40 | 40/40 | 0 |

### Optional fields on known-used inputs

| Cohort | N | Exact wei balance available | Historical count available | First timestamp available |
| --- | ---: | ---: | ---: | ---: |
| EOAs | 20 | 20 | 19 | 20 |
| Contracts | 20 | 20 | 19 | 20 |
| All known-used | 40 | 40 | 38 | 40 |

These are availability scores. Live balances/counts can move between observations and their coverage depends on the reporting provider. Native `balance` is a JSON number; exact precision is preserved by `balance_wei`. Nonce is not treated as a historical transaction count. Missing fields remain null. Historical counts were not independently graded for exactness.

### Independent exact-balance verification

An additional audit queried `tenderly_public` at each detailed-cold result's **exact cached state block**, rather than comparing balances at different moving heads. It independently verified **80/80 format-valid inputs**: **80 exact wei matches**, **0 mismatches**, and **0 unverified**. Comparison used integer wei strings, not the rounded native-number display field. Malformed inputs were excluded from the on-chain balance audit.

| Cohort | Audited | Exact matches | Mismatches | Unverified |
| --- | ---: | ---: | ---: | ---: |
| existing_eoa | 20 | 20 | 0 | 0 |
| existing_contract | 20 | 20 | 0 | 0 |
| generated_valid | 40 | 40 | 0 | 0 |

This audit used **17 additional HTTP requests** containing **81 RPC methods** outside the measured DUT calls. It verifies the saved balance snapshots, not future balance freshness or historical transaction-count accuracy.

## Age assessment

The independent control set contains **20 verified deployments**: **17** direct-creation receipts and **20** verified empty-code-before/nonempty-code-at-deployment transitions. For factory-created contracts, the code transition supplies the creation evidence. Candidate creation transactions were discovered separately from DUT `first_txn`; canonical receipt, block hash/timestamp, and archive-state verification provide the on-chain check. A discovery index can also be a DUT fallback, so the claim is independently verified deployment time, not a wholly disjoint set of discovery services.

Exact deployment agreement was **17/20 (85.00%)** over all eligible controls, or **17/20 (85.00%)** among controls with returned timestamps. There were **0** missing timestamps, **3** later timestamps, and **0** earlier observations.

A timestamp later than verified deployment makes the address appear too young if `first_txn` is interpreted as creation or first-ever appearance. The first-transaction search can omit deployment/creation events under its documented category coverage. This is separate from correctly detecting that the address already exists.

**USDC (eth-022):** verified deployment was 2018-08-03 19:28:24 UTC; returned `first_txn` was 2018-12-31 23:40:01 UTC. The offset was **12,975,097 seconds (150.1747 days)**. Successful first-transaction categories were `erc20_transfers, erc721_transfers, erc1155_transfers`. This is the earliest result across the categories that returned usable data, not a guaranteed complete-history minimum.

Recorded upstream failures for this check:

- `blockscout` / `txlist`: `HTTP 429`
- `routescan` / `txlist`: `Network failure (TimeoutError)`
- `routescan` / `txlistinternal`: `Network failure (TimeoutError)`

The response also warned: `normal_transactions history unavailable: HTTP 429`; `Blockscout rate limit reached; remaining history queries were skipped.`; `Routescan was queried to supplement unavailable or incomplete Blockscout first-occurrence history.`; `normal_transactions history unavailable: Network failure (TimeoutError)`; `internal_transactions history unavailable: Network failure (TimeoutError)`.

**USDT (eth-023):** verified deployment was 2017-11-28 00:41:21 UTC; returned `first_txn` was 2018-09-18 23:12:34 UTC. The offset was **25,482,673 seconds (294.9383 days)**. Successful first-transaction categories were `erc20_transfers, erc721_transfers, erc1155_transfers`. This is the earliest result across the categories that returned usable data, not a guaranteed complete-history minimum.

Recorded upstream failures for this check:

- `blockscout` / `txlist`: `HTTP 429`
- `routescan` / `txlist`: `Network failure (TimeoutError)`
- `routescan` / `txlistinternal`: `Network failure (TimeoutError)`

The response also warned: `normal_transactions history unavailable: HTTP 429`; `Blockscout rate limit reached; remaining history queries were skipped.`; `Routescan was queried to supplement unavailable or incomplete Blockscout first-occurrence history.`; `normal_transactions history unavailable: Network failure (TimeoutError)`; `internal_transactions history unavailable: Network failure (TimeoutError)`.

**MKR (eth-029):** verified deployment was 2017-11-25 18:24:17 UTC; returned `first_txn` was 2017-11-25 19:11:58 UTC. The offset was **2,861 seconds (0.0331 days)**. Successful first-transaction categories were `normal_transactions, erc20_transfers, erc721_transfers, erc1155_transfers`. This is the earliest result across the categories that returned usable data, not a guaranteed complete-history minimum.

Recorded upstream failures for this check:

- `blockscout` / `txlist`: `HTTP 429`
- `routescan` / `txlistinternal`: `Network failure (TimeoutError)`

The response also warned: `normal_transactions history unavailable: HTTP 429`; `Blockscout rate limit reached; remaining history queries were skipped.`; `Routescan was queried to supplement unavailable or incomplete Blockscout first-occurrence history.`; `internal_transactions history unavailable: Network failure (TimeoutError)`.

These cases show that classification can remain correct while the optional age estimate becomes too young after partial history failures. The conclusive classification and its partial optional age are stored together for 24 hours, so a warm hit can preserve that partial age.

A timestamp earlier than deployment is not automatically wrong: prefunding or other earlier activity can involve the eventual contract address. Deployment agreement also does not prove the absence of earlier prefunding. EOA private keys can be generated offline long before first activity, so exact key-generation age cannot be established from this API or this benchmark. See [Ethereum account creation](https://ethereum.org/developers/docs/accounts/) for the distinction between key generation and on-chain accounts.

For **18 EOAs** with canonical transaction inclusion evidence, the proof is an upper bound on first use. **18** had returned first timestamps; **0** were later than an independently known prior transaction. The benchmark does not grade the remaining EOA timestamps as exact first-use dates.

Genesis-state checks were available for **20/20 EOAs**; **0** had a positive genesis balance in those checks. The run therefore supplied **0 positive genesis controls**. No synthetic age is assigned from a zero-valued genesis header timestamp.

### Contract timestamp controls

Error is `reported first_txn − deployment timestamp`; positive error means an age estimate that is too young relative to deployment. Negative error denotes an earlier observation and remains ambiguous.

| ID | Contract | Verified deployment UTC | Reported first activity UTC | Error seconds | Assessment |
| --- | --- | --- | --- | ---: | --- |
| eth-021 | WETH9 | 2017-12-12 11:17:35 UTC | 2017-12-12 11:17:35 UTC | 0 | Exact deployment match |
| eth-022 | USDC | 2018-08-03 19:28:24 UTC | 2018-12-31 23:40:01 UTC | 12,975,097 | Later; age too young |
| eth-023 | USDT | 2017-11-28 00:41:21 UTC | 2018-09-18 23:12:34 UTC | 25,482,673 | Later; age too young |
| eth-024 | DAI | 2019-11-13 19:17:57 UTC | 2019-11-13 19:17:57 UTC | 0 | Exact deployment match |
| eth-025 | UNI | 2020-09-14 18:11:26 UTC | 2020-09-14 18:11:26 UTC | 0 | Exact deployment match |
| eth-026 | LINK | 2017-09-16 21:26:29 UTC | 2017-09-16 21:26:29 UTC | 0 | Exact deployment match |
| eth-027 | WBTC | 2018-11-24 21:45:52 UTC | 2018-11-24 21:45:52 UTC | 0 | Exact deployment match |
| eth-028 | AAVE | 2020-09-24 18:06:28 UTC | 2020-09-24 18:06:28 UTC | 0 | Exact deployment match |
| eth-029 | MKR | 2017-11-25 18:24:17 UTC | 2017-11-25 19:11:58 UTC | 2,861 | Later; age too young |
| eth-030 | COMP | 2020-03-04 00:08:55 UTC | 2020-03-04 00:08:55 UTC | 0 | Exact deployment match |
| eth-031 | Uniswap V2 Factory | 2020-05-04 16:34:02 UTC | 2020-05-04 16:34:02 UTC | 0 | Exact deployment match |
| eth-032 | Uniswap V2 Router02 | 2020-06-05 20:17:21 UTC | 2020-06-05 20:17:21 UTC | 0 | Exact deployment match |
| eth-033 | Uniswap V3 Factory | 2021-05-04 19:27:00 UTC | 2021-05-04 19:27:00 UTC | 0 | Exact deployment match |
| eth-034 | Uniswap V3 SwapRouter | 2021-05-04 19:30:41 UTC | 2021-05-04 19:30:41 UTC | 0 | Exact deployment match |
| eth-035 | Uniswap SwapRouter02 | 2021-12-14 17:37:26 UTC | 2021-12-14 17:37:26 UTC | 0 | Exact deployment match |
| eth-036 | Uniswap V3 NonfungiblePositionManager | 2021-05-04 19:35:13 UTC | 2021-05-04 19:35:13 UTC | 0 | Exact deployment match |
| eth-037 | Permit2 | 2022-11-17 01:17:59 UTC | 2022-11-17 01:17:59 UTC | 0 | Exact deployment match |
| eth-038 | ENS Registry | 2020-01-30 00:37:12 UTC | 2020-01-30 00:37:12 UTC | 0 | Exact deployment match |
| eth-039 | ENS BaseRegistrarImplementation | 2020-01-30 00:43:54 UTC | 2020-01-30 00:43:54 UTC | 0 | Exact deployment match |
| eth-040 | Beacon deposit contract | 2020-10-14 09:22:52 UTC | 2020-10-14 09:22:52 UTC | 0 | Exact deployment match |

## Latency

All values in the following tables are **milliseconds**, measured at the loopback HTTP client. Percentiles use Hyndman–Fan type 7 linear interpolation at `(n − 1) × p`. Valid and malformed inputs are separated to avoid letting fast syntax rejections dilute provider-backed latency.

| Profile | Phase | Cohort | N | Mean ms | p50 ms | p95 ms | p99 ms | Max ms |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Detailed | Cold | Valid addresses | 80 | 9,761.59 | 8,100.24 | 18,455.72 | 22,983.16 | 25,381.05 |
| Detailed | Cold | Malformed strings | 20 | 5.91 | 4.90 | 9.65 | 27.24 | 31.64 |
| Detailed | Warm | Valid addresses | 80 | 1.06 | 0.87 | 1.72 | 2.86 | 5.58 |
| Detailed | Warm | Malformed strings | 20 | 0.88 | 0.56 | 1.60 | 4.57 | 5.31 |
| Basic | Cold | Valid addresses | 80 | 1,441.96 | 1,469.51 | 2,181.59 | 2,655.88 | 2,682.93 |
| Basic | Cold | Malformed strings | 20 | 4.44 | 4.30 | 6.97 | 11.01 | 12.02 |
| Basic | Warm | Valid addresses | 80 | 0.99 | 0.81 | 1.65 | 2.24 | 3.27 |
| Basic | Warm | Malformed strings | 20 | 0.99 | 0.59 | 1.67 | 5.67 | 6.67 |

### Additional repeat passes

The harness also repeated inputs eligible after the initial cold pass. Malformed strings are fast local rejections and are not cache hits; their inclusion in a repeat phase should not be mistaken for cached upstream work.

| Profile | Additional passes | Total requests | Valid cache hits | Malformed local rejections | Hit mean ms | Hit p95 ms | Upstream HTTP requests |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Detailed | 3 | 300 | 240 | 60 | 0.84 | 1.09 | 0 |
| Basic | 3 | 300 | 240 | 60 | 0.76 | 0.85 | 0 |

## Cache behavior and 24-hour lifetime

| Profile | Conclusive valid cold results | Warm valid hits | Hits with zero upstream requests | Stats unchanged on repeat | Mean speedup |
| --- | ---: | ---: | ---: | ---: | ---: |
| Detailed | 80 | 80/80 | 80 | 80/80 | 9,215.9× |
| Basic | 80 | 80/80 | 80 | 80/80 | 1,452.2× |

Across all phases, **640 responses were marked cache hits**, and **0 cache hits made an upstream request**. Conclusive valid results are stored in SQLite for 86,400 seconds. Reads preserve the original observation and expiry times. Malformed inputs are rejected locally on each call; unknown results are not cached. A cached conclusive result can still contain null optional fields after a provider failure.

The 24-hour expiry boundary is covered by the project's injected-clock regression tests. This benchmark did not wait 24 wall-clock hours. Reported warm timings measure cache reuse within the same run; the SQLite persistence and exact expiry tests are separate evidence.

## Sources, resilience, and request cost

| Source | Upstream HTTP requests | Logical RPC methods | Transport errors | HTTP 429 |
| --- | ---: | ---: | ---: | ---: |
| blockscout | 402 | 0 | 79 | 76 |
| publicnode | 480 | 800 | 0 | 0 |
| routescan | 400 | 0 | 12 | 0 |
| **Total** | **1282** | **800** | **91** | **76** |

There were **15 identifiable timeout errors** in recorded transport error strings. Some network failures are wrapped as `URLError` without their underlying cause, so that count is not proof that no other timeout occurred. JSON-level empty-history status values are not counted as transport errors. Source fallback tests and actual source usage should be distinguished: using a fallback in this run does not establish behavior under every possible outage.

Recorded transport error messages:

- `HTTP 429`: 76
- `Network failure (TimeoutError)`: 15

**Alchemy and QuickNode were not configured in the benchmark environment.** These timings exercise the free/public fallback path, not the user's paid endpoints. No paid-provider latency, billing-credit, or monetary-cost claim is made. Batch HTTP requests can contain multiple billable RPC methods.

The detailed cold pass made **962 upstream HTTP requests**, versus **320** in the basic cold pass (**642 fewer** in the basic pass). **40 known-used basic checks** completed without public-history calls. The observed valid-input mean latency ratio was **6.77×** detailed/basic. Rate limits and upstream caches can differ between sequential phases, so this is not a controlled causal cost estimate.

## Recommendations

1. Use the basic check for routine new-address screening and request details only when a workflow needs age/count information.
2. Treat `new_address` as a scoped no-observed-activity result. Preserve unknown/null outcomes and consult warnings rather than converting provider failures into new wallets.
3. Expose an age-completeness indicator. When required history categories fail, return null or explicitly flag `first_txn` as partial. Include a verified contract-creation receipt/block timestamp as an earliest-activity candidate when contract age is needed.
4. Keep the 24-hour classification cache, but allow partial optional age/count enrichment to refresh sooner instead of pinning a too-young or missing age for the entire classification TTL. Keep EOA key-generation age outside the API's claims.
5. Rerun this corpus with the existing paid Alchemy/QuickNode URLs configured before making production-latency or paid-cost claims. Add concurrent and WAN tests separately if those are deployment requirements.
6. Extend the corpus with old drained EOAs, token-only recipients, failed-transaction-only histories, genesis allocations, pre-funded contract addresses, delegated EOAs, and recently created addresses with independently recorded funding times.

## Evidence and reproduction

- [100-input dataset](addresses.json)
- [Raw timed HTTP results (JSONL)](run-2026-11-05/results.jsonl)
- [Machine-readable summary](run-2026-11-05/summary.json)
- [Run metadata and source hashes](run-2026-11-05/metadata.json)
- [Independent age reference evidence](references.json)
- [Benchmark runner](run_benchmark.py)
- [Summary calculator](analyze_benchmark.py)
- [Report generator](write_report.py)
- [API OpenAPI contract](../openapi.json)
- [Independent exact-balance audit](run-2026-11-05/balance-verification.json)
- [Balance verification script](verify_balances.py)
- [Application and benchmark-analysis test results](run-2026-11-05/final-regression-tests.txt)

Dataset SHA-256: `72d1a2fa26ad76e467e771e60c448aae16b49d319d27243278bdf751fd46dee0`. Per-file production hashes and the environment/provider order are recorded in the metadata artifact. The report was generated from saved results and did not contact providers.

Use a fresh output directory for each timing run. To reduce quota contamination in a follow-up study, collect reference controls separately from the timed run.

```bash
cd walletInsightsAPI
python3 benchmarks/reference_age.py --output benchmarks/references-new.json
python3 benchmarks/run_benchmark.py --output benchmarks/run-new --timeout 5 --pause 0.3 --warm-repeats 3
python3 benchmarks/verify_balances.py --run benchmarks/run-new
python3 benchmarks/analyze_benchmark.py --run benchmarks/run-new --references benchmarks/references-new.json
python3 benchmarks/write_report.py --run benchmarks/run-new --references benchmarks/references-new.json --output benchmarks/BENCHMARK_REPORT_NEW.md
```

## Appendix: all 100 inputs

Addresses are JSON-escaped inside table cells so malformed whitespace and Unicode remain visible. The first-activity column uses the detailed cold result. Cold/warm durations below are milliseconds for the detailed profile. An em dash means missing or not applicable, not zero age.

| ID | Cohort | Input address (JSON) | Expected | Observed | First activity UTC | Cold ms | Warm ms |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| eth-001 | existing_eoa | `"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"` | seen | seen | 2015-09-28 08:24:43 UTC | 8,110.07 | 1.75 |
| eth-002 | existing_eoa | `"0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"` | seen | seen | 2015-08-08 15:44:00 UTC | 19,748.18 | 1.03 |
| eth-003 | existing_eoa | `"0xc917c3fa468f2c4b9c84c72caa46420eb9825249"` | seen | seen | 2026-04-21 19:01:23 UTC | 12,656.36 | 0.80 |
| eth-004 | existing_eoa | `"0x37c728c153eeb40ee907cb5b41b342d896878846"` | seen | seen | 2026-09-30 15:05:23 UTC | 9,118.75 | 0.77 |
| eth-005 | existing_eoa | `"0x35f7cff299f48b1b2a542224fd823152e437c654"` | seen | seen | 2026-10-29 15:40:47 UTC | 8,374.51 | 0.78 |
| eth-006 | existing_eoa | `"0xccba2faab763603b2894a5f203bd335dc26d16b8"` | seen | seen | 2026-10-28 15:46:23 UTC | 8,402.29 | 0.72 |
| eth-007 | existing_eoa | `"0xbdc8f547b06f06d38a102217ca28ce7ed9340805"` | seen | seen | 2026-05-07 08:44:47 UTC | 9,860.39 | 0.89 |
| eth-008 | existing_eoa | `"0xb5e5ba15a5aa58df03d91549496d8cd67a15f858"` | seen | seen | 2026-08-26 07:47:11 UTC | 11,463.25 | 1.38 |
| eth-009 | existing_eoa | `"0xd82c5660cac4169de051d7df34323f99c21176a7"` | seen | seen | 2026-08-21 14:00:35 UTC | 8,728.89 | 0.75 |
| eth-010 | existing_eoa | `"0x4817a075bb59c1ed2d21e8240ff1561750175515"` | seen | seen | 2025-05-14 05:42:23 UTC | 14,904.64 | 0.91 |
| eth-011 | existing_eoa | `"0x403e2b67f70ee3d10ec88c9b3d6de9eb1c7200d8"` | seen | seen | 2026-10-21 04:29:47 UTC | 9,623.07 | 5.58 |
| eth-012 | existing_eoa | `"0x91e33655188280afd5042a1fba723a731a117f1b"` | seen | seen | 2026-10-18 16:44:35 UTC | 8,090.41 | 1.78 |
| eth-013 | existing_eoa | `"0xc066ac5d385419b1a8c43a0e146fa439837a8b8c"` | seen | seen | 2024-11-04 15:35:59 UTC | 16,266.25 | 0.76 |
| eth-014 | existing_eoa | `"0x6d14296c706813c642045bc6102ee6e2fcaf8528"` | seen | seen | 2026-07-03 18:09:59 UTC | 8,220.69 | 1.32 |
| eth-015 | existing_eoa | `"0x51649cf810ea9f1f61165044b7149070c67f79c8"` | seen | seen | 2026-10-11 23:36:11 UTC | 5,912.64 | 0.82 |
| eth-016 | existing_eoa | `"0x07fcdbe8283f1b67cee5d5a4151cec8e8184301f"` | seen | seen | 2026-06-04 16:49:59 UTC | 6,668.39 | 0.76 |
| eth-017 | existing_eoa | `"0xb42f812a44c22cc6b861478900401ee759ebead6"` | seen | seen | 2024-11-04 16:11:59 UTC | 15,063.10 | 0.71 |
| eth-018 | existing_eoa | `"0x7a0e37f1543d0adb4e7ba7a27ea16b926b35a67c"` | seen | seen | 2023-10-04 18:03:23 UTC | 12,865.44 | 0.85 |
| eth-019 | existing_eoa | `"0xaa8ba7d4611437141192e7ceced531bc0a133efb"` | seen | seen | 2025-09-11 04:11:23 UTC | 22,345.74 | 1.02 |
| eth-020 | existing_eoa | `"0xd37ac934502619cee14908cef22138f4b98e06b4"` | seen | seen | 2025-12-08 13:52:11 UTC | 11,668.08 | 0.77 |
| eth-021 | existing_contract | `"0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"` | seen | seen | 2017-12-12 11:17:35 UTC | 13,101.45 | 0.83 |
| eth-022 | existing_contract | `"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"` | seen | seen | 2018-12-31 23:40:01 UTC | 18,376.09 | 0.78 |
| eth-023 | existing_contract | `"0xdac17f958d2ee523a2206206994597c13d831ec7"` | seen | seen | 2018-09-18 23:12:34 UTC | 17,513.84 | 0.75 |
| eth-024 | existing_contract | `"0x6b175474e89094c44da98b954eedeac495271d0f"` | seen | seen | 2019-11-13 19:17:57 UTC | 14,535.16 | 0.75 |
| eth-025 | existing_contract | `"0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"` | seen | seen | 2020-09-14 18:11:26 UTC | 25,381.05 | 2.14 |
| eth-026 | existing_contract | `"0x514910771af9ca656af840dff83e8264ecf986ca"` | seen | seen | 2017-09-16 21:26:29 UTC | 18,387.70 | 0.78 |
| eth-027 | existing_contract | `"0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"` | seen | seen | 2018-11-24 21:45:52 UTC | 13,485.89 | 0.98 |
| eth-028 | existing_contract | `"0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"` | seen | seen | 2020-09-24 18:06:28 UTC | 16,080.95 | 0.71 |
| eth-029 | existing_contract | `"0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"` | seen | seen | 2017-11-25 19:11:58 UTC | 13,628.28 | 0.72 |
| eth-030 | existing_contract | `"0xc00e94cb662c3520282e6f5717214004a7f26888"` | seen | seen | 2020-03-04 00:08:55 UTC | 12,383.16 | 1.72 |
| eth-031 | existing_contract | `"0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"` | seen | seen | 2020-05-04 16:34:02 UTC | 9,477.36 | 0.93 |
| eth-032 | existing_contract | `"0x7a250d5630b4cf539739df2c5dacb4c659f2488d"` | seen | seen | 2020-06-05 20:17:21 UTC | 20,040.95 | 1.06 |
| eth-033 | existing_contract | `"0x1f98431c8ad98523631ae4a59f267346ea31f984"` | seen | seen | 2021-05-04 19:27:00 UTC | 9,519.19 | 0.78 |
| eth-034 | existing_contract | `"0xe592427a0aece92de3edee1f18e0157c05861564"` | seen | seen | 2021-05-04 19:30:41 UTC | 15,394.95 | 0.90 |
| eth-035 | existing_contract | `"0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"` | seen | seen | 2021-12-14 17:37:26 UTC | 13,798.08 | 0.76 |
| eth-036 | existing_contract | `"0xc36442b4a4522e871399cd717abdd847ab11fe88"` | seen | seen | 2021-05-04 19:35:13 UTC | 16,923.09 | 1.60 |
| eth-037 | existing_contract | `"0x000000000022d473030f116ddee9f6b43ac78ba3"` | seen | seen | 2022-11-17 01:17:59 UTC | 13,133.34 | 1.19 |
| eth-038 | existing_contract | `"0x00000000000c2e074ec69a0dfb2997ba6c7d2e1e"` | seen | seen | 2020-01-30 00:37:12 UTC | 10,691.19 | 1.41 |
| eth-039 | existing_contract | `"0x57f1887a8bf19b14fc0df6fd9b2acc9af147ea85"` | seen | seen | 2020-01-30 00:43:54 UTC | 12,299.59 | 0.88 |
| eth-040 | existing_contract | `"0x00000000219ab540356cbb839cbe05303d7705fa"` | seen | seen | 2020-10-14 09:22:52 UTC | 10,766.42 | 1.04 |
| eth-041 | generated_valid | `"0x941c9014c10ccf1f90228453b75f6c9f66ab3237"` | unobserved candidate | no observed activity | — | 5,183.65 | 0.77 |
| eth-042 | generated_valid | `"0xd5ff6dcf5153245be026debac13e4516a91481a7"` | unobserved candidate | no observed activity | — | 6,478.75 | 0.81 |
| eth-043 | generated_valid | `"0x6d8abcc3de765f917d8a0d61a25bfaf84015fab6"` | unobserved candidate | no observed activity | — | 7,553.83 | 0.72 |
| eth-044 | generated_valid | `"0x1abdd483a6bedb4a6e8c56d7cc21a472bd322947"` | unobserved candidate | no observed activity | — | 6,126.35 | 0.77 |
| eth-045 | generated_valid | `"0x6d2b789677686865ffe5f493e7737a3953763657"` | unobserved candidate | no observed activity | — | 5,460.02 | 0.94 |
| eth-046 | generated_valid | `"0x68a16f5a188af63ff96b847319a37a18a42471d3"` | unobserved candidate | no observed activity | — | 5,884.98 | 0.76 |
| eth-047 | generated_valid | `"0x0d9f7e0f240d000303b18efdf7cbd7c41049527e"` | unobserved candidate | no observed activity | — | 6,979.43 | 0.79 |
| eth-048 | generated_valid | `"0xe4ae5f19b13d1b383cb6c950d0dc5c4206402b77"` | unobserved candidate | no observed activity | — | 9,705.44 | 1.71 |
| eth-049 | generated_valid | `"0x0ec23ed32cc5d9663b4f7052548d3c37264efd4f"` | unobserved candidate | no observed activity | — | 5,519.00 | 0.75 |
| eth-050 | generated_valid | `"0x55c30b5034ce3e77c6c64eb42946a112287b6a28"` | unobserved candidate | no observed activity | — | 5,278.17 | 1.47 |
| eth-051 | generated_valid | `"0xc09cc5c8b959b53d56cab2c78adb00e469490fde"` | unobserved candidate | no observed activity | — | 6,985.64 | 1.63 |
| eth-052 | generated_valid | `"0x34d2ded544ec43d3f76262f346326d9c73c637db"` | unobserved candidate | no observed activity | — | 4,585.58 | 1.17 |
| eth-053 | generated_valid | `"0xa7a024d96066322cd9ef0eb90277263fe1922948"` | unobserved candidate | no observed activity | — | 6,809.20 | 0.75 |
| eth-054 | generated_valid | `"0xb9e83f94ea3768822f32d529fe98ebf911f09f9f"` | unobserved candidate | no observed activity | — | 7,117.20 | 0.93 |
| eth-055 | generated_valid | `"0xc45a2acbbdec947e5b16a5102098854d516e88cb"` | unobserved candidate | no observed activity | — | 7,768.81 | 1.24 |
| eth-056 | generated_valid | `"0x91fc52dbdacecf60132a46a0f2e72015d37e9e6c"` | unobserved candidate | no observed activity | — | 5,207.71 | 0.76 |
| eth-057 | generated_valid | `"0xe63a40cfee31a745de2761daf89905d1c823258a"` | unobserved candidate | no observed activity | — | 7,954.32 | 1.25 |
| eth-058 | generated_valid | `"0xf3c0fb4452bc1ff845f23443f5e5143a649f186f"` | unobserved candidate | no observed activity | — | 5,199.24 | 0.74 |
| eth-059 | generated_valid | `"0x4031d3cdd02f329e9af1b494e78270255b724ada"` | unobserved candidate | no observed activity | — | 4,795.82 | 0.91 |
| eth-060 | generated_valid | `"0x15dd314a2763474e0d5768c82a88aa686c38f5ae"` | unobserved candidate | no observed activity | — | 6,245.41 | 1.10 |
| eth-061 | generated_valid | `"0x81328784d16a252808fab1b17ca695151eb79729"` | unobserved candidate | no observed activity | — | 6,620.32 | 0.74 |
| eth-062 | generated_valid | `"0x5325fa1c98d091c07d63aa31cfa6abe8b2cb8343"` | unobserved candidate | no observed activity | — | 6,667.39 | 0.75 |
| eth-063 | generated_valid | `"0xc29cbe4dcc1d39b7d18c0c16a6cf43b47e38ba51"` | unobserved candidate | no observed activity | — | 4,941.15 | 1.07 |
| eth-064 | generated_valid | `"0x2e0fe7a6e908ad05efe54e954a70c022cadd1467"` | unobserved candidate | no observed activity | — | 5,667.32 | 0.86 |
| eth-065 | generated_valid | `"0xadc677dc2cdad427d17b921fd158fc454860bf5d"` | unobserved candidate | no observed activity | — | 7,498.52 | 1.45 |
| eth-066 | generated_valid | `"0x0b2a274c002b9469cd112ee4842ecaa347e61cb7"` | unobserved candidate | no observed activity | — | 8,363.58 | 0.95 |
| eth-067 | generated_valid | `"0xe3685a40fa74deba76da12d2a3b914cd677f7ed7"` | unobserved candidate | no observed activity | — | 8,259.56 | 0.84 |
| eth-068 | generated_valid | `"0x8af22802dfd21cb8f21736d203a3a142b9edf1ff"` | unobserved candidate | no observed activity | — | 6,097.30 | 1.18 |
| eth-069 | generated_valid | `"0x9a33fc9010949bdef4fce8285ce842174592fbc4"` | unobserved candidate | no observed activity | — | 6,616.51 | 0.74 |
| eth-070 | generated_valid | `"0xe1993bd0dc5ba0e38fd4bd6e0907061e96ebaf99"` | unobserved candidate | no observed activity | — | 5,850.52 | 1.58 |
| eth-071 | generated_valid | `"0x48577c94da33e60423f741022e4842d65f5e4954"` | unobserved candidate | no observed activity | — | 7,731.42 | 1.11 |
| eth-072 | generated_valid | `"0x614870240de6379b49a00dd45f3c384939d36964"` | unobserved candidate | no observed activity | — | 6,624.26 | 0.84 |
| eth-073 | generated_valid | `"0xdcb2afaf2cb6e4f35807e2c996af8f9857518062"` | unobserved candidate | no observed activity | — | 5,786.73 | 0.75 |
| eth-074 | generated_valid | `"0xbbe35e12f8779e9162dc31fafe1b12f0a7b12c94"` | unobserved candidate | no observed activity | — | 7,025.69 | 1.10 |
| eth-075 | generated_valid | `"0xf8fd1362b19ba5dc5ac2506d9c0e78ff4df6daf7"` | unobserved candidate | no observed activity | — | 6,070.98 | 0.74 |
| eth-076 | generated_valid | `"0x084c2026aef4c3ba51736174a1d4f5272f516e90"` | unobserved candidate | no observed activity | — | 7,847.12 | 0.78 |
| eth-077 | generated_valid | `"0xcac36747d1cd34199cab69721cb6163b384ffe33"` | unobserved candidate | no observed activity | — | 5,826.32 | 1.50 |
| eth-078 | generated_valid | `"0x8acd8c981d5e97fb3a1c03afe34548fb3cc6e61d"` | unobserved candidate | no observed activity | — | 6,577.02 | 0.75 |
| eth-079 | generated_valid | `"0xc931ebb8f72fb21d9cc1f72ac847396fcd3e0721"` | unobserved candidate | no observed activity | — | 6,050.88 | 0.92 |
| eth-080 | generated_valid | `"0x5d736d69aaf0f48fbe1f4129e2b476991b0ef36d"` | unobserved candidate | no observed activity | — | 4,957.58 | 1.27 |
| eth-081 | malformed | `""` | invalid format | invalid format | — | 5.01 | 0.49 |
| eth-082 | malformed | `"0x"` | invalid format | invalid format | — | 4.87 | 0.52 |
| eth-083 | malformed | `"0x1234"` | invalid format | invalid format | — | 2.75 | 0.60 |
| eth-084 | malformed | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 6.09 | 0.53 |
| eth-085 | malformed | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 5.21 | 0.56 |
| eth-086 | malformed | `"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 8.49 | 0.54 |
| eth-087 | malformed | `"0Xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 4.28 | 0.62 |
| eth-088 | malformed | `"0xgggggggggggggggggggggggggggggggggggggggg"` | invalid format | invalid format | — | 5.21 | 0.51 |
| eth-089 | malformed | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaz"` | invalid format | invalid format | — | 4.43 | 0.56 |
| eth-090 | malformed | `" 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 4.17 | 0.54 |
| eth-091 | malformed | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "` | invalid format | invalid format | — | 5.23 | 0.76 |
| eth-092 | malformed | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"` | invalid format | invalid format | — | 3.09 | 0.54 |
| eth-093 | malformed | `"\t0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 5.50 | 0.54 |
| eth-094 | malformed | `"0x\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21"` | invalid format | invalid format | — | 4.46 | 0.95 |
| eth-095 | malformed | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\ud83d\ude00"` | invalid format | invalid format | — | 5.69 | 1.41 |
| eth-096 | malformed | `"0\u0445aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 2.71 | 0.56 |
| eth-097 | malformed | `"vitalik.eth"` | invalid format | invalid format | — | 4.93 | 0.51 |
| eth-098 | malformed | `"https://etherscan.io/address/0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 2.13 | 0.96 |
| eth-099 | malformed | `"0xaaaaaaaaaaaaaaaaaaaa_aaaaaaaaaaaaaaaaaaa"` | invalid format | invalid format | — | 31.64 | 5.31 |
| eth-100 | malformed | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\u0000"` | invalid format | invalid format | — | 2.26 | 0.61 |
