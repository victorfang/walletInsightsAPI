## Quick start

In Bash or Zsh, run this once and paste your assigned test key at the prompt. The key stays hidden as you type. Run either curl command below in the same terminal.

```bash
{ printf 'Test API key: '; IFS= read -rs CLIENT_API_KEY; printf '\n'; }
```

### Check wallet age

```bash
curl -sS --max-time 10 --write-out '\nHTTP %{http_code}\n' \
  'https://walletinsightsapi-production.up.railway.app/walletAgeDays' \
  -H "x-api-key: $CLIENT_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"blockchain":"ETH","address":"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"}'
```

Example output. The first line is the response body in full days. Curl prints the HTTP status on the second line. The age increases over time.

```text
4058
HTTP 200
```

### Check wallet insights

```bash
curl -sS --max-time 10 --write-out '\nHTTP %{http_code}\n' \
  'https://walletinsightsapi-production.up.railway.app/walletInsights' \
  -H "x-api-key: $CLIENT_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"blockchain":"ETH","address":"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"}'
```

Example HTTP 200 response, showing selected JSON fields for this address. Balances, nonce and labels can change between requests.

```json
{
  "status": "complete",
  "address": "0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
  "balances": {
    "native": {"symbol": "ETH", "decimals": 18, "balanceRaw": "5780441042197210512"}
  },
  "accountNonce": "5970",
  "components": {"snapshot": "complete", "riskLabels": "available"}
}
```

If `status` is `partial`, check `components` to see which data is available. [Full walletInsights response reference](https://walletinsightsapi-production.up.railway.app/docs#tag/wallet-insights/POST/walletInsights)

| HTTP status | JSON body | What to do |
| --- | --- | --- |
| 400 | `"invalid"` or `"invalid request"` | Send exactly `blockchain` and `address`. An ETH address starts with `0x` followed by 40 hex digits. |
| 401 | `"unauthorized"` | Enter your assigned test key again and retry. |
| 404 | `"not_found"` | For an age lookup, record the age as unknown. |
| 429 | `"rate limited"` | Wait the number of seconds in the `Retry-After` response header before retrying. |
| 503 | `"unknown"` or an unavailable result | Record the result as unavailable and retry later. |

Read the HTTP status separately from the body. An HTTP 200 age response with body `404` means 404 days old. [Zero days and unknown age](https://walletinsightsapi-production.up.railway.app/docs#description/zero-days-and-unknown-age)

For support, include the HTTP status and `x-railway-request-id`. Add `-i` to curl to see the response headers.
