# walletAgeDays API benchmark

Completed: 2026-11-05T20:01:34.955050Z

This run tests the rewritten production factory and real local `/walletAgeDays` HTTP route. Success is a bare JSON integer. Failures remain strings with HTTP error statuses; they are never fake zero-day ages.

Paid providers configured: none; public sources only.

## Latency

Twenty malformed inputs are excluded from valid-address timing. The corpus has 40 known-used addresses, 40 synthetic format-valid candidates, and 20 malformed inputs.

| Phase | Valid inputs | Median | p95 | Maximum | Cache hits | Calls entering HTTP client | Requests over 3 s |
|---|---:|---:|---:|---:|---:|---:|---:|
| cold | 80 | 0.715326 s | 2.220531 s | 2.474024 s | 0 | 630 | 0 |
| warm | 80 | 0.156562 s | 0.539928 s | 0.755462 s | 40 | 200 | 0 |
| polygon_alias | 40 | 0.001308 s | 0.002374 s | 0.002844 s | 40 | 0 | 0 |

For the **40 known-used addresses alone**, cold median was **1.504325 s**, p95 **2.467981 s**, maximum **2.474024 s**. The 80-input cold timing also includes unsuccessful age lookups for synthetic candidates; it must not be read as successful-age latency only.

Warm permanent-cache hits alone: **40 requests**, median **4.739 ms**, p95 **9.720 ms**, maximum **14.474 ms**. Unobserved/unknown candidates are deliberately not cached, so the warm pass also contains uncached provider retries.

The application waits within a 2.6-second budget, reserving time for persistence and serialization. Measured maximum is specific to this sequential run, not a guarantee that every provider can supply complete data in three seconds. Timeouts and exhausted capacity return an error rather than a fabricated age.

## Coverage and checks

| Phase | Known-used age returned | HTTP statuses |
|---|---:|---|
| cold | 40/40 | {"200": 40, "400": 20, "404": 2, "503": 38} |
| warm | 40/40 | {"200": 40, "400": 20, "503": 40} |
| polygon_alias | 40/40 | {"200": 40} |

Malformed rejection: **20/20**, with **0** provider attempts. Format-valid syntax accepted: **80/80**. The epoch-to-days formula matched **120/120** successful responses. Saved epochs remained identical on **80/80** successful repeated checks.

Synthetic-candidate cold outcomes: `{"404": 2, "503": 38}`. These generated strings have no private keys and are expected to be unobserved, but their complete historical absence is not independently proven. 404 means no observation found within these sources; 503 means unavailable/incomplete evidence. Neither has a defined age.

All successful ages are lower bounds from finalized observed activity within the implemented coverage. Ethereum, EVM, and Polygon selectors intentionally share namespace `evm:1:v1`; the response header states `X-Wallet-Age-Coverage: ethereum`. Alias reuse does not mean Polygon history was searched. More chains require a new coverage namespace and a minimum over their observations.

## Independent deployment controls

These are the existing 20 independently collected deployment receipt/block/archive-state controls. Agreement measures deployment timing, not proof of universally earliest address appearance; prefunding or other earlier activity can exist.

| Contract | Returned epoch | Reference deployment epoch | Difference (s) |
|---|---:|---:|---:|
| WETH9 | 1513077455 | 1513077455 | 0 |
| USDC | 1533324504 | 1533324504 | 0 |
| USDT | 1511829681 | 1511829681 | 0 |
| DAI | 1573672677 | 1573672677 | 0 |
| UNI | 1600107086 | 1600107086 | 0 |
| LINK | 1505597189 | 1505597189 | 0 |
| WBTC | 1543095952 | 1543095952 | 0 |
| AAVE | 1600970788 | 1600970788 | 0 |
| MKR | 1511634257 | 1511634257 | 0 |
| COMP | 1583280535 | 1583280535 | 0 |
| Uniswap V2 Factory | 1588610042 | 1588610042 | 0 |
| Uniswap V2 Router02 | 1591388241 | 1591388241 | 0 |
| Uniswap V3 Factory | 1620156420 | 1620156420 | 0 |
| Uniswap V3 SwapRouter | 1620156641 | 1620156641 | 0 |
| Uniswap SwapRouter02 | 1639503446 | 1639503446 | 0 |
| Uniswap V3 NonfungiblePositionManager | 1620156913 | 1620156913 | 0 |
| Permit2 | 1668647879 | 1668647879 | 0 |
| ENS Registry | 1580344632 | 1580344632 | 0 |
| ENS BaseRegistrarImplementation | 1580345034 | 1580345034 | 0 |
| Beacon deposit contract | 1602667372 | 1602667372 | 0 |

