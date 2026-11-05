# Fast address-age API benchmark

Run completed: 2026-11-05T19:38:23.409173Z

The same 100-input corpus is exercised through the production Python factory and local HTTP endpoint. The result reports speed, usable coverage, and timestamp evidence separately.

Configured paid providers: none; public sources only.

## Latency

Valid-address timing excludes the 20 malformed inputs, which are rejected locally.

| Phase | Valid requests | Median | p95 | Maximum | All requests over 3 s | Result-cache hits | Permanent-age hits |
|---|---:|---:|---:|---:|---:|---:|---:|
| detailed_cold | 80 | 1.1867 s | 2.4600 s | 2.4724 s | 0 | 0 | 0 |
| detailed_warm | 80 | 0.0014 s | 0.0025 s | 0.0046 s | 0 | 80 | 40 |
| permanent_age | 40 | 0.0013 s | 0.0015 s | 0.0017 s | 0 | 0 | 40 |

Against the saved baseline's same 80 valid inputs, cold median fell from 8.1002 s to 1.1867 s (85.4% lower); maximum fell from 25.3810 s to 2.4724 s. These are two sequential runs at different times, not a controlled simultaneous provider comparison.

Maximum timing is measured for this run; it is not a guarantee of successful complete third-party data within three seconds. At the request deadline the service may return partial evidence or unavailable/unknown.

## Classification and age coverage

| Phase | Format judgments | Known-used correct / eligible | Known-used unknown | Synthetic agreement / eligible | Synthetic unknown | Known-used timestamps | Age statuses |
|---|---:|---:|---:|---:|---:|---:|---|
| detailed_cold | 100/100 | 40/40 | 0 | 40/40 | 0 | 40/40 | {"lower_bound": 40} |
| detailed_warm | 100/100 | 40/40 | 0 | 40/40 | 0 | 40/40 | {"lower_bound": 40} |
| permanent_age | 40/40 | 40/40 | 0 | 0/0 | 0 | 40/40 | {"lower_bound": 40} |

Synthetic addresses are deterministic fabricated 20-byte strings, without private keys. Their expected lack of activity is not absolute historical ground truth. The 40 existing controls have positive on-chain evidence. The corpus is a convenience sample and does not estimate performance across all Ethereum addresses.

An observed timestamp provides a minimum age when history coverage is incomplete. A missing timestamp remains unknown. No wallet-key creation date can be inferred from a generated address.

## Independent deployment controls

References come from separately collected canonical deployment receipts/blocks and archive code checks. Matching a contract deployment timestamp measures deployment agreement; an address may have been funded or mentioned before deployment.

| Assessment | Controls |
|---|---:|
| agrees_with_deployment | 20 |

| Contract | Returned epoch | Deployment epoch | Difference | Age status |
|---|---:|---:|---:|---|
| WETH9 | 1513077455 | 1513077455 | 0 s | lower_bound |
| USDC | 1533324504 | 1533324504 | 0 s | lower_bound |
| USDT | 1511829681 | 1511829681 | 0 s | lower_bound |
| DAI | 1573672677 | 1573672677 | 0 s | lower_bound |
| UNI | 1600107086 | 1600107086 | 0 s | lower_bound |
| LINK | 1505597189 | 1505597189 | 0 s | lower_bound |
| WBTC | 1543095952 | 1543095952 | 0 s | lower_bound |
| AAVE | 1600970788 | 1600970788 | 0 s | lower_bound |
| MKR | 1511634257 | 1511634257 | 0 s | lower_bound |
| COMP | 1583280535 | 1583280535 | 0 s | lower_bound |
| Uniswap V2 Factory | 1588610042 | 1588610042 | 0 s | lower_bound |
| Uniswap V2 Router02 | 1591388241 | 1591388241 | 0 s | lower_bound |
| Uniswap V3 Factory | 1620156420 | 1620156420 | 0 s | lower_bound |
| Uniswap V3 SwapRouter | 1620156641 | 1620156641 | 0 s | lower_bound |
| Uniswap SwapRouter02 | 1639503446 | 1639503446 | 0 s | lower_bound |
| Uniswap V3 NonfungiblePositionManager | 1620156913 | 1620156913 | 0 s | lower_bound |
| Permit2 | 1668647879 | 1668647879 | 0 s | lower_bound |
| ENS Registry | 1580344632 | 1580344632 | 0 s | lower_bound |
| ENS BaseRegistrarImplementation | 1580345034 | 1580345034 | 0 s | lower_bound |
| Beacon deposit contract | 1602667372 | 1602667372 | 0 s | lower_bound |

