# Test walletAgeDays on staging

Use the API key AnChain supplied to your team. Send `blockchain` and `address` to get the number of full days since the earliest verified activity we observed for that address on Ethereum mainnet.

**Run one request.** Paste this block into Bash or Zsh. At the prompt, enter your assigned key and press Return. The key stays hidden as you type.

```bash
{
  printf 'Staging API key: '
  IFS= read -rs CLIENT_API_KEY
  printf '\n'
  curl -sS --max-time 10 --write-out '\nHTTP %{http_code}\n' \
    'https://walletinsightsapi-production.up.railway.app/walletAgeDays' \
    -H "x-api-key: $CLIENT_API_KEY" \
    -H 'Content-Type: application/json' \
    --data '{"blockchain":"ETH","address":"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"}'
}
```

**Read the result.** The example wallet returned the following in staging. Its age increases over time. The second line is the status printed by curl.

```text
4057
HTTP 200
```

The response body is a single JSON integer. `0` means the earliest verified observation was less than 24 hours ago. The age is a lower bound because earlier activity may exist.

| Status | What to do |
| --- | --- |
| 400 | Send exactly the two fields shown above. An ETH address starts with `0x` followed by 40 hex digits. |
| 401 | Check that the `x-api-key` header contains your assigned staging key. |
| 404 or 503 | Treat the age as unknown. Retry later for a 503. |
| 429 | Wait the number of seconds in the `Retry-After` response header before retrying. |

For support, include the HTTP status and `x-railway-request-id`. Add `-i` to curl to see response headers. [Service health](https://walletinsightsapi-production.up.railway.app/health) · [OpenAPI specification](https://walletinsightsapi-production.up.railway.app/openapi.json)
