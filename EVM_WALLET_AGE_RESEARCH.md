# EVM wallet age: scope and API recommendation

Research date: November 5, 2026, America/Chicago. This is a proposed extension; the deployed playground still searches Ethereum mainnet. Its current EVM aliases do not constitute multichain support.

## Recommended initial scope

Launch with seven explicitly supported mainnets, selected for indexed-history availability and compatibility with the existing providers. This is an engineering recommendation, not a measured market-share ranking or coverage of most EVM networks by count.

| Selector | Network | Chain ID |
| --- | --- | --- |
| `eth` | Ethereum mainnet | 1 |
| `base` | Base | 8453 |
| `arbitrum` | Arbitrum One | 42161 |
| `optimism` | OP Mainnet | 10 |
| `polygon` | Polygon PoS | 137 |
| `bsc` | BNB Smart Chain | 56 |
| `avalanche` | Avalanche C-Chain | 43114 |

Alchemy exposes RPC networks for these chains, and indexed Transfers support is documented beyond its original Ethereum/L2 scope. Availability for a particular account must be probed rather than inferred from a paid subscription. [Alchemy RPC networks](https://www.alchemy.com/docs/reference/node-supported-chains), [BSC API overview](https://www.alchemy.com/docs/bnb-smart-chain/bnb-smart-chain-api-overview), [Avalanche RPC directory](https://www.alchemy.com/rpc/avalanche).

Next candidates are Linea, Scroll, Gnosis, HyperEVM and Sonic, driven by actual withdrawal destinations. Fantom/Sonic historical continuity and network migrations need explicit treatment. Exclude testnets. BTC, Solana and TRON need separate address/history adapters and must not enter this same address namespace.

## API contract

Use **EVM as a search scope on the existing metric**, rather than naming a route `/EVM`:

```http
GET /walletAgeDays?blockchain=evm&address=<0x-address>
x-api-key: <service key>
```

Keep successful `/walletAgeDays` responses as bare integers. `blockchain=eth` should mean Ethereum only, `polygon` Polygon only, and `evm` the published seven-chain set. This deliberately replaces the existing documented Ethereum-only family-alias convention and requires release notes.

Add a small detailed companion `/walletAge` with the same two inputs. Proposed response fields are `ageDays`, `firstSeenAt` (epoch seconds), `firstSeenBlockchain`, `firstSeenChainId`, `firstSeenTransactionHash`, `coverageVersion`, `coverageStatus` and a compact list of chain outcomes. This allows clients to distinguish completed scope from missing providers without adding balance/risk calls.

```json
{
  "blockchain": "evm",
  "address": "<0x-address>",
  "ageDays": 2191,
  "firstSeenAt": 1604534400,
  "firstSeenBlockchain": "polygon",
  "firstSeenChainId": 137,
  "quality": "lower_bound",
  "coverageVersion": "evm-mainnet-7-v1",
  "coverageStatus": "partial",
  "chainsCompleted": ["eth", "polygon", "arbitrum", "optimism", "bsc", "avalanche"],
  "chainsUnavailable": ["base"]
}
```

The example is hypothetical at November 5, 2026 00:00 UTC, not a live wallet result. A production object would also include the actual verified transaction hash. `complete` should mean the configured lookup plan finished for all requested chains; it must not imply exhaustive coverage of every possible event or EVM network.

For strict integer clients, return an integer only when the required scope has completed or remaining chains have been safely ruled out by an audited time bound. An incomplete search returns `503 "unknown"`; the detailed endpoint may still return a verified lower-bound age with `coverageStatus:partial`. If all completed scoped histories are empty, return `404 "not_found"`. If there are no verified events and any source/chain is unavailable, return 503. Never replace those states with zero. A verified event less than 24 hours old returns zero days.

## What the age means

```text
firstSeenEvm = min(verifiedFirstSeenEpoch for supported chains)
ageDays = floor((nowEpoch - firstSeenEvm) / 86400)
```

The earliest timestamp produces the **largest** per-chain age; taking the smallest age would be a bug. The claim is earliest observed address activity in the stated histories, not key creation or proof of no older activity.

The same private key produces the same EOA address on networks using Ethereum address derivation. Contract accounts are controlled by code; identical contract addresses across networks do not establish identical deployment, state or owners. Consequently, an address-history aggregate is useful but does not establish the age of the current user or their ownership. [Ethereum accounts](https://ethereum.org/developers/docs/accounts/), [CREATE2](https://eips.ethereum.org/EIPS/eip-1014).

For withdrawal review, old incoming dust or token spam alone is weaker evidence of active control than an old signed outgoing transaction. Buying or compromising an aged key also defeats any assumption that age equals trust. Keep age as one signal alongside the existing Insights metrics.

## Provider strategy and actual costs

| Role | Provider | Practical constraint |
| --- | --- | --- |
| Indexed discovery | Existing Alchemy Transfers | Ascending, one candidate per direction; transfer categories do not cover every appearance |
| Multichain accelerator | Alchemy Transfers By Wallet Beta | Up to five networks per call; explicit partial failures; evaluate cost/latency before making it the default |
| Independent index fallback | Etherscan V2 | New key; plan/chain entitlement varies, so seven-chain free access cannot be promised |
| Independent index fallback | Blockscout Universal API | New key/routes; enable only networks actually covered |
| Additional public index | Routescan | Useful for listed chains, particularly Avalanche; not a universal fallback for all seven |
| Canonical evidence verification | Alchemy, configured QuickNode, public RPC | Receipt, matching chain ID/block and chain-specific finality; standard RPC is not an address-history index |

Alchemy's new endpoint launched November 4, 2026. It can merge both directions in timestamp order for up to five networks; seven chains require at least two calls. It can return HTTP 200 with `error.partialErrors`, so an HTTP success alone is insufficient. Its beta contract can change. [Alchemy launch](https://www.alchemy.com/blog/introducing-the-multichain-transfers-api), [endpoint contract](https://www.alchemy.com/docs/data/portfolio-apis/portfolio-api-endpoints/portfolio-api-endpoints/get-transfers-by-address).

Current listed discovery costs, before verification, retries and extra event categories:

| Strategy across seven networks | Listed CUs |
| --- | --- |
| Legacy Transfers: incoming + outgoing, 120 CU each | 1,680 |
| New multichain Transfers: 480 CU per requested network | 3,360 |

Fewer HTTP calls do not mean lower provider charges. These are listed compute units, not a dollar quote or an account-specific invoice. [Alchemy CU costs](https://www.alchemy.com/docs/reference/compute-unit-costs).

Transfer categories have gaps: zero-value internal calls and delegate calls are omitted; internal transfer availability differs by network. Use a normal-transaction/creation index where available to improve coverage rather than describing transfer-only history as every blockchain appearance. [Alchemy transfer coverage](https://www.alchemy.com/docs/reference/transfers-api-quickstart).

QuickNode paid RPC does not automatically include indexed address history. Its v2 address-history method requires an add-on and has pagination limits without a documented oldest-first option. Multichain endpoint routing also needs account configuration; do not derive other-chain endpoints from the existing Ethereum URL without checking entitlement. [QuickNode indexed history](https://www.quicknode.com/docs/ethereum/qn_getTransactionsByAddress_v2), [multichain setup](https://www.quicknode.com/guides/ai/evm-mcp-server).

Etherscan supports a unified `chainid` request and ascending normal transactions, but free access covers selected networks. [Supported networks/plans](https://docs.etherscan.io/supported-chains), [oldest normal transaction query](https://docs.etherscan.io/api-reference/endpoint/txlist).

Blockscout's current Universal API uses a shared endpoint plus `chain_id` and a new key. Its per-instance APIs are deprecated, with v12 rollout starting October 7, 2026. The current free plan can help, but its supported-chain directory must be checked. [Migration notice](https://docs.blockscout.com/devs/apis), [Universal API](https://docs.blockscout.com/devs/blockscout-api). Routescan coverage must likewise follow its actual chain directory. [Routescan chains](https://routescan.io/chains).

Three independent **history** sources per chain are not established by listing Alchemy, QuickNode and public nodes. Keep independent discovery and verification capabilities separate in configuration. Additional free index keys are useful; no new paid plan is necessary to conduct the initial implementation and failure tests, but complete redundant coverage may require one.

## Cache and latency design

Retain canonical per-chain positive entries under `(eip155:<chainId>, normalizedAddress)` and update each with `MIN(epoch)`. Preserve the one-million-record LRU cap across the age store; seven chain entries may represent one address. Store proof provenance alongside observations or in a bounded associated record so the winning chain/hash can be returned.

Derive an aggregate from those entries under an explicit chain-set/source-plan version. A finalized event time does not expire, but knowledge of the earliest event can improve. Never permanently freeze aggregate completeness after one responding chain. Empty/failed-chain checks need separate short freshness/retry metadata; a missing address can become active immediately. When a chain is added, retain existing chain epochs and check the newly uncovered chain. Old `evm:1:v1` rows can seed Ethereum only; they must not mark seven-chain coverage complete.

Use one global provider-work deadline, bounded concurrency, delayed fallbacks and batched receipt/block checks. Do not replicate the current Ethereum fanout seven times: Cloudflare documents six simultaneous outgoing connections waiting on headers; excess calls queue. Hosting-plan subrequest entitlements must also be respected. The three-second target requires returning honest partial/unknown responses during outages. [Worker limits](https://developers.cloudflare.com/workers/platform/limits/).

An optional optimization can skip a chain whose audited earliest possible timestamp is later than an already verified event. Record this as excluded by a time bound, not as queried. Do not substitute marketing launch dates, placeholder genesis timestamps or assumptions about migrated histories for an audited lower boundary.

## Live capability probe using the existing paid key

The read-only probe used one public address, `0xd8da6bf26964af9d7eed9e03e53415d37aa96045`, on November 5, 2026, America/Chicago. Its [redacted raw results](benchmarks/evm-age-provider-capabilities.json) contain no credentials or provider URLs. No additional keys, plans or purchases were needed.

All seven legacy Alchemy endpoints returned the expected chain ID and incoming/outgoing ascending transfer candidates. Separate checks found matching receipt/block hashes, transaction membership and finalized heights for the selected candidate on every chain. These checks use the same provider; they are not an independent index-completeness assessment or a guarantee of identical finality semantics across chains.

| Chain | History batch | Receipt/block check | Selected observed candidate date (UTC) |
| --- | --- | --- | --- |
| Ethereum | 659.4 ms | 452.5 ms | 2015-09-28 |
| Base | 619.5 ms | 355.1 ms | 2023-07-30 |
| Arbitrum One | 630.1 ms | 354.2 ms | 2021-11-27 |
| Optimism | 619.7 ms | 416.5 ms | 2021-12-17 |
| Polygon PoS | 619.3 ms | 460.0 ms | 2021-03-03 |
| BNB Smart Chain | 655.4 ms | 561.7 ms | 2021-05-02 |
| Avalanche C-Chain | 488.8 ms | 661.5 ms | 2022-01-21 |

Those are individual provider-batch measurements from separate discovery and verification stages, not a completed seven-chain service benchmark. The complete exercise made 23 HTTP attempts, including an initial parser retry for null metadata; the raw discovery run records nine requests and a 1185.8 ms wall time. Do not interpret the discovery wall time as the full verified API latency.

Two findings affect the implementation choice:

- Avalanche's legacy history returned null timestamp metadata despite `withMetadata:true`; the canonical block supplied the timestamp. Treat missing metadata as recoverable, not an empty history.
- The beta Portfolio request across ETH/Base/Polygon/Arbitrum/Optimism succeeded in 519.8 ms. The BNB+Avalanche request returned HTTP 200 in 513.8 ms with a valid BNB candidate **and an Avalanche partial error**. Legacy Avalanche history and RPC checks still succeeded. The beta wrapper therefore cannot be the sole discovery path for seven-chain coverage.

Based on the current key's observed behavior and the listed CU costs, use stable per-chain Transfers as the initial default with bounded batching/concurrency, and treat the beta merged endpoint as an optional accelerator. Add the independent index fallbacks per chain as their credentials and capabilities are configured.

## Implementation and acceptance criteria

1. Introduce a chain registry with aliases, IDs, index capabilities, RPC endpoints and finality policy. Make chain selectors literal and retain `evm` as the aggregate.
2. Refactor proof validation to require the requested chain, matching receipt/block and its finality rules. Fix the Python creation-verification parity issue: a successful receipt with null `contractAddress` must not establish deployment of the target address.
3. Add per-chain caches/provenance and separate coverage tracking. Reuse old ETH evidence only as ETH evidence. Derive versioned aggregate minima.
4. Add the detailed age endpoint and expose seven-chain coverage/outages in the playground. Keep WalletInsights separate and Ethereum-only until its own expansion is tested.
5. Reuse the 100-address benchmark, adding deterministic cross-chain fixtures and live cases whose oldest event is outside Ethereum. Measure completed-scope success separately from partial/unknown, and cold/warm p50/p95/max latency. A wrong or missing age is not made correct by meeting the deadline.
6. Test partial provider failures, all chains empty, age zero, older Polygon history, wrong-chain RPC, new chain/source versions, migration, LRU eviction, and recovery of an earlier epoch. Publish only verified coverage.

The current deployed Site is unchanged by this research document. The live capability probe supports the seven-chain implementation choice; it does not establish exhaustive history, independent finality, end-to-end latency or withdrawal-risk accuracy.
