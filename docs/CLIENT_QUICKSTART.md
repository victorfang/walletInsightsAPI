## Quick start

**Base URL** `https://walletinsightsapi-production.up.railway.app`

### Authentication

Send your key in the `x-api-key` header. In Bash or Zsh, enter it once at the hidden prompt. Run the examples in the same terminal.

```bash
{ printf 'Test API key: '; IFS= read -rs CLIENT_API_KEY; printf '\n'; }
```

### Request parameters

- `blockchain` — Use `ETH` for Ethereum mainnet.
- `address` — Withdrawal destination matching `^0x[0-9a-fA-F]{40}$`.
- **POST** — Send exactly these two fields in an `application/json` body, up to 1,024 bytes. Omit query parameters.
- **GET** — Send the same two fields as query parameters.

### Check wallet age

```bash
curl -sS --max-time 10 --write-out '\nHTTP %{http_code}\n' \
  'https://walletinsightsapi-production.up.railway.app/walletAgeDays' \
  -H "x-api-key: $CLIENT_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"blockchain":"ETH","address":"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"}'
```

Example output. The body is the age in full days. Curl appends the HTTP status.

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

Selected fields from a sample HTTP 200 body.

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

- Check `status` and `components`. For `partial`, use only available fields.
- Parse `balanceRaw` as an integer string. Display amount = `balanceRaw / 10**decimals`. A null balance is unknown. [Full response schema](https://walletinsightsapi-production.up.railway.app/docs#tag/wallet-insights/POST/walletInsights)

### Error handling

| HTTP | JSON body | Client action |
| --- | --- | --- |
| 400 | `"invalid"` or `"invalid request"` | Correct the address or request format. |
| 401 | `"unauthorized"` | Check the `x-api-key` header. |
| 404 | `"not_found"` | For an age lookup, record an unknown age. |
| 405 | `"method not allowed"` | Use GET or POST. |
| 429 | `"rate limited"` | Retry after the number of seconds in `Retry-After`. |
| 501 | `"coming soon contact info@anchain.ai"` | Use a supported chain selector. |
| 503 | `"unknown"` or an object with `status: "unavailable"` | Record unavailable data and retry later. |

- **Default rate limit** — 60 requests per client per rolling minute, shared across both endpoints.
- **Support** — Add `-i` to curl and include the HTTP status and `x-railway-request-id` when reporting an issue.