Exact deployment agreement: **20/20**; missing: **0**.

## Permanent cache at one million records

A separate actual **1,000,000-row** production compact SQLite store used **66.30 MiB disk** and **21.27 MiB process peak RSS** (**3.95 MiB** incremental). Actual `get()` lookup median was **0.478 ms**, p95 **0.708 ms**.

Rows never expire or evict. At the global one-million-row limit, new facts bypass cache admission while existing epochs can still improve. The capacity boundary was exercised in the store experiment. SQLite does not load a million-entry Python dictionary. Lookup timing follows population with warm filesystem caches; process RSS excludes the OS page cache. Bulk-load throughput differs from individual `put_min()` throughput. Raw experiment: [wallet-age-days-million.json](../wallet-age-days-million.json).

## Reproducibility

- Dataset SHA-256: `72d1a2fa26ad76e467e771e60c448aae16b49d319d27243278bdf751fd46dee0`.
- Python: `3.13.7 (main, Aug 14 2025, 11:12:11) [Clang 17.0.0 (clang-1700.0.13.3)]`.
- Sequential requests through an actual local HTTP server; network propagation outside localhost is not measured.
- Cold-pass pause: 0.15 seconds, excluded from timing.
- Runtime source files unchanged during run: **True**.
- Application regression tests: **224 passed** before this run, including the new integer-response HTTP API.
- Total API requests: **240**; total instrumented upstream attempts: **838**.
- Forbidden balance/nonce/code RPC methods observed: **0**.
- Meter attempts include HTTP calls rejected locally after their deadline; late source completion is recorded separately.
- The real clock is instrumented to record the exact `as_of` used to calculate each returned integer.
- Raw API rows: `results.jsonl`; complete provider instrumentation: `upstream-events.json`; aggregates: `summary.json`.
- Reference corpus and deployment controls are reused; this run does not establish exact first-use ground truth for every EOA.

Reproduce into a new output directory:

```bash
python3 benchmarks/run_wallet_age_days_benchmark.py --output benchmarks/run-wallet-age-days-new
```

## Per-input results

