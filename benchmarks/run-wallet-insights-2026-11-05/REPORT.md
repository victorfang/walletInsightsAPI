# WalletInsights: 100-address benchmark

Completed: 2026-11-05 14:42:34 CST (America/Chicago)

The same 100-input corpus is exercised through the production factory and real local `/WalletInsights` HTTP endpoint. Each address gets a fresh-cache cold lookup followed immediately by a warm lookup so a short snapshot TTL does not expire during a full corpus pass.

**Cold valid-address latency:** median **0.6854s**, p95 **0.8071s**, maximum **0.8842s**. **100/100 cold requests** finished within three seconds.

Among the 80 valid cold inputs: **80/80 complete on-chain snapshots**, **80/80 AnChain label lookups available**. Response speed, complete data availability, and reference agreement are separate measures.

## Uncached and overall latency

| Phase/cohort | Inputs | Mean s | Median s | p95 s | p99 s | Max s | ≤3s |
|---|---:|---:|---:|---:|---:|---:|---:|
| cold/overall | 100 | 0.5531 | 0.6651 | 0.7939 | 0.8424 | 0.8842 | 100/100 |
| cold/valid | 80 | 0.6901 | 0.6854 | 0.8071 | 0.8508 | 0.8842 | 80/80 |
| cold/known_used | 40 | 0.7075 | 0.6995 | 0.8357 | 0.8677 | 0.8842 | 40/40 |
| cold/complete_results | 80 | 0.6901 | 0.6854 | 0.8071 | 0.8508 | 0.8842 | 80/80 |
| cold/synthetic | 40 | 0.6726 | 0.6683 | 0.7585 | 0.7991 | 0.8070 | 40/40 |
| cold/malformed | 20 | 0.0054 | 0.0037 | 0.0113 | 0.0235 | 0.0265 | 20/20 |
| warm/overall | 100 | 0.0031 | 0.0031 | 0.0044 | 0.0090 | 0.0096 | 100/100 |
| warm/valid | 80 | 0.0034 | 0.0033 | 0.0044 | 0.0073 | 0.0096 | 80/80 |
| warm/known_used | 40 | 0.0035 | 0.0034 | 0.0047 | 0.0085 | 0.0096 | 40/40 |
| warm/complete_results | 80 | 0.0034 | 0.0033 | 0.0044 | 0.0073 | 0.0096 | 80/80 |
| warm/fully_cached | 80 | 0.0034 | 0.0033 | 0.0044 | 0.0073 | 0.0096 | 80/80 |
| warm/synthetic | 40 | 0.0033 | 0.0033 | 0.0043 | 0.0046 | 0.0047 | 40/40 |
| warm/malformed | 20 | 0.0019 | 0.0015 | 0.0028 | 0.0078 | 0.0090 | 20/20 |

Cold timing includes 80 format-valid addresses and 20 local malformed-address rejections. The known-used cohort has 40 controls; synthetic candidates also have legitimate current-state snapshots, even when all their balances are zero. Zero balance or nonce is not proof of never having appeared. The sample is not a production-load or attacker-detection evaluation.

## Component availability and outcomes

| Phase | HTTP outcomes | Result status | Complete snapshot | Labels available | Native balance | Nonce | Code profile |
|---|---|---|---:|---:|---:|---:|---:|
| cold | `{"200": 80, "400": 20}` | `{"complete": 80}` | 80/80 | 80/80 | 80/80 | 80/80 | 80/80 |
| warm | `{"200": 80, "400": 20}` | `{"complete": 80}` | 80/80 | 80/80 | 80/80 | 80/80 | 80/80 |

cold token balance availability: `{"DAI": 80, "USDC": 80, "USDT": 80}`.

warm token balance availability: `{"DAI": 80, "USDC": 80, "USDT": 80}`.


An available empty label list means the provider returned no labels for the queried address. It is not a low-risk or safe verdict. `unavailable` and `not_configured` preserve unknown data rather than silently returning an empty label list or zero balance. No sanctions, scam, exploit, or other attribution ground-truth corpus is provided here, so label accuracy is not claimed.

## Fresh-cache and warm-cache evidence

