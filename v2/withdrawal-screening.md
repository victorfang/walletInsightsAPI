## Response times

- **Successful queries** — p95 target of about 1 second or less.
- **All requests** — Target response time under 3 seconds, including unavailable results. Network delays and load can increase client latency.

| Endpoint | Lookup | Average | p95 | Observed maximum |
| --- | --- | ---: | ---: | ---: |
| `/walletAgeDays` | Cached | 1 ms | 2 ms | 6 ms |
| `/walletAgeDays` | Uncached | 760 ms | 996 ms | 1,004 ms |
| `/walletInsights` | Fully cached | 3 ms | 4 ms | 10 ms |
| `/walletInsights` | Uncached | 690 ms | 807 ms | 884 ms |

Local HTTP measurements, November 5–6, 2026, one request at a time. Each age row includes 40 successful responses. Each Insights row includes 80 complete responses. Client-to-Railway network time is excluded.

**Concurrent unknown-age test** — 16 requests, eight in flight. All returned HTTP 503 with body `"unknown"`. Measured p95 was 2.42 seconds.

## Zero days and unknown age

Check the HTTP status before parsing the body. Only HTTP 200 returns an integer age. The result is a lower bound on Ethereum activity.

| HTTP | JSON body | Interpretation |
| --- | --- | --- |
| 200 | `404` | Earliest observed activity was 404 full days ago. |
| 404 | `"not_found"` | No Ethereum activity found for the address. An address awaiting its first transfer can return this. Age is unknown. |
| 200 | `0` | Earliest observed activity was less than 24 hours ago, e.g., six hours ago. |
| 503 | `"unknown"` | Age temporarily unavailable. Retry later. |

Apply a **less-than-three-days** rule only when HTTP status is 200 and the body is `0`, `1`, or `2`. Route unknown ages through your missing-data policy.

## Use Case - DeFi Anti Fraud

Screen the destination **before signing or releasing funds**. Your application enforces the hold or cap.

1. **Build the customer baseline.** Track approved destinations, median completed withdrawal amount over 30 days, and total paid or pending over 24 hours. Use one currency. Apply your new-customer limit when history is insufficient.
2. **Check the destination.** Call `/walletAgeDays`. For flagged requests, call `/walletInsights` with the same address.
3. **Record the decision.** Log the destination, amount, baseline, age or unknown status, available labels, and reason for review.

In 2019, Binance reported that one fraudulent withdrawal of 7,000 BTC passed its checks. The Ethereum thresholds below are illustrative. [Binance incident report](https://www.binance.com/en-AU/support/announcement/detail/360028031711)

### Case 1. Hold an unusually large withdrawal

- **Trigger** — First withdrawal to this destination, observed age `1` day, amount 80,000 USDC against a 2,000 USDC median (40×).
- **Action** — Hold before release. Route to the risk team for customer verification.

### Case 2. Cap repeated withdrawals to fresh addresses

- **Trigger** — Three 4,000 USDC requests in one hour to destinations aged `0`, `1`, and `2` days. The per-customer cap for these destinations is 10,000 USDC per rolling 24 hours.
- **Action** — Count paid and pending withdrawals together. After two requests, 2,000 USDC remains. Hold the third request. Reserve capacity atomically when approving concurrent requests.

### Case 3. Review an older wallet when the customer’s behavior changes

- **Trigger** — First withdrawal to a 600-day-old destination with substantial ETH. The requested amount is 10× the customer’s median after a credential change.
- **Action** — Hold for independent confirmation. Age and balance alone cannot establish ownership.
