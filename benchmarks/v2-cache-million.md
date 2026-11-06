# V2 SQLite cache capacity benchmark

2026-11-06T14:25:42.613532+00:00

Synthetic address/epoch fixture; no provider requests or API keys. This measures local cache operations, not HTTP latency or wallet-age accuracy.

- Entries: **1,000,000**; metadata count matches.
- Fixture bulk load: 4.768 seconds (not cache insertion latency).
- SQLite bytes after close: 157,855,744.
- Capacity, MIN epochs, LRU eviction and recency after reopening: **passed**.

| Operation at capacity | Samples | Median ms | p95 ms | Maximum ms |
| --- | ---: | ---: | ---: | ---: |
| cache_hit | 1000 | 0.124 | 0.172 | 2.040 |
| minimum_upsert_at_capacity | 1000 | 0.121 | 0.170 | 1.410 |
| insert_with_lru_eviction | 1000 | 0.156 | 0.218 | 1.634 |

One sequential client on the development machine with a warm filesystem cache. Percentiles use nearest rank. Temporary database and sidecars are deleted; this report contains no large database artifact.