- Initial component-cache probe: `{"snapshot": false, "riskLabels": false}`.
- Cold valid inputs verified absent before lookup: **80/80**.
- Cold snapshot-cache hits: **0**; label-cache hits: **0**.
- Warm snapshot hits: **80/80**; label hits: **80/80**; both components: **80/80**.
- Warm lookup follows its cold counterpart immediately. Failed/unavailable components may be retried; a partial cache hit is not a fully cached response.
- The cache is isolated to this run; existing production/age caches are not cleared or modified. Upstream caches cannot be controlled.

## Independent exact-block RPC agreement

Independent reference queries cover **80/80** valid inputs at the exact returned block and block hash. **80** have complete reference data. Across native balance, nonce, code profile and configured token balances: **480 exact field matches**, **0 mismatches**, **0 unverified fields**.

The reference provider is distinct from the on-chain snapshot provider. Each reference batch verifies Ethereum chain ID and canonical block identity before comparing raw integer quantities; no floating-point balance comparison or latest-state approximation is used. These additional RPC requests occur after both timed phases and are excluded from endpoint latency. Missing/error data stays unverified. Reference agreement validates current snapshot values, not ownership or safety.

Reference providers used: `{"quicknode": 27, "tenderly_public": 53}`. Reference HTTP attempts, including failures: **134**.

The initial public-reference pass left 27 inputs unverified because Tenderly returned HTTP429 and LlamaRPC returned HTTP525. A separate supplemental pass verified **27/27** using **quicknode**, distinct from the Alchemy provider selected for every timed snapshot. The original pass is preserved in `reference-initial.json`; API timings and responses were not rerun or altered.

## Provider behavior and budget

| Provider | HTTP-client attempts | JSON returned | Errors | Locally expired |
|---|---:|---:|---|---:|
| alchemy | 160 | 160 | `{}` | 0 |
| anchain | 80 | 80 | `{}` | 0 |
| quicknode | 138 | 138 | `{}` | 0 |

Provider counts include calls locally rejected after the shared deadline and late source calls. They are whole-run totals; a call beginning during a later request is not assumed to belong to that request. HTTP JSON success is not necessarily usable component data.

The shared 2.6-second application budget leaves headroom under the three-second target. At timeout the API returns partial/unavailable fields rather than waiting for all providers. This does not bound arbitrary OS stalls, remote-client transit, or concurrent load.

Selected cold snapshot providers: `{"alchemy": 80}`. The **138 QuickNode HTTP-client attempts** in the timed run were healthy hedged work, not evidence that Alchemy had failed. Hedging adds paid-provider calls even when Alchemy ultimately supplies the selected snapshot; redundant work can continue briefly after the API has selected a complete result.

## Supplemental RPC failover controls

Completed: 2026-11-05 14:43:59 CST (America/Chicago).

These are separate direct production `service.check` controls using real RPC network calls. **AnChain labels were explicitly stubbed; no AnChain network request was made.** They are excluded from the 100 cold and 100 warm HTTP rows and are not end-to-end HTTP plus live-label latency measurements.

| Injected condition | Selected RPC | Direct service seconds | Snapshot | Cache unchanged after late work |
|---|---|---:|---|---|
| alchemy_failure | quicknode | 0.580 | complete | True |
| both_paid_failure | publicnode | 0.735 | complete | True |
| alchemy_slow_3s | quicknode | 1.159 | complete | True |

All three controls returned complete snapshots under three seconds. The slow-Alchemy control allowed late work to finish and verified that it did not replace the selected cached snapshot. Raw evidence: [fallback-controls.json](fallback-controls.json).

## Reproducibility and scope