| ID | Category | Cold seconds | Warm seconds | Cold HTTP | Cold integer | Saved epoch |
|---|---|---:|---:|---:|---:|---:|
| eth-001 | existing_eoa | 1.687741 | 0.005544 | 200 | 4056 | 1443428683 |
| eth-002 | existing_eoa | 1.654205 | 0.007517 | 200 | 4107 | 1439048640 |
| eth-003 | existing_eoa | 1.270945 | 0.003415 | 200 | 198 | 1776798083 |
| eth-004 | existing_eoa | 0.939590 | 0.002364 | 200 | 36 | 1790780723 |
| eth-005 | existing_eoa | 0.956635 | 0.002433 | 200 | 7 | 1793292215 |
| eth-006 | existing_eoa | 0.779856 | 0.001985 | 200 | 8 | 1793202539 |
| eth-007 | existing_eoa | 1.043375 | 0.010744 | 200 | 182 | 1778143487 |
| eth-008 | existing_eoa | 1.843185 | 0.009666 | 200 | 55 | 1789129859 |
| eth-009 | existing_eoa | 1.025630 | 0.006590 | 200 | 76 | 1787320835 |
| eth-010 | existing_eoa | 2.125717 | 0.008255 | 200 | 540 | 1747201343 |
| eth-011 | existing_eoa | 1.649723 | 0.005275 | 200 | 15 | 1792556987 |
| eth-012 | existing_eoa | 0.764107 | 0.003204 | 200 | 18 | 1792341875 |
| eth-013 | existing_eoa | 1.418699 | 0.008385 | 200 | 731 | 1730734559 |
| eth-014 | existing_eoa | 1.337055 | 0.002975 | 200 | 114 | 1784052479 |
| eth-015 | existing_eoa | 0.872202 | 0.004867 | 200 | 24 | 1791777815 |
| eth-016 | existing_eoa | 0.783851 | 0.004611 | 200 | 153 | 1780687259 |
| eth-017 | existing_eoa | 1.327954 | 0.002156 | 200 | 731 | 1730736719 |
| eth-018 | existing_eoa | 1.224972 | 0.014474 | 200 | 1128 | 1696442603 |
| eth-019 | existing_eoa | 0.963079 | 0.006859 | 200 | 420 | 1757563883 |
| eth-020 | existing_eoa | 1.054932 | 0.004460 | 200 | 332 | 1765201931 |
| eth-021 | existing_contract | 1.928340 | 0.006308 | 200 | 3250 | 1513077455 |
| eth-022 | existing_contract | 1.381382 | 0.007211 | 200 | 3016 | 1533324504 |
| eth-023 | existing_contract | 0.949982 | 0.006259 | 200 | 3264 | 1511829681 |
| eth-024 | existing_contract | 1.460243 | 0.002018 | 200 | 2549 | 1573672677 |
| eth-025 | existing_contract | 2.026117 | 0.002950 | 200 | 2243 | 1600107086 |
| eth-026 | existing_contract | 2.055996 | 0.002947 | 200 | 3336 | 1505597189 |
| eth-027 | existing_contract | 2.467767 | 0.009053 | 200 | 2902 | 1543095952 |
| eth-028 | existing_contract | 2.156145 | 0.002331 | 200 | 2233 | 1600970788 |
| eth-029 | existing_contract | 1.246250 | 0.001926 | 200 | 3267 | 1511634257 |
| eth-030 | existing_contract | 1.548407 | 0.008236 | 200 | 2437 | 1583280535 |
| eth-031 | existing_contract | 1.695606 | 0.001763 | 200 | 2376 | 1588610042 |
| eth-032 | existing_contract | 2.070297 | 0.005383 | 200 | 2343 | 1591388241 |
| eth-033 | existing_contract | 1.861705 | 0.006648 | 200 | 2011 | 1620156420 |
| eth-034 | existing_contract | 2.207662 | 0.003324 | 200 | 2011 | 1620156641 |
| eth-035 | existing_contract | 1.361866 | 0.004051 | 200 | 1787 | 1639503446 |
| eth-036 | existing_contract | 2.465035 | 0.008018 | 200 | 2011 | 1620156913 |
| eth-037 | existing_contract | 2.472062 | 0.003010 | 200 | 1449 | 1668647879 |
| eth-038 | existing_contract | 2.474024 | 0.006248 | 200 | 2471 | 1580344632 |
| eth-039 | existing_contract | 1.587266 | 0.004494 | 200 | 2471 | 1580345034 |
| eth-040 | existing_contract | 2.069474 | 0.002408 | 200 | 2213 | 1602667372 |
| eth-041 | generated_valid | 0.525184 | 0.503169 | 503 | — | — |
| eth-042 | generated_valid | 0.326661 | 0.539108 | 503 | — | — |
| eth-043 | generated_valid | 0.336930 | 0.455102 | 503 | — | — |
| eth-044 | generated_valid | 0.330692 | 0.519262 | 503 | — | — |
| eth-045 | generated_valid | 0.453885 | 0.469766 | 404 | — | — |
| eth-046 | generated_valid | 0.463946 | 0.341391 | 503 | — | — |
| eth-047 | generated_valid | 0.328015 | 0.298650 | 503 | — | — |
| eth-048 | generated_valid | 0.355059 | 0.332128 | 503 | — | — |
| eth-049 | generated_valid | 0.313744 | 0.326394 | 503 | — | — |
| eth-050 | generated_valid | 0.346452 | 0.507760 | 503 | — | — |
| eth-051 | generated_valid | 0.368795 | 0.309062 | 503 | — | — |
| eth-052 | generated_valid | 0.338503 | 0.323925 | 503 | — | — |
| eth-053 | generated_valid | 0.339162 | 0.328774 | 503 | — | — |
| eth-054 | generated_valid | 0.330827 | 0.339830 | 503 | — | — |
| eth-055 | generated_valid | 0.324194 | 0.473612 | 503 | — | — |
| eth-056 | generated_valid | 0.316938 | 0.320839 | 503 | — | — |
| eth-057 | generated_valid | 0.319750 | 0.465871 | 503 | — | — |
| eth-058 | generated_valid | 0.666545 | 0.331390 | 503 | — | — |
| eth-059 | generated_valid | 0.318733 | 0.663914 | 503 | — | — |
| eth-060 | generated_valid | 0.336156 | 0.755462 | 503 | — | — |
| eth-061 | generated_valid | 0.314543 | 0.315613 | 503 | — | — |
| eth-062 | generated_valid | 0.537579 | 0.332141 | 503 | — | — |
| eth-063 | generated_valid | 0.324129 | 0.319120 | 503 | — | — |
| eth-064 | generated_valid | 0.349641 | 0.515518 | 503 | — | — |
| eth-065 | generated_valid | 0.319700 | 0.555518 | 503 | — | — |
| eth-066 | generated_valid | 0.529547 | 0.313580 | 404 | — | — |
| eth-067 | generated_valid | 0.365417 | 0.660717 | 503 | — | — |
| eth-068 | generated_valid | 0.488478 | 0.538749 | 503 | — | — |
| eth-069 | generated_valid | 0.335996 | 0.333512 | 503 | — | — |
| eth-070 | generated_valid | 0.309487 | 0.514743 | 503 | — | — |
| eth-071 | generated_valid | 0.582062 | 0.311616 | 503 | — | — |
| eth-072 | generated_valid | 0.328547 | 0.335723 | 503 | — | — |
| eth-073 | generated_valid | 0.581126 | 0.327920 | 503 | — | — |
| eth-074 | generated_valid | 0.561212 | 0.337608 | 503 | — | — |
| eth-075 | generated_valid | 0.343282 | 0.310889 | 503 | — | — |
| eth-076 | generated_valid | 0.328787 | 0.321653 | 503 | — | — |
| eth-077 | generated_valid | 0.334058 | 0.319460 | 503 | — | — |
| eth-078 | generated_valid | 0.296638 | 0.371063 | 503 | — | — |
| eth-079 | generated_valid | 0.349089 | 0.384253 | 503 | — | — |
| eth-080 | generated_valid | 0.324039 | 0.442342 | 503 | — | — |
| eth-081 | malformed | 0.004785 | 0.001665 | 400 | — | — |
| eth-082 | malformed | 0.004273 | 0.001960 | 400 | — | — |
| eth-083 | malformed | 0.003020 | 0.001469 | 400 | — | — |
| eth-084 | malformed | 0.002181 | 0.002525 | 400 | — | — |
| eth-085 | malformed | 0.003099 | 0.006410 | 400 | — | — |
| eth-086 | malformed | 0.001414 | 0.002055 | 400 | — | — |
| eth-087 | malformed | 0.005665 | 0.001875 | 400 | — | — |
| eth-088 | malformed | 0.003876 | 0.001339 | 400 | — | — |
| eth-089 | malformed | 0.003340 | 0.001135 | 400 | — | — |
| eth-090 | malformed | 0.003175 | 0.005612 | 400 | — | — |
| eth-091 | malformed | 0.006710 | 0.005732 | 400 | — | — |
| eth-092 | malformed | 0.004192 | 0.002724 | 400 | — | — |
| eth-093 | malformed | 0.003938 | 0.001126 | 400 | — | — |
| eth-094 | malformed | 0.007297 | 0.001643 | 400 | — | — |
| eth-095 | malformed | 0.004004 | 0.001699 | 400 | — | — |
| eth-096 | malformed | 0.004742 | 0.007530 | 400 | — | — |
| eth-097 | malformed | 0.003616 | 0.004087 | 400 | — | — |
| eth-098 | malformed | 0.004554 | 0.001374 | 400 | — | — |
| eth-099 | malformed | 0.024605 | 0.003830 | 400 | — | — |
| eth-100 | malformed | 0.004969 | 0.001607 | 400 | — | — |
