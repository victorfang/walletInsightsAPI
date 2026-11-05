# walletAgeDays: semantics and EVM extension

The API returns completed days since the earliest on-chain appearance found for
an address in configured source histories. The two API fields are `blockchain`
and `address`; successful output is a JSON integer.

## Shared EVM address family

An externally owned account derives its address from its public key. The same
key/address can be used across EVM-compatible networks. Ethereum's own wallet
guide documents that reuse and separately cautions about smart contract wallets.
[Ethereum wallet guide](https://ethereum.org/guides/how-to-use-a-wallet),
[Ethereum accounts](https://ethereum.org/developers/docs/accounts/).

The aggregate is feasible as:

```text
first_seen_evm = minimum(first_seen_on_chain for each covered EVM chain)
age_days = floor((now_epoch_seconds - first_seen_evm) / 86400)
```

Histories on those chains are independent. If an address first appeared on
Polygon in 2021 and Ethereum in 2024, its family age uses 2021 only after that
Polygon event has actually been found. Shared keys do not let an Ethereum node
infer activity on Polygon.

The current selectors `eth`, `ethereum`, `eth-mainnet`, `evm`, `polygon`, `matic`,
`polygon-mainnet`, `matic-mainnet`, `base`, `arbitrum`/`arb`, `optimism`/`op`,
`bsc`/`bnb`, and `avalanche`/`avax` choose the same family. **Every selector
currently searches Ethereum mainnet only.** This is a documented family-selector convention, not
Polygon-specific history support. `X-Wallet-Age-Coverage: ethereum` states the
actual coverage. Non-EVM blockchains are not grouped merely because some
cryptographic keys can be reused.

Contracts are controlled by code and storage. Matching hexadecimal addresses on
different networks do not establish the same contract state, deployment date or
owner. This product measures activity of an address string across covered chains;
it does not identify a person or prove account ownership.
[Ethereum account types](https://ethereum.org/developers/docs/accounts/).

## Durable observations in an evictable cache

A finalized canonical event's timestamp is a durable positive fact. An indexer's
current earliest result can still be incomplete: it can recover older records,
the query can add activity categories, or the service can add chains.
[Ethereum proof of stake and finality](https://ethereum.org/developers/docs/consensus-mechanisms/pos/).

For each retained entry, the store keeps `MIN(epoch)` and production code saves
only finalized positive observations. A later observation never replaces an
earlier saved epoch while that entry remains cached.
Missing history, malformed addresses, failures, timeouts and provisional events
are not age-cache entries. An unobserved address can become active immediately
after a request.

Keeping a retained positive row without a TTL saves provider cost, but a cached partial result
does not automatically converge to the globally exact first appearance. A bare
integer cannot carry that nuance in its body. Coverage and basis headers plus
the documented source scope define it as age since **earliest observed activity
in covered histories**. Moralis also distinguishes first observed chain activity
from wallet creation.
[Moralis first-transaction explanation](https://api-help.moralis.io/en/articles/95777-how-to-get-the-first-transaction-of-a-wallet-address).

The current aggregate key is `(evm:1:v1, normalized_address)`. Adding Polygon,
Base, Arbitrum or other chains must change the coverage namespace or persist
explicit coverage tracking. Otherwise an ETH-only cached epoch could be reused
forever without discovering older activity on a newly supported chain. A larger
rollout can preserve per-chain observations and derive the minimum across its
explicit covered-chain set.

## Indexed sources and the latency target

Standard Ethereum JSON-RPC offers blocks, state, transactions and receipts, but
no universal address birthday. `eth_getTransactionCount` counts transactions sent
by an address; zero does not exclude incoming transfers or token activity.
[Ethereum JSON-RPC reference](https://ethereum.org/developers/docs/apis/json-rpc/).

| Source | Useful request | Constraint |
| --- | --- | --- |
| Alchemy | Ascending `alchemy_getAssetTransfers`, small `maxCount`, metadata; separate incoming and outgoing queries | Supported transfer categories, not every activity type |
| Routescan | Ascending indexed transaction and contract-creation queries | Keyless access is rate-limited |
| Blockscout | Independent indexed address histories | Explorer/network coverage can vary |
| BlockCypher | Minimum over a complete small returned history | Newest-first truncated pages do not establish oldest activity |
| QuickNode | Optional `qn_getTransactionsByAddress` | Token and NFT API v2 add-on; no documented ascending sort |
| Configured/public RPC | Receipt, chain ID, canonical block and finality checks | Verification of an event discovered elsewhere |

Alchemy supports ascending transfer ordering and incoming/outgoing filtering.
Its coverage excludes zero-value internal transfers, delegate calls and miner
rewards. A transfer-only timestamp is not proof of complete global history.
[Alchemy transfer method](https://www.alchemy.com/docs/data/transfers-api/transfers-endpoints/alchemy-get-asset-transfers),
[Alchemy transfer coverage](https://www.alchemy.com/docs/reference/transfers-api-quickstart).

QuickNode's documented parameters are `address`, `fromBlock`, `toBlock`, `page`
and `perPage`. It permits at most 100 records per page and pagination up to
100,000 results, with the indexed-history add-on required even for paid standard
RPC endpoints. No oldest-first option is documented. A minimum over a provably
complete returned history is usable; a first/latest page is not a birthday.
[QuickNode indexed transactions](https://www.quicknode.com/docs/ethereum/qn_getTransactionsByAddress_v2).

BlockCypher documents descending history and `before` pagination. Its complete
small histories can provide independent evidence; arbitrary full pagination is
incompatible with a short cold-request budget.
[BlockCypher Ethereum API](https://www.blockcypher.com/dev/ethereum/).
Routescan documents keyless access and Blockscout publishes its explorer API.
[Routescan getting started](https://docs.routescan.io/getting-started/get-started),
[Blockscout APIs](https://docs.blockscout.com/devs/apis).

A bounded worker pool races source jobs under a 2.6-second application budget.
The checker returns a finalized verified age or a clear error instead of scanning the
chain. A provider timeout is not an empty history. Completed lookups without a
usable observation return `not_found`; errors or incomplete lookup without an
epoch return `unknown`. Neither becomes integer zero. Provisional observations alone also return
`503 "unknown"`; successful responses require a finalized canonical event. Internet transit and
operating-system stalls remain outside the application budget.

## Future multichain accelerator

Alchemy's **Transfers By Wallet (Beta)** endpoint fans out across an explicit set
of 14 supported EVM networks and merges transfers by block timestamp. Inputs
include address, direction, networks, category, ordering and result limit. It is
a useful future discovery source, not coverage of every EVM chain or activity.
HTTP 200 can include `error.partialErrors`; a partial result does not establish
complete family coverage. The beta API may change.
[Alchemy multichain transfers](https://www.alchemy.com/docs/data/portfolio-apis/portfolio-api-endpoints/portfolio-api-endpoints/get-transfers-by-address?explorer=true).

A safe expansion adds explicitly configured chains with indexed sources and RPC
verification, changes the cache coverage namespace, and tests addresses whose
oldest event is on a non-Ethereum network. The shared-key premise makes address
aggregation useful; it does not remove the need to query those histories.

## One-million-entry storage policy

The compact SQLite `WITHOUT ROWID` table stores `(chain, address, epoch)` plus a
small metadata counter. It bounds each connection's page cache and keeps the
address set on disk.
[SQLite WITHOUT ROWID](https://www.sqlite.org/withoutrowid.html),
[SQLite cache settings](https://www.sqlite.org/pragma.html#pragma_cache_size).

Both active cache databases use persistent **least recently used (LRU)**
eviction, with a default hard limit of **1,000,000 entries per database**.
The age database stores one `(coverage namespace, address)` observation per row.
The Insights database stores snapshots and AnChain label results as separate
rows in one shared limit, so one address can use two entries.

Successful reads and writes refresh recency. At capacity, admitting a new entry
removes the least recently used entry; eviction is based on last access, not the
blockchain timestamp or insertion order. Sequence-based recency survives process
restarts and avoids timestamp ties or wall-clock changes. SQLite serializes
recency updates, eviction and admission across processes. Existing schemas
migrate automatically while preserving values; old entries with no recency
history use deterministic tie-breaking order.

Age epochs have no TTL while retained, but may be evicted. A later request for an
evicted address repeats source discovery and can incur provider cost again. This
is a bounded cache rather than an immutable archive. `MIN(epoch)` is preserved
for each retained entry; after eviction the next lookup uses observations
available from the current sources and coverage.

Insights still has separate freshness limits: 15 seconds for a complete snapshot
and 300 seconds for successful labels. Expired entries are removed before a
fresh retained entry is evicted. Reading updates recency without extending
`expiresAt` or changing `capturedAt`. Failed or unknown components are not stored.
LRU changes storage admission, not endpoint inputs, bodies or age arithmetic.

The [LRU million-entry benchmark](benchmarks/wallet-age-days-lru-million.json)
populated 1,000,000 rows in the new schema and checked real full-capacity eviction.
The least recently used row was replaced, a recently read row was retained, and
the row count stayed at one million. It measured 136.28 MiB on disk, 20.59 MiB
peak benchmark-process RSS, 1.07 ms median and 1.82 ms p95 reads including their
persistent recency update. A full-capacity insertion took 1.18 ms. The filesystem
cache was warm; RSS excludes OS page cache, and bulk insertion throughput is not
ordinary request throughput.

The [historical compact-store benchmark](benchmarks/wallet-age-days-million.json)
actually populated 1,000,000 records: 66.30 MiB on disk, 21.27 MiB peak process
RSS, 0.478 ms median and 0.708 ms p95 lookup. It predates the LRU recency index
and writes and used the old refusal-at-capacity policy. These are historical
measurements, not performance or size claims for the new LRU stores. Lookups
followed population with a warm OS filesystem cache; process RSS excludes OS
page cache, and bulk insertion throughput is not per-request `put_min()` throughput.

Earlier experiments used a richer SQLite evidence schema, measuring about
279 MiB on disk, and an epoch-only dictionary measuring about 294 MiB peak process
RSS. They are historical measurements, not figures for the rewritten compact
store or full service.
[Earlier SQLite experiment](benchmarks/age-store-million.json),
[Earlier dictionary experiment](benchmarks/age-dictionary-million.json).