## Provider use and permanent storage

| Phase | HTTP-client call attempts | RPC method attempts | HTTP statuses |
|---|---:|---:|---|
| detailed_cold | 840 | 768 | {"200": 80, "400": 20} |
| detailed_warm | 0 | 0 | {"200": 80, "400": 20} |
| permanent_age | 0 | 0 | {"200": 40} |

Persisted age rows after cold/warm: 40. Permanent-age pass inputs: 40. That pass removes the isolated TTL cache entries before checking persisted positive addresses with details=false. Mutable balances/counts are not permanently cached.

Call attempts include requests rejected before networking because their deadline had already expired. Timed-out source jobs can finish after the API response; complete upstream instrumentation is saved separately. The global log contains 841 attempts; per-request snapshots can omit attempts that start between response completion and the next request.

AgeDays formula checks passed for 120/120 timestamp-bearing responses: `max(0, floor((age_as_of - first_txn) / 86400))`. The stored first epoch stayed equal to its cold value for 80/80 repeated responses.

The permanent cache preserves the earliest finalized observation it knows; it does not automatically rescan partial history. A later verified earlier observation can correct the minimum through `observe()`. Until then an incomplete record continues to return `lower_bound`, even though its timestamp never expires.

## One-million-address storage experiment

These separate offline experiments used actual one-million-row stores, not estimates from a tiny sample. Synthetic records require no provider calls or private keys.

| Storage | Process peak RSS | Additional peak RSS | Durable disk file | Lookup median | Lookup p95 |
|---|---:|---:|---:|---:|---:|
| SQLite, production provenance schema | 24.62 MiB | 6.18 MiB | 279.04 MiB | 0.494 ms | 0.626 ms |
| Python dictionary, tuple key + integer epoch | 293.97 MiB | 277.96 MiB | Not persisted | 1.000 µs | 1.667 µs |

SQLite stores protocol, network, address, epoch and provenance/finality/scope metadata; the dictionary stores only a shared chain string, address, tuple and epoch. Their contents differ. Dictionary lookups exclude constructing the lookup key. SQLite lookup timings call the production `get()` method after population with a warm OS filesystem cache. RSS excludes OS filesystem-cache accounting; SQLite disk size is not RAM use. SQLite population uses bulk inserts, so its load time is not production `observe()` throughput.

The SQLite approach keeps the million records on disk with a bounded connection page cache, survives restarts, and avoids loading the full Python dictionary. Raw measurements: [SQLite](../age-store-million.json), [Python dictionary](../age-dictionary-million.json).

