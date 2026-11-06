# Concurrent age discovery and provider agreement

Implemented for the current Ethereum-only age coverage. This does not activate the proposed seven-chain EVM expansion. The integer response body and one-million-record permanent-epoch LRU remain unchanged.

## Credentials and provider roles

Use the explicit `--env-file .env` option for both endpoints. The parser reads supported keys without executing shell code; environment variables take precedence for the same names. Example RPC URL placeholders are ignored so a valid `ALCHEMY_API_KEY` or `QUICKNODE_ETH_URL` is not masked by copied example text.

- `ALCHEMY_API_KEY` or a real `ALCHEMY_RPC_URL`: ascending incoming/outgoing transfer history and Ethereum RPC verification.
- `ETHERSCAN_API_KEY`: V2 normal-transaction and creation history.
- `QUICKNODE_ETH_URL` or a real `QUICKNODE_RPC_URL`, plus optional `QUICKNODE_ETH_AUTH_TOKEN`: authenticated RPC evidence verification and Insights state. Indexed history still requires its separate optional add-on; a paid RPC endpoint alone does not imply it.
- Existing keyless Routescan, current legacy Blockscout and BlockCypher adapters remain available for history, subject to their documented scopes and limits. PublicNode, 1RPC and BlockPI remain RPC fallbacks.

Credentialed query URLs and headers are server-side only. Requests reject redirects so a key cannot be forwarded to a redirect destination. Errors and benchmarks omit configured URLs and secrets.

## A useful two-provider rule

On a cache miss, submit provider work concurrently under the existing shared deadline. A response counts toward early agreement only if it contains a validated positive observation whose receipt, canonical block, address involvement and finality checks passed.

Group jobs by independent history provider: Alchemy incoming and outgoing jobs are one group; two RPC URLs verifying the same indexed event are not two history providers. A group votes only after all of its jobs settle successfully. Empty histories, HTTP-200 plan errors, authentication failures, provisional events and unavailable results are not positive votes.

Return early when two groups agree on the **current minimum verified epoch**. Already observed earlier activity must not be ignored. When Alchemy is configured, wait for its incoming/outgoing group to settle before an agreement among normal-transaction indexes can return. This prevents two fast normal indexes from hiding an older token transfer still being fetched.

If providers disagree, keep collecting until completion or the shared deadline and return the earliest finalized canonical event found. A single verified event can still return the established `lower-bound` age, explicitly marked as single-source fallback. This keeps existing availability behavior. It is not a strict two-source security quorum: clients that require corroboration can inspect the evidence header and route other results to review. No verified event plus a failure is `503 "unknown"`; all scoped histories empty can be `404 "not_found"`. Neither is age zero.

Age zero remains a verified event less than 24 hours old. Matching providers establish corroboration within their indexed scopes, not proof of complete blockchain history. Normal transactions, token transfers, internal calls and contract creation can have different first observations.

## Evidence headers

`X-Wallet-Age-Evidence` reports one of:

| Value | Meaning |
| --- | --- |
| `corroborated` | Two fully settled independent history groups agree on the minimum verified epoch |
| `multiple-sources` | Multiple history providers supplied finalized observations; agreement requirements were not met |
| `single-source` | One history provider supplied a usable finalized observation |
| `cached-canonical` | The compact positive epoch cache supplied the result |

`X-Wallet-Age-Sources` counts distinct contributing positive history providers for a live result. A cache hit reports zero because the compact database deliberately stores the epoch rather than source-agreement evidence; zero here does not mean the event was unverified. Clients must not treat cached-canonical as fresh two-provider corroboration. The existing coverage, quality, cache and finalized headers remain in place.

Only the request owner writes the LRU entry. Abandoned provider work cannot write after the response. Already submitted atomic database work may finish independently, as documented for D1. Retained epochs use `MIN(epoch)` and no TTL; failures/empty histories are not permanent age entries. Return integer days by flooring `(now - epoch) / 86400`.