- Implementation regression validation: **285 tests passed**.
- Dataset SHA-256: `72d1a2fa26ad76e467e771e60c448aae16b49d319d27243278bdf751fd46dee0`; 100 unique inputs even after lowercasing.
- Paid RPC providers configured: `["alchemy", "quicknode"]`; AnChain configured: **True**.
- Runtime source files unchanged during run: **True**.
- Malformed inputs correctly rejected: **20/20**; valid syntax accepted: **80/80**.
- API requests: **200**; concurrency:1; cold shuffle seed:20261105; pause:0.15s outside timing.
- API-loop wall time: **71.796s**; sum of timed HTTP requests: **55.626s**.
- Unfinished HTTP calls after bounded drain: **0**.
- Ethereum mainnet only. Mutable balances, nonces, code and labels are not shared across EVM-family aliases or cached forever.
- Current schema codeKind is descriptive: delegated EOAs and contract code do not independently imply danger.
- The historical existing_eoa corpus labels are not independently revalidated account-type claims.
- Raw data: `results.jsonl`, `metadata.json`, `summary.json`, `upstream-events.json`, and `reference-checks.json`.

```bash
python3 benchmarks/run_wallet_insights_benchmark.py --output benchmarks/run-wallet-insights-new --env-file .env
```

## All 100 uncached inputs

