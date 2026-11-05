# Dependencies, providers and API keys

## Local software dependencies

The runtime and unit tests use **Python 3.10+ and the standard library only**.
There are no required pip packages, Redis, database server, Node.js build,
frontend or third-party SDK. SQLite is accessed through Python's `sqlite3` module;
use a supported Python distribution with a current SQLite library and working
HTTPS certificate trust.

The optional memory benchmark scripts use Unix's standard-library `resource`
module and were measured on macOS. Time-zone reporting uses standard-library
`zoneinfo` plus the operating system's time-zone data. These scripts are not
required to serve requests. `jsonschema` was used for an additional development
validation, but is not a runtime or unit-test dependency.

## Provider roles

| Provider | Role | Credential / entitlement | Fallback behavior |
| --- | --- | --- | --- |
| Alchemy | Ethereum RPC snapshots and proof of discovered age events; ascending Transfers API when configured for age | Ethereum RPC URL or API key; Transfers availability follows the provider account | Optional; RPC falls back to QuickNode and public sources |
| QuickNode | Ethereum RPC snapshots and age-event verification | Full RPC URL, optionally `x-token` | Optional; RPC falls back to public sources |
| QuickNode indexed history | Optional age-discovery job | Token and NFT API v2 add-on, plus explicit history flag | Standard paid RPC does not imply the add-on is enabled; Insights does not need it |
| PublicNode, 1RPC, BlockPI | Keyless Ethereum RPC fallbacks and event verification | None | Can rate-limit, lag, reject methods, or become unavailable |
| Routescan and Blockscout | Indexed first-observation and contract-creation discovery | Current adapters use keyless access | Missing or limited history never proves universal absence |
| BlockCypher | Age observation from a provably complete small history | Current adapter is keyless | Truncated newest-first history is not accepted as a first-appearance result |
| AnChain.AI | Ethereum attribution and OSINT labels for Insights | AnChain Data API credential | No ordinary RPC substitute exists for these labels; absence of a credential produces `not_configured` |

The public RPC defaults are `https://ethereum-rpc.publicnode.com`,
`https://public.1rpc.io/eth`, and
`https://ethereum.public.blockpi.network/v1/rpc/public`. Availability and quotas
are controlled by their operators. Public access is not a service-level guarantee.
No cost or throughput entitlement is bundled with this repository.

AnChain calls use `GET https://api.anchainai.com/api/intel/address/label` with
`proto=eth`, the address query parameter and an `x-api-key` header. The adapter
restricts credentials to that official HTTPS origin and rejects authenticated
redirects. Successful attribution, `unaffiliated`, empty labels, and OSINT
associations are not automatically interpreted as safe or malicious verdicts.

## Configuration matrix

All values below are names or placeholders. Never put a real credential in a
URL example, test fixture, Git commit, issue, benchmark artifact or log.

| Setting | Required? | Scope and precedence |
| --- | --- | --- |
| `ALCHEMY_RPC_URL` | Optional | Full Ethereum HTTPS RPC URL; takes precedence over `ALCHEMY_API_KEY` |
| `ALCHEMY_API_KEY` | Optional | Constructs the Ethereum Alchemy RPC URL when no full URL is set |
| `QUICKNODE_RPC_URL` | Optional | Full Ethereum HTTPS RPC URL used by both services when exported |
| `QUICKNODE_ETH_URL` | Optional | Insights-only alternate name; used when `QUICKNODE_RPC_URL` is absent |
| `QUICKNODE_ETH_AUTH_TOKEN` | Optional | Insights-only `x-token`, bound to the configured QuickNode endpoint |
| `ANCHAIN_DATA_API_BASE_URL` | Optional | Defaults to `https://api.anchainai.com`; only that HTTPS origin is accepted |
| `ANCHAIN_DATA_API_KEY` | Required for AnChain labels | Takes precedence over `ANCHAIN_API_KEY` |
| `ANCHAIN_API_KEY` | Required alternative for labels | Alias when the preceding key is absent |
| `WALLET_AGE_QUICKNODE_HISTORY` | Optional | Export `1` to enable the age-only QuickNode indexed-history add-on |
| `WALLET_AGE_DB` | Optional | Export to change the age database, or use server `--cache-db` |
| `WALLET_INSIGHTS_DB` | Optional | Insights cache path, or server `--insights-cache-db` |
| `WALLET_CHECK_API_KEY` | Optional | Export to require local clients' `x-api-key`; this is separate from the upstream AnChain credential |

Without paid RPC keys, both systems can use public RPC. Without an AnChain key,
Insights can still return balances but its labels remain null and
`riskLabels.status` is `not_configured`. This is not a complete risk response.

## Portable setup

From the repository root, the keyless mode requires no configuration file:

```bash
python3 -m wallet_insights --port 8787
```

For Insights provider credentials, create a private local file from the example:

```bash
cp .env.example .env
# Edit .env: replace placeholders with your values or comment out unused settings.
python3 -m wallet_insights --port 8787 --env-file .env
```

The explicit file loader only reads relevant Insights settings, never evaluates
shell code, and does not modify the process environment. **`--env-file` configures
WalletInsights only.** Shell values take precedence over matching file names.
The one-shot Insights CLI accepts the same flag:

```bash
python3 check_wallet_insights.py ETH \
  0xd8da6bf26964af9d7eed9e03e53415d37aa96045 --env-file .env
```

For paid providers in **walletAgeDays**, export `ALCHEMY_RPC_URL` or
`ALCHEMY_API_KEY`, `QUICKNODE_RPC_URL`, and any age history flag through your shell
or secret manager before starting the process. The age factory does not read the
Insights file or its `QUICKNODE_ETH_URL`/`x-token` aliases. Export incoming
`WALLET_CHECK_API_KEY` and `WALLET_AGE_DB` through the shell as well.

Do not use `source .env` merely to load this application's configuration: the
application file parser deliberately does not execute file contents. Follow your
secret manager's integration for exported secrets. `.env`, private variants and
runtime databases are excluded by `.gitignore`; `.env.example` contains only
placeholders.

## Operational boundaries

The default server binds `127.0.0.1`. Configure an existing authenticated reverse
proxy and HTTPS termination for remote deployment. Keep the local API credential
distinct from provider credentials. Caches store public address evidence, not API
keys or private endpoint URLs.

Provider HTTP budgets share a 2.6-second application deadline; failures yield
partial or unknown data. Retry or review unresolved withdrawals according to the
client's policy. Multi-provider hedging can issue more than one RPC attempt even
when the primary eventually succeeds. See the request counts in the benchmark
report before estimating provider cost; one batch can contain multiple billable
RPC methods.
