## Screen a DeFi withdrawal

Check the **destination address** before your service signs or releases a withdrawal. Send `blockchain: "ETH"` and the destination to `/walletAgeDays`. Wallet age measures time since the earliest observed activity on Ethereum mainnet. Earlier activity may exist.

Keep a withdrawal profile in your own application. For each customer, track previously approved destinations, the median amount of completed withdrawals over the past 30 days, and the total sent or pending in the past 24 hours. Compare amounts in the same currency. If the customer has too little history for a baseline, use your new-customer limit. For a flagged request, call `/walletInsights` on the same destination. Save the available labels and account details with the reason for review. Your service enforces the hold or cap.

### Zero days and unknown age

The HTTP status code and the JSON response body are separate. Check the status first. With HTTP 200, the body is an integer age in days, so a body of `404` means **404 days old**. With HTTP 404, the body is the string `"not_found"` and the age is unknown.

| HTTP status | JSON body | Example and action |
| --- | --- | --- |
| 200 | `404` | The earliest observed Ethereum activity was 404 full days ago. Use 404 as the wallet age. |
| 404 | `"not_found"` | No activity was found to date the address. For example, a customer may request a withdrawal to an address awaiting its first transfer. Record its age as unknown. |
| 200 | `0` | The first observed transfer was six hours ago. Less than one full day has elapsed. Count this destination as under three days old. |
| 503 | `"unknown"` | The lookup is temporarily unavailable, even for an active address. Record its age as unknown for this decision and retry later. |

Apply an **age under three days** rule only to HTTP 200 results of `0`, `1`, or `2`. Keep unknown ages separate. For example, if a customer requests an unusually large withdrawal to an unfamiliar destination with unknown age, hold it for review under your missing-data policy.

### Hold an unusually large withdrawal

A customer whose median withdrawal is 2,000 USDC requests 80,000 USDC to an address they have never used. `/walletAgeDays` returns `1`. The destination is new to the customer, has less than three days of observed activity, and the request is 40 times their usual amount. An example policy would block release pending review. Send the risk team the destination, age, amount, usual amount, and any available risk labels so they can verify the request with the customer.

### Cap repeated withdrawals to fresh addresses

Three requests for 4,000 USDC each arrive within an hour, all from one customer to different destinations with observed ages of `0`, `1`, and `2`. Suppose your policy caps withdrawals to these destinations at 10,000 USDC per customer in a rolling 24-hour period. If the first two requests are already paid or pending, only 2,000 USDC of capacity remains. Hold the third request for review. Count pending amounts when applying the cap so concurrent requests cannot each use the same allowance.

### Review an older wallet when the customer’s behavior changes

A destination is 600 days old and holds substantial ETH, but the customer has never paid it and has requested ten times their usual amount shortly after changing account credentials. Keep the withdrawal on hold for independent confirmation. Wallet age and balance alone cannot establish who controls the address. If `/walletInsights` returns `partial`, use the available fields and mark the rest unknown.

These thresholds are illustrative. Why check before release? In May 2019, Binance reported that attackers withdrew 7,000 BTC in a single transaction that passed its checks. That incident involved Bitcoin. The Ethereum examples above illustrate policies you can apply to your own withdrawals. [Binance incident report](https://www.binance.com/en-AU/support/announcement/detail/360028031711)
