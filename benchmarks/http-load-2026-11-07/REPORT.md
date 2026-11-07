# Wallet API throughput on November 7, 2026

The local service returned 2,755 cached wallet ages per second at 64 concurrent requests. With live blockchain lookups and eight concurrent requests, it returned 16 ages in 2.01 seconds, or 7.95 successful checks per second. A burst of 40 distinct cold requests returned 32 ages and eight HTTP 503 responses. These numbers measure a single local process.

The Railway service returned HTTP 401 for the previously supplied test credential. The authenticated Railway run stopped before it could measure wallet throughput.

## How concurrency works

The public HTTP server uses aiohttp and one asyncio event loop per process. A cold `/walletAgeDays` request starts history lookups concurrently. The service admits up to 32 active discovery requests, including pending work finishing after a response, and up to 32 provider HTTP operations. SQLite work runs in worker threads and uses one lock around database transactions. Cache hits skip the discovery limit. [HTTP server](../../v2/app.py) · [Age service](../../v2/service.py) · [Provider calls](../../v2/feeds.py) · [SQLite cache](../../v2/cache.py)

The latest `/walletInsights` route runs a synchronous checker in a worker thread. The checker can run multiple network calls concurrently. The HTTP server admits four simultaneous checks shared with the other legacy routes. Requests above that limit can return HTTP 503. [HTTP dispatch](../../v2/app.py) · [Insights checker](../../wallet_insights/service.py)

The default per-client quota is **60 requests per rolling 60 seconds** across the data routes on one instance. That gives a sustained allowance of **1 request per second per client** and permits short bursts. A local run using the shipped config admitted the first 60 requests and returned HTTP 429 on the next eight. [Configuration](../../v2/config.toml) · [Quota test](burst.json)

## Measured HTTP results

TPS counts successful wallet responses divided by elapsed wall time. For age this requires HTTP 200 and a nonnegative integer. For Insights it requires HTTP 200, the requested address and a complete or partial result. All successful Insights responses in this test were partial. The latency column is the 95th percentile for successful responses, except where marked.

| Wallet request | Concurrent requests | Successful / sent | Successful TPS | p95 latency |
| --- | ---: | ---: | ---: | ---: |
| Age, cached | 1 | 14,190 / 14,190 | 1,774 | 0.71 ms |
| Age, cached | 8 | 21,103 / 21,103 | 2,637 | 3.97 ms |
| Age, cached | 32 | 21,949 / 21,949 | 2,741 | 20.08 ms |
| Age, cached | 64 | 22,086 / 22,086 | 2,755 | 32.16 ms |
| Age, cold known addresses | 1 | 4 / 4 | 2.07 | 513 ms |
| Age, cold known addresses | 4 | 12 / 12 | 3.97 | 1,015 ms |
| Age, cold known addresses | 8 | 16 / 16 | 7.95 | 1,010 ms |
| Age, one cold burst | 40 | 32 / 40 | 28.14 | 1,131 ms |
| Age, generated valid addresses | 8 | 0 / 16 | 0 | 2,421 ms across all responses |
| Insights, cold known addresses | 4 | 16 / 16 | 10.32 | 455 ms |
| Insights, one cold burst | 8 | 4 / 8 | 8.10 | 493 ms |
| Insights, repeated addresses | 4 | 4,732 / 4,732 | 943.68 | 8.82 ms |

All 16 generated candidates returned HTTP 503. Their on-chain history is unknown. The 40-way age burst and the eight-way Insights burst returned eight and four HTTP 503s, respectively. The repeated Insights run recorded 4,724 mixed cache responses and eight misses. No measured request exceeded three seconds across the 84,188 local requests. These short runs do not establish a latency guarantee or sustained Railway capacity.

## Workload and reproduction

The server and load generator were separate processes on the same macOS host with 10 logical CPUs, Python 3.13.7 and aiohttp 3.14.5. Each phase used authenticated POST requests over loopback with persistent connections and normal access logging. Each worker sent its next request when its previous response completed. The measured time includes reading and decoding the response and draining outstanding requests in each phase.

For the first run only, a temporary config raised the per-client quota to 100,000 per minute. Eight known addresses were seeded from prior verified epoch measurements in a temporary disk database. Each cached concurrency phase lasted eight seconds. Cold phases used distinct known addresses from the [existing corpus](../addresses.json), a fresh application cache for each tested key and real configured feeds. Earlier results held by third parties could still affect timings. The independent burst run used a fresh empty cache, the shipped quota and 40 known-used addresses followed by 16 generated candidates. Local test databases and access logs were discarded at shutdown.

Run from the repository root with the V2 environment installed. The local tests consume provider quota. For the optional Railway run, supply one valid staging `CLIENT_API_KEY` through the environment, a local ignored `.env` file, or the hidden prompt. The Railway workload is capped at 46 requests with up to four in flight. It stops on authentication or rate-limit errors.

```bash
v2/.venv/bin/python benchmarks/run_http_load.py local /tmp/wallet-http-local.json
v2/.venv/bin/python benchmarks/run_http_load.py local-burst /tmp/wallet-http-burst.json
v2/.venv/bin/python benchmarks/run_http_load.py railway /tmp/wallet-http-railway.json
```

[Load generator](../run_http_load.py) · [Local timing and status distributions](local.json) · [Burst and quota measurements](burst.json)
