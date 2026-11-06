# V2 walletAgeDays benchmark

Measured 2026-11-06T14:26:22.778304Z.

Real local HTTP and configured provider requests; fresh isolated SQLite LRU; one client at a time.

| Cohort | Count | Integer ages | Median | p95 | Maximum | Over 3 s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| uncached_known_used | 40 | 40 | 0.7226 s | 0.9960 s | 1.0037 s | 0 |
| uncached_format_valid | 80 | 40 | 0.6978 s | 0.9896 s | 1.0169 s | 0 |
| uncached_overall | 100 | 40 | 0.6933 s | 0.9795 s | 1.0169 s | 0 |
| cached_positive_repeats | 40 | 40 | 0.0008 s | 0.0018 s | 0.0058 s | 0 |

Initial deployment-control epoch agreement: 17/20. After bounded refinement: 17/20.

Background epoch corrections: 1. Positive-repeat cache hits: 40. Completed-day formula checks: 80/80.

## Interpretation

The corpus contains 40 known-used controls, 40 format-valid synthetic candidates and 20 malformed inputs. Synthetic absence is not established; unavailable history must not be scored as age zero. Contract deployment is independent activity evidence, not proof that no earlier prefunding occurred. These controls measure deployment agreement, not universal wallet-age or fraud-detection accuracy.

Cold response latency excludes deliberate pacing and the wait for bounded background refinement. Warm results repeat only cold successes after refinement. This measures a small warmed cache, not one-million-entry capacity or production concurrency. Provider-side caches cannot be cleared. No provider-attempt count, billing count or hosted-site latency is asserted.

Artifacts: `metadata.json`, `results.jsonl`, `summary.json`; credentials and configured URLs are omitted.

## Deployment-control gaps

| Case | Initial delta, seconds | Refined delta, seconds |
| --- | ---: | ---: |
| eth-024 | 320622 | 320622 |
| eth-029 | 2861 | 2861 |
| eth-037 | 5364 | 5364 |

These discrepancies remain discovery/verification limitations. A valid response is an observed lower bound, not proof of the exact first-ever appearance. See `summary.json` for every control.