The [service README](../../README.md) documents the current cache/deadline policy and official API research. The age semantics follow [first activity](https://docs.moralis.com/data-api/evm/wallet/wallet-insights), while permanent Ethereum observations require [finalized canonical blocks](https://ethereum.org/developers/docs/consensus-mechanisms/pos/). [SQLite cache sizing](https://www.sqlite.org/pragma.html#pragma_cache_size) controls its connection page cache.

## Reproducibility and limits

- Dataset SHA-256: `72d1a2fa26ad76e467e771e60c448aae16b49d319d27243278bdf751fd46dee0`.
- Python: `3.13.7 (main, Aug 14 2025, 11:12:11) [Clang 17.0.0 (clang-1700.0.13.3)]`.
- Sequential HTTP requests; pause between cold requests: 0.15 s, excluded from latency.
- Production files unchanged during run: True.
- After the run, documentation-only corrections updated README cache/memory descriptions and OpenAPI metadata types. No runtime implementation or recorded benchmark response was changed by those corrections.
- Validation: 174 application regression tests passed after integration, including localhost HTTP checks.
- Raw responses: `results.jsonl`; aggregate metrics: `summary.json`; upstream attempts: `upstream-events.json`.
- The reference corpus is reused from the baseline; this run does not perform new independent balance verification.
- A deadline bounds waiting, not upstream availability. Unknown and partial answers are retained in reported denominators.

## Per-input results

| ID | Category | Cold seconds | Warm seconds | HTTP | Seen | First epoch | Age status |
|---|---|---:|---:|---:|---|---:|---|
| eth-001 | existing_eoa | 2.1065 s | 0.0021 s | 200 | True | 1443428683 | lower_bound |
| eth-002 | existing_eoa | 2.4724 s | 0.0014 s | 200 | True | 1439048640 | lower_bound |
| eth-003 | existing_eoa | 1.6070 s | 0.0015 s | 200 | True | 1776798083 | lower_bound |
| eth-004 | existing_eoa | 1.8549 s | 0.0015 s | 200 | True | 1790780723 | lower_bound |
| eth-005 | existing_eoa | 1.3467 s | 0.0015 s | 200 | True | 1793292215 | lower_bound |
| eth-006 | existing_eoa | 1.3011 s | 0.0014 s | 200 | True | 1793202539 | lower_bound |
| eth-007 | existing_eoa | 0.9259 s | 0.0014 s | 200 | True | 1778143487 | lower_bound |
| eth-008 | existing_eoa | 0.9879 s | 0.0025 s | 200 | True | 1789129859 | lower_bound |
| eth-009 | existing_eoa | 1.7731 s | 0.0014 s | 200 | True | 1787320835 | lower_bound |
| eth-010 | existing_eoa | 1.5969 s | 0.0015 s | 200 | True | 1747201343 | lower_bound |
| eth-011 | existing_eoa | 2.2498 s | 0.0046 s | 200 | True | 1792556987 | lower_bound |
| eth-012 | existing_eoa | 1.5206 s | 0.0025 s | 200 | True | 1792341875 | lower_bound |
| eth-013 | existing_eoa | 1.4311 s | 0.0015 s | 200 | True | 1730734559 | lower_bound |
| eth-014 | existing_eoa | 1.3484 s | 0.0021 s | 200 | True | 1784052479 | lower_bound |
| eth-015 | existing_eoa | 1.1192 s | 0.0014 s | 200 | True | 1791777815 | lower_bound |
| eth-016 | existing_eoa | 1.8299 s | 0.0016 s | 200 | True | 1780687259 | lower_bound |
| eth-017 | existing_eoa | 1.4433 s | 0.0014 s | 200 | True | 1730736719 | lower_bound |
| eth-018 | existing_eoa | 1.2930 s | 0.0014 s | 200 | True | 1696442603 | lower_bound |
| eth-019 | existing_eoa | 2.4597 s | 0.0016 s | 200 | True | 1757563883 | lower_bound |
| eth-020 | existing_eoa | 1.1995 s | 0.0014 s | 200 | True | 1765201931 | lower_bound |
| eth-021 | existing_contract | 1.9301 s | 0.0017 s | 200 | True | 1513077455 | lower_bound |
| eth-022 | existing_contract | 2.3889 s | 0.0015 s | 200 | True | 1533324504 | lower_bound |
| eth-023 | existing_contract | 1.9991 s | 0.0014 s | 200 | True | 1511829681 | lower_bound |
| eth-024 | existing_contract | 1.9864 s | 0.0014 s | 200 | True | 1573672677 | lower_bound |
| eth-025 | existing_contract | 2.2567 s | 0.0032 s | 200 | True | 1600107086 | lower_bound |
| eth-026 | existing_contract | 2.0284 s | 0.0015 s | 200 | True | 1505597189 | lower_bound |
| eth-027 | existing_contract | 1.8185 s | 0.0016 s | 200 | True | 1543095952 | lower_bound |
| eth-028 | existing_contract | 2.4656 s | 0.0014 s | 200 | True | 1600970788 | lower_bound |
| eth-029 | existing_contract | 2.3734 s | 0.0014 s | 200 | True | 1511634257 | lower_bound |
| eth-030 | existing_contract | 1.2838 s | 0.0020 s | 200 | True | 1583280535 | lower_bound |
| eth-031 | existing_contract | 1.9196 s | 0.0016 s | 200 | True | 1588610042 | lower_bound |
| eth-032 | existing_contract | 2.4706 s | 0.0019 s | 200 | True | 1591388241 | lower_bound |
| eth-033 | existing_contract | 1.4021 s | 0.0014 s | 200 | True | 1620156420 | lower_bound |
| eth-034 | existing_contract | 2.0472 s | 0.0015 s | 200 | True | 1620156641 | lower_bound |
| eth-035 | existing_contract | 2.4536 s | 0.0014 s | 200 | True | 1639503446 | lower_bound |
| eth-036 | existing_contract | 2.4651 s | 0.0018 s | 200 | True | 1620156913 | lower_bound |
| eth-037 | existing_contract | 1.0526 s | 0.0020 s | 200 | True | 1668647879 | lower_bound |
| eth-038 | existing_contract | 1.8148 s | 0.0029 s | 200 | True | 1580344632 | lower_bound |
| eth-039 | existing_contract | 2.2819 s | 0.0014 s | 200 | True | 1580345034 | lower_bound |
| eth-040 | existing_contract | 1.6696 s | 0.0017 s | 200 | True | 1602667372 | lower_bound |
| eth-041 | generated_valid | 1.3171 s | 0.0013 s | 200 | False | None | unknown |
| eth-042 | generated_valid | 0.9360 s | 0.0014 s | 200 | False | None | unknown |
| eth-043 | generated_valid | 1.5826 s | 0.0014 s | 200 | False | None | unknown |
| eth-044 | generated_valid | 0.8806 s | 0.0014 s | 200 | False | None | unknown |
| eth-045 | generated_valid | 0.9254 s | 0.0019 s | 200 | False | None | unknown |
| eth-046 | generated_valid | 0.9460 s | 0.0013 s | 200 | False | None | unknown |
| eth-047 | generated_valid | 1.2690 s | 0.0014 s | 200 | False | None | unknown |
| eth-048 | generated_valid | 0.9044 s | 0.0027 s | 200 | False | None | unknown |
| eth-049 | generated_valid | 0.9543 s | 0.0013 s | 200 | False | None | unknown |
| eth-050 | generated_valid | 0.9326 s | 0.0020 s | 200 | False | None | unknown |
| eth-051 | generated_valid | 0.9152 s | 0.0025 s | 200 | False | None | unknown |
| eth-052 | generated_valid | 1.0024 s | 0.0020 s | 200 | False | None | unknown |
| eth-053 | generated_valid | 0.8753 s | 0.0013 s | 200 | False | None | unknown |
| eth-054 | generated_valid | 1.0159 s | 0.0014 s | 200 | False | None | unknown |
| eth-055 | generated_valid | 0.9820 s | 0.0017 s | 200 | False | None | unknown |
| eth-056 | generated_valid | 0.9120 s | 0.0014 s | 200 | False | None | unknown |
| eth-057 | generated_valid | 0.9565 s | 0.0020 s | 200 | False | None | unknown |
| eth-058 | generated_valid | 0.9240 s | 0.0014 s | 200 | False | None | unknown |
| eth-059 | generated_valid | 1.0502 s | 0.0014 s | 200 | False | None | unknown |
| eth-060 | generated_valid | 1.0044 s | 0.0013 s | 200 | False | None | unknown |
| eth-061 | generated_valid | 1.1415 s | 0.0014 s | 200 | False | None | unknown |
| eth-062 | generated_valid | 0.8039 s | 0.0013 s | 200 | False | None | unknown |
| eth-063 | generated_valid | 0.8407 s | 0.0014 s | 200 | False | None | unknown |
| eth-064 | generated_valid | 0.8477 s | 0.0014 s | 200 | False | None | unknown |
| eth-065 | generated_valid | 1.0523 s | 0.0024 s | 200 | False | None | unknown |
| eth-066 | generated_valid | 1.0928 s | 0.0018 s | 200 | False | None | unknown |
| eth-067 | generated_valid | 0.8316 s | 0.0014 s | 200 | False | None | unknown |
| eth-068 | generated_valid | 0.8377 s | 0.0022 s | 200 | False | None | unknown |
| eth-069 | generated_valid | 0.9244 s | 0.0014 s | 200 | False | None | unknown |
| eth-070 | generated_valid | 0.9018 s | 0.0017 s | 200 | False | None | unknown |
| eth-071 | generated_valid | 0.8722 s | 0.0016 s | 200 | False | None | unknown |
| eth-072 | generated_valid | 1.0641 s | 0.0014 s | 200 | False | None | unknown |
| eth-073 | generated_valid | 0.9377 s | 0.0014 s | 200 | False | None | unknown |
| eth-074 | generated_valid | 0.9287 s | 0.0021 s | 200 | False | None | unknown |
| eth-075 | generated_valid | 0.8687 s | 0.0014 s | 200 | False | None | unknown |
| eth-076 | generated_valid | 1.0970 s | 0.0014 s | 200 | False | None | unknown |
| eth-077 | generated_valid | 0.7204 s | 0.0021 s | 200 | False | None | unknown |
| eth-078 | generated_valid | 1.1738 s | 0.0014 s | 200 | False | None | unknown |
| eth-079 | generated_valid | 0.8940 s | 0.0014 s | 200 | False | None | unknown |
| eth-080 | generated_valid | 1.5641 s | 0.0014 s | 200 | False | None | unknown |
| eth-081 | malformed | 0.0022 s | 0.0005 s | 400 | None | None | None |
| eth-082 | malformed | 0.0012 s | 0.0006 s | 400 | None | None | None |
| eth-083 | malformed | 0.0019 s | 0.0006 s | 400 | None | None | None |
| eth-084 | malformed | 0.0104 s | 0.0006 s | 400 | None | None | None |
| eth-085 | malformed | 0.0034 s | 0.0006 s | 400 | None | None | None |
| eth-086 | malformed | 0.0075 s | 0.0007 s | 400 | None | None | None |
| eth-087 | malformed | 0.0038 s | 0.0006 s | 400 | None | None | None |
| eth-088 | malformed | 0.0039 s | 0.0006 s | 400 | None | None | None |
| eth-089 | malformed | 0.0010 s | 0.0006 s | 400 | None | None | None |
| eth-090 | malformed | 0.0055 s | 0.0005 s | 400 | None | None | None |
| eth-091 | malformed | 0.0093 s | 0.0008 s | 400 | None | None | None |
| eth-092 | malformed | 0.0020 s | 0.0006 s | 400 | None | None | None |
| eth-093 | malformed | 0.0040 s | 0.0007 s | 400 | None | None | None |
| eth-094 | malformed | 0.0045 s | 0.0009 s | 400 | None | None | None |
| eth-095 | malformed | 0.0033 s | 0.0012 s | 400 | None | None | None |
| eth-096 | malformed | 0.0011 s | 0.0006 s | 400 | None | None | None |
| eth-097 | malformed | 0.0043 s | 0.0006 s | 400 | None | None | None |
| eth-098 | malformed | 0.0017 s | 0.0006 s | 400 | None | None | None |
| eth-099 | malformed | 0.0245 s | 0.0019 s | 400 | None | None | None |
| eth-100 | malformed | 0.0036 s | 0.0006 s | 400 | None | None | None |
