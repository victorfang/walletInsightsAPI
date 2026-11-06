# Railway staging with Route 53 and automatic HTTPS

Recommended client URL: **`https://api-staging.anchainai.com`**. Keep the existing `api.anchainai.com` AWS records as they are. Public DNS checked November 6, 2026 shows AWS nameservers for `anchainai.com`; make staging records in the authoritative Route 53 public hosted zone.

This guide prepares deployment; no Railway resources or DNS records have been created by the code changes.

## 1. Create the Railway service

In Railway, create a project named `wallet-age-staging`, then add a service from the GitHub repository `victorfang/walletInsightsAPI`, branch `main`. Keep the source root at the **repository root**, not `/v2`: legacy compatibility imports the sibling packages.

The root [Dockerfile](../Dockerfile) installs the pinned dependency and starts aiohttp on `0.0.0.0` using Railway's `PORT`. Leave custom build and start commands empty. Railway detects a root Dockerfile automatically. [Dockerfile deployment documentation](https://docs.railway.com/builds/dockerfiles).

Use dashboard settings for this small deployment. Railway's current documentation deprecates `railway.toml`/`railway.json` configuration for new services, so this setup does not introduce those files. [Current configuration guidance](https://docs.railway.com/config-as-code).

## 2. Add variables and a persistent volume

Under service **Variables**, add these values from your existing local configuration:

| Variable | Value |
| --- | --- |
| `WALLET_CHECK_API_KEY` | A new staging key to share with the client |
| `ALCHEMY_API_KEY` | Your Alchemy key; alternatively use `ALCHEMY_RPC_URL` |
| `ETHERSCAN_API_KEY` | Your Etherscan key |
| `QUICKNODE_ETH_URL` | Your private Ethereum endpoint; `QUICKNODE_RPC_URL` also works |
| `QUICKNODE_ETH_AUTH_TOKEN` | Your QuickNode token, if required by that endpoint |

Generate the inbound staging key locally with `openssl rand -hex 32`, then put it in Railway Variables. Give clients that staging key, not your upstream provider credentials. Railway injects these settings into the process environment; no uploaded `.env` or `--env-file` option is needed. Paid credentials are optional, but configured paid feeds improve resilience over public feeds alone.

Attach **one persistent volume** to the service at:

```text
/app/v2/data
```

The Dockerfile keeps each cache in a separate file on that volume:

| Route/version | Persistent SQLite file |
| --- | --- |
| Latest/V2 age | `/app/v2/data/wallet-age.sqlite3` |
| Legacy V1 age | `/app/v2/data/wallet-age-v1.sqlite3` |
| Legacy Insights | `/app/v2/data/wallet-insights-v1.sqlite3` |

The V2 age limit stays 1,000,000 entries. Future V2 balance signals remain unimplemented; their planned 100k cache is not created. Volume capacity must leave space for SQLite WAL files and any legacy caches; the measured V2 million-entry database alone was about 151 MiB.

Use **one replica**. Volume-backed services currently cannot use replicas, and redeployments can briefly interrupt service. Never mount over `/app` or `/app/v2`, which would hide the application files. [Railway volume guidance](https://docs.railway.com/volumes).

Set the deployment healthcheck to **`/health`**; it returns JSON `"ok"` without an API key. This checks service readiness, not upstream history availability. [Railway healthchecks](https://docs.railway.com/deployments/healthchecks).

Keep **Serverless/App Sleeping disabled** for client latency tests: waking a sleeping service adds delay. Choose a region close to the client, then measure hosted latency there. [Railway Serverless](https://docs.railway.com/deployments/serverless).

Apply the pending changes and deploy. Under **Settings → Networking → Public Networking**, generate a Railway domain first and check its `/health`. The container already listens on the supplied `PORT`; use that port if the networking settings request a target port.

## 3. Add the staging domain

In Railway's service networking settings, add this **Custom Domain**:

```text
api-staging.anchainai.com
```

Railway displays the domain's DNS requirements. In AWS **Route 53 → Hosted zones → `anchainai.com`**, create:

| Record | Name | Value |
| --- | --- | --- |
| `CNAME` | `api-staging` | The exact Railway target hostname shown for this custom domain |
| `TXT` | The separate verification name Railway displays | Railway's exact ownership-verification value |

For the CNAME, use **Alias: off**, **Simple routing**, and an initial TTL of **300 seconds**. Its value is a hostname, without `https://` or a URL path. For verification names, omit the zone suffix if the Route 53 form already appends it. Do not guess the TXT label or copy the production API record. [AWS record types](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/ResourceRecordTypes.html), [Railway DNS requirements](https://docs.railway.com/integrations/api/manage-domains).

Wait for Railway to show the custom domain as verified. Both its requested CNAME and TXT must be present. An ordinary exact hostname is sufficient; no wildcard domain is needed.

Railway provisions and renews the HTTPS certificate. There are no certificate files, Certbot jobs, ACM requests or additional load balancers to maintain for this setup. Keep the validation records in DNS. [Railway domain and HTTPS documentation](https://docs.railway.com/networking/domains/working-with-domains).

The record values are generated by your Railway service; they cannot be filled in before that service/domain exists. Do not change your domain's nameservers or existing production API records.

## 4. Give the client a test call

```bash
curl -i 'https://api-staging.anchainai.com/health'

curl -i --get 'https://api-staging.anchainai.com/walletAgeDays' \
  --data-urlencode 'blockchain=ETH' \
  --data-urlencode 'address=0xd8da6bf26964af9d7eed9e03e53415d37aa96045' \
  -H 'x-api-key: YOUR_STAGING_KEY'
```

Success is a bare JSON integer. `X-API-Version: v2` identifies the selected release; repeating a successful call should show `X-Wallet-Age-Cache: hit`. The latest route follows the newest compatible version. Clients can explicitly use `/v2/walletAgeDays` or `/v1/walletAgeDays` to pin behavior. Unknown history remains an error, not age zero.

After a successful check, redeploy once and repeat it to confirm the same volume retained the cache. Hosted client latency includes networking and TLS; the local benchmark is not a Railway latency guarantee.

## If the exact production hostname is required

DNS selects hosts, not URL paths. A CNAME change for `api.anchainai.com` would move traffic for that hostname, including existing API routes. For `api.anchainai.com/staging/...` or a single new route alongside the AWS API, configure routing in the existing AWS HTTP front door to a Railway origin. The exact setup depends on whether AWS currently uses API Gateway, CloudFront or another proxy. A dedicated staging hostname avoids that extra routing work. [CloudFront path/origin routing](https://docs.aws.amazon.com/cloudfront/latest/APIReference/API_CacheBehavior.html).

## Local container check (optional)

With Docker running, from the repository root:

```bash
docker build -t wallet-age-staging .
docker volume create wallet-age-staging-cache
docker run --rm -p 127.0.0.1:8787:8787 \
  -e PORT=8787 -e WALLET_CHECK_API_KEY=local-staging-test-key \
  -v wallet-age-staging-cache:/app/v2/data wallet-age-staging
```

The image includes application sources and public configuration, not local provider keys, databases, benchmarks or test artifacts. Put real hosting credentials in Railway Variables.