| ID | Address | Seconds | HTTP | Status | Native raw | Nonce | Code kind | Risk labels status |
|---|---|---:|---:|---|---:|---:|---|---|
| eth-001 | `"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"` | 0.6553 | 200 | complete | 5780441042197210512 | 5970 | delegated | available |
| eth-002 | `"0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"` | 0.6163 | 200 | complete | 5774551794456062094343 | 0 | contract_code | available |
| eth-003 | `"0xc917c3fa468f2c4b9c84c72caa46420eb9825249"` | 0.6920 | 200 | complete | 965686589638736356 | 192768 | empty_code | available |
| eth-004 | `"0x37c728c153eeb40ee907cb5b41b342d896878846"` | 0.6585 | 200 | complete | 632785088848139308 | 2436 | empty_code | available |
| eth-005 | `"0x35f7cff299f48b1b2a542224fd823152e437c654"` | 0.7254 | 200 | complete | 799966751059078 | 2083 | empty_code | available |
| eth-006 | `"0xccba2faab763603b2894a5f203bd335dc26d16b8"` | 0.6232 | 200 | complete | 4331898663745472 | 4 | delegated | available |
| eth-007 | `"0xbdc8f547b06f06d38a102217ca28ce7ed9340805"` | 0.7108 | 200 | complete | 60140951360603591 | 2204 | empty_code | available |
| eth-008 | `"0xb5e5ba15a5aa58df03d91549496d8cd67a15f858"` | 0.7425 | 200 | complete | 445051686915355010 | 7453 | delegated | available |
| eth-009 | `"0xd82c5660cac4169de051d7df34323f99c21176a7"` | 0.5269 | 200 | complete | 28214932316706821156 | 64088 | empty_code | available |
| eth-010 | `"0x4817a075bb59c1ed2d21e8240ff1561750175515"` | 0.6799 | 200 | complete | 9895938369944805129 | 25980 | empty_code | available |
| eth-011 | `"0x403e2b67f70ee3d10ec88c9b3d6de9eb1c7200d8"` | 0.6628 | 200 | complete | 27748984535641579 | 7410 | empty_code | available |
| eth-012 | `"0x91e33655188280afd5042a1fba723a731a117f1b"` | 0.6744 | 200 | complete | 10220567853490082 | 243 | empty_code | available |
| eth-013 | `"0xc066ac5d385419b1a8c43a0e146fa439837a8b8c"` | 0.7226 | 200 | complete | 7268040848064921816 | 861544 | empty_code | available |
| eth-014 | `"0x6d14296c706813c642045bc6102ee6e2fcaf8528"` | 0.7347 | 200 | complete | 102714065097147 | 98 | delegated | available |
| eth-015 | `"0x51649cf810ea9f1f61165044b7149070c67f79c8"` | 0.6438 | 200 | complete | 273977357875149551047 | 6438 | empty_code | available |
| eth-016 | `"0x07fcdbe8283f1b67cee5d5a4151cec8e8184301f"` | 0.6828 | 200 | complete | 103616036962862829 | 33 | empty_code | available |
| eth-017 | `"0xb42f812a44c22cc6b861478900401ee759ebead6"` | 0.6883 | 200 | complete | 5945696351996854609 | 860181 | empty_code | available |
| eth-018 | `"0x7a0e37f1543d0adb4e7ba7a27ea16b926b35a67c"` | 0.6635 | 200 | complete | 207017628578806 | 74 | empty_code | available |
| eth-019 | `"0xaa8ba7d4611437141192e7ceced531bc0a133efb"` | 0.6679 | 200 | complete | 95056457989327335424 | 1524257 | empty_code | available |
| eth-020 | `"0xd37ac934502619cee14908cef22138f4b98e06b4"` | 0.7213 | 200 | complete | 103987765583081894701 | 714092 | empty_code | available |
| eth-021 | `"0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"` | 0.6470 | 200 | complete | 2091253202443055346431004 | 1 | contract_code | available |
| eth-022 | `"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"` | 0.6991 | 200 | complete | 0 | 1 | contract_code | available |
| eth-023 | `"0xdac17f958d2ee523a2206206994597c13d831ec7"` | 0.7801 | 200 | complete | 42 | 1 | contract_code | available |
| eth-024 | `"0x6b175474e89094c44da98b954eedeac495271d0f"` | 0.7932 | 200 | complete | 0 | 1 | contract_code | available |
| eth-025 | `"0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"` | 0.7000 | 200 | complete | 0 | 1 | contract_code | available |
| eth-026 | `"0x514910771af9ca656af840dff83e8264ecf986ca"` | 0.7423 | 200 | complete | 0 | 1 | contract_code | available |
| eth-027 | `"0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"` | 0.6214 | 200 | complete | 0 | 1 | contract_code | available |
| eth-028 | `"0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"` | 0.6617 | 200 | complete | 0 | 1 | contract_code | available |
| eth-029 | `"0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"` | 0.6527 | 200 | complete | 0 | 1 | contract_code | available |
| eth-030 | `"0xc00e94cb662c3520282e6f5717214004a7f26888"` | 0.7302 | 200 | complete | 0 | 1 | contract_code | available |
| eth-031 | `"0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"` | 0.7141 | 200 | complete | 0 | 527071 | contract_code | available |
| eth-032 | `"0x7a250d5630b4cf539739df2c5dacb4c659f2488d"` | 0.8354 | 200 | complete | 25000000000000000 | 1 | contract_code | available |
| eth-033 | `"0x1f98431c8ad98523631ae4a59f267346ea31f984"` | 0.7576 | 200 | complete | 0 | 74511 | contract_code | available |
| eth-034 | `"0xe592427a0aece92de3edee1f18e0157c05861564"` | 0.6980 | 200 | complete | 0 | 1 | contract_code | available |
| eth-035 | `"0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"` | 0.8842 | 200 | complete | 0 | 1 | contract_code | available |
| eth-036 | `"0xc36442b4a4522e871399cd717abdd847ab11fe88"` | 0.7726 | 200 | complete | 0 | 1 | contract_code | available |
| eth-037 | `"0x000000000022d473030f116ddee9f6b43ac78ba3"` | 0.8420 | 200 | complete | 0 | 1 | contract_code | available |
| eth-038 | `"0x00000000000c2e074ec69a0dfb2997ba6c7d2e1e"` | 0.7369 | 200 | complete | 0 | 1 | contract_code | available |
| eth-039 | `"0x57f1887a8bf19b14fc0df6fd9b2acc9af147ea85"` | 0.8078 | 200 | complete | 0 | 1 | contract_code | available |
| eth-040 | `"0x00000000219ab540356cbb839cbe05303d7705fa"` | 0.7322 | 200 | complete | 93097607579580077399106838 | 1 | contract_code | available |
| eth-041 | `"0x941c9014c10ccf1f90228453b75f6c9f66ab3237"` | 0.6724 | 200 | complete | 0 | 0 | empty_code | available |
| eth-042 | `"0xd5ff6dcf5153245be026debac13e4516a91481a7"` | 0.7019 | 200 | complete | 0 | 0 | empty_code | available |
| eth-043 | `"0x6d8abcc3de765f917d8a0d61a25bfaf84015fab6"` | 0.5943 | 200 | complete | 0 | 0 | empty_code | available |
| eth-044 | `"0x1abdd483a6bedb4a6e8c56d7cc21a472bd322947"` | 0.7395 | 200 | complete | 0 | 0 | empty_code | available |
| eth-045 | `"0x6d2b789677686865ffe5f493e7737a3953763657"` | 0.6303 | 200 | complete | 0 | 0 | empty_code | available |
| eth-046 | `"0x68a16f5a188af63ff96b847319a37a18a42471d3"` | 0.6557 | 200 | complete | 0 | 0 | empty_code | available |
| eth-047 | `"0x0d9f7e0f240d000303b18efdf7cbd7c41049527e"` | 0.7234 | 200 | complete | 0 | 0 | empty_code | available |
| eth-048 | `"0xe4ae5f19b13d1b383cb6c950d0dc5c4206402b77"` | 0.6203 | 200 | complete | 0 | 0 | empty_code | available |
| eth-049 | `"0x0ec23ed32cc5d9663b4f7052548d3c37264efd4f"` | 0.5986 | 200 | complete | 0 | 0 | empty_code | available |
| eth-050 | `"0x55c30b5034ce3e77c6c64eb42946a112287b6a28"` | 0.6632 | 200 | complete | 0 | 0 | empty_code | available |
| eth-051 | `"0xc09cc5c8b959b53d56cab2c78adb00e469490fde"` | 0.6916 | 200 | complete | 0 | 0 | empty_code | available |
| eth-052 | `"0x34d2ded544ec43d3f76262f346326d9c73c637db"` | 0.6672 | 200 | complete | 0 | 0 | empty_code | available |
| eth-053 | `"0xa7a024d96066322cd9ef0eb90277263fe1922948"` | 0.6546 | 200 | complete | 0 | 0 | empty_code | available |
| eth-054 | `"0xb9e83f94ea3768822f32d529fe98ebf911f09f9f"` | 0.7174 | 200 | complete | 0 | 0 | empty_code | available |
| eth-055 | `"0xc45a2acbbdec947e5b16a5102098854d516e88cb"` | 0.8070 | 200 | complete | 0 | 0 | empty_code | available |
| eth-056 | `"0x91fc52dbdacecf60132a46a0f2e72015d37e9e6c"` | 0.5958 | 200 | complete | 0 | 0 | empty_code | available |
| eth-057 | `"0xe63a40cfee31a745de2761daf89905d1c823258a"` | 0.7868 | 200 | complete | 0 | 0 | empty_code | available |
| eth-058 | `"0xf3c0fb4452bc1ff845f23443f5e5143a649f186f"` | 0.6965 | 200 | complete | 0 | 0 | empty_code | available |
| eth-059 | `"0x4031d3cdd02f329e9af1b494e78270255b724ada"` | 0.6955 | 200 | complete | 0 | 0 | empty_code | available |
| eth-060 | `"0x15dd314a2763474e0d5768c82a88aa686c38f5ae"` | 0.6045 | 200 | complete | 0 | 0 | empty_code | available |
| eth-061 | `"0x81328784d16a252808fab1b17ca695151eb79729"` | 0.6025 | 200 | complete | 0 | 0 | empty_code | available |
| eth-062 | `"0x5325fa1c98d091c07d63aa31cfa6abe8b2cb8343"` | 0.6061 | 200 | complete | 0 | 0 | empty_code | available |
| eth-063 | `"0xc29cbe4dcc1d39b7d18c0c16a6cf43b47e38ba51"` | 0.6249 | 200 | complete | 0 | 0 | empty_code | available |
| eth-064 | `"0x2e0fe7a6e908ad05efe54e954a70c022cadd1467"` | 0.5938 | 200 | complete | 0 | 0 | empty_code | available |
| eth-065 | `"0xadc677dc2cdad427d17b921fd158fc454860bf5d"` | 0.7119 | 200 | complete | 0 | 0 | empty_code | available |
| eth-066 | `"0x0b2a274c002b9469cd112ee4842ecaa347e61cb7"` | 0.6879 | 200 | complete | 0 | 0 | empty_code | available |
| eth-067 | `"0xe3685a40fa74deba76da12d2a3b914cd677f7ed7"` | 0.6764 | 200 | complete | 0 | 0 | empty_code | available |
| eth-068 | `"0x8af22802dfd21cb8f21736d203a3a142b9edf1ff"` | 0.6693 | 200 | complete | 0 | 0 | empty_code | available |
| eth-069 | `"0x9a33fc9010949bdef4fce8285ce842174592fbc4"` | 0.6667 | 200 | complete | 0 | 0 | empty_code | available |
| eth-070 | `"0xe1993bd0dc5ba0e38fd4bd6e0907061e96ebaf99"` | 0.6829 | 200 | complete | 0 | 0 | empty_code | available |
| eth-071 | `"0x48577c94da33e60423f741022e4842d65f5e4954"` | 0.7239 | 200 | complete | 0 | 0 | empty_code | available |
| eth-072 | `"0x614870240de6379b49a00dd45f3c384939d36964"` | 0.6449 | 200 | complete | 0 | 0 | empty_code | available |
| eth-073 | `"0xdcb2afaf2cb6e4f35807e2c996af8f9857518062"` | 0.6219 | 200 | complete | 0 | 0 | empty_code | available |
| eth-074 | `"0xbbe35e12f8779e9162dc31fafe1b12f0a7b12c94"` | 0.7571 | 200 | complete | 0 | 0 | empty_code | available |
| eth-075 | `"0xf8fd1362b19ba5dc5ac2506d9c0e78ff4df6daf7"` | 0.6032 | 200 | complete | 0 | 0 | empty_code | available |
| eth-076 | `"0x084c2026aef4c3ba51736174a1d4f5272f516e90"` | 0.7352 | 200 | complete | 0 | 0 | empty_code | available |
| eth-077 | `"0xcac36747d1cd34199cab69721cb6163b384ffe33"` | 0.6541 | 200 | complete | 0 | 0 | empty_code | available |
| eth-078 | `"0x8acd8c981d5e97fb3a1c03afe34548fb3cc6e61d"` | 0.7352 | 200 | complete | 0 | 0 | empty_code | available |
| eth-079 | `"0xc931ebb8f72fb21d9cc1f72ac847396fcd3e0721"` | 0.7447 | 200 | complete | 0 | 0 | empty_code | available |
| eth-080 | `"0x5d736d69aaf0f48fbe1f4129e2b476991b0ef36d"` | 0.6461 | 200 | complete | 0 | 0 | empty_code | available |
| eth-081 | `""` | 0.0015 | 400 | invalid | — | — | — | — |
| eth-082 | `"0x"` | 0.0062 | 400 | invalid | — | — | — | — |
| eth-083 | `"0x1234"` | 0.0016 | 400 | invalid | — | — | — | — |
| eth-084 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0078 | 400 | invalid | — | — | — | — |
| eth-085 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0040 | 400 | invalid | — | — | — | — |
| eth-086 | `"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0015 | 400 | invalid | — | — | — | — |
| eth-087 | `"0Xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0047 | 400 | invalid | — | — | — | — |
| eth-088 | `"0xgggggggggggggggggggggggggggggggggggggggg"` | 0.0041 | 400 | invalid | — | — | — | — |
| eth-089 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaz"` | 0.0027 | 400 | invalid | — | — | — | — |
| eth-090 | `" 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0105 | 400 | invalid | — | — | — | — |
| eth-091 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "` | 0.0078 | 400 | invalid | — | — | — | — |
| eth-092 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"` | 0.0014 | 400 | invalid | — | — | — | — |
| eth-093 | `"\t0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0034 | 400 | invalid | — | — | — | — |
| eth-094 | `"0x\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21"` | 0.0016 | 400 | invalid | — | — | — | — |
| eth-095 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\ud83d\ude00"` | 0.0020 | 400 | invalid | — | — | — | — |
| eth-096 | `"0\u0445aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0031 | 400 | invalid | — | — | — | — |
| eth-097 | `"vitalik.eth"` | 0.0089 | 400 | invalid | — | — | — | — |
| eth-098 | `"https://etherscan.io/address/0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | 0.0058 | 400 | invalid | — | — | — | — |
| eth-099 | `"0xaaaaaaaaaaaaaaaaaaaa_aaaaaaaaaaaaaaaaaaa"` | 0.0265 | 400 | invalid | — | — | — | — |
| eth-100 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\u0000"` | 0.0023 | 400 | invalid | — | — | — | — |