The hosted adapter also cancels delayed proof hedges as soon as a preferred proof succeeds, even if other history groups are still loading. This avoids starting unnecessary QuickNode/public verification calls after the event is already verified. Calls already sent may still be charged. Hosted D1 gets up to 350 ms for reads and 250 ms for writes within the unchanged total budget, reducing unnecessary paid refetches caused by an overly short cache wait.

## Live credential and scope probe

The sanitized [raw capability results](../benchmarks/new-provider-credentials.json) used one public Ethereum address and twelve read-only HTTP calls on November 5, 2026, America/Chicago. This checks credentials and capabilities; it is not a 100-address accuracy benchmark or the latency of the complete age API.

| Provider | Observed result | Individual measured call time |
| --- | --- | --- |
| Alchemy | Correct ETH chain and ascending history | 755.7 ms |
| Etherscan | ETH normal history | 520.8 ms |
| QuickNode with x-token | Correct chain and matching receipt | 375.7 ms |
| QuickNode | Canonical block/member/finality checks | 435.5 ms |
| Routescan | Matching normal-history event | 1163.8 ms |
| Current legacy Blockscout | Matching normal-history event | 639.0 ms |

The current Etherscan key returned usable history for Ethereum, Arbitrum and Polygon. Base, Optimism, BNB Smart Chain and Avalanche returned HTTP 200 with plan restrictions. These responses must never be parsed as empty history or success. No plan was upgraded or purchased.

The Polygon probe demonstrates why simple first-two-response logic is unsafe: Etherscan's first normal transaction was in July 2021, while an earlier Alchemy probe found an NFT transfer in March 2021. Both can be valid within their scopes. The broader history must not be discarded merely because two normal indexes answered sooner.

For a future EVM aggregate, apply coverage tracking per chain. Two Ethereum providers cannot establish the earliest activity across seven networks. The current implementation remains Ethereum-only and retains explicit coverage headers.

## Integrated validation and deployed smoke

The Python suite passes 326 tests; the hosted adapters/cache/HTTP suite passes 48 tests plus TypeScript checking and the production build. Regressions cover duplicated provider jobs, pending older incoming/NFT history, errors disguised as HTTP 200, disagreement, no verified event, cancellation, and an earlier concurrent cache write changing the returned timestamp. Additional regressions verify that a valid 250 ms cache read causes zero upstream calls, a 200 ms write completes without bypass, and successful proofs cancel delayed RPC hedges during an 850 ms history wait.

A live Python check using the current `.env` and an isolated SQLite database returned 4056 days for the public reference address in 1650.8 ms, with four positive providers and corroborated evidence. Its immediate cached repeat took 3.7 ms and correctly reported cached-canonical with zero retained source count. These are one-address in-process measurements, not HTTP percentiles.

The private deployed Site was also checked with actual provider secrets and persistent D1. Its [eight-request smoke record](../benchmarks/provider-agreement-site-smoke.json) includes two observed addresses, two format-valid candidates without sufficient evidence, repeated cache requests, and an Insights regression check:

| Request | HTTP | Browser-independent client round trip | Application time | Evidence |
| --- | --- | --- | --- | --- |
| Observed address A, cold miss | 200 | 2256.2 ms | 910 ms | Two positive providers; multiple-sources |
| Address A, cached repeat | 200 | 557.3 ms | 88 ms | Cached-canonical |
| Observed address B, cold miss | 200 | 1160.0 ms | 741 ms | Two positive providers; multiple-sources |
| Address B, cached repeat | 200 | 524.6 ms | 92 ms | Cached-canonical |
| Format-valid candidate, first/repeat | 503 | 1090.7 / 1364.5 ms | 577 / 614 ms | Unknown; not cached as an age |
| Synthetic candidate | 503 | 946.3 ms | 551 ms | Unknown; never age zero |
| Insights regression | 200 | 1007.7 ms | 527 ms | Complete snapshot and AnChain labels |

The deployed positive samples used fallback evidence rather than claiming two fully successful groups agreed. A count of two positive providers is therefore not synonymous with corroborated. Every measured round trip was below three seconds in this small smoke run; that does not establish a max-latency SLA or replace the historical 100-address benchmark. Application and network timings are shown separately.
