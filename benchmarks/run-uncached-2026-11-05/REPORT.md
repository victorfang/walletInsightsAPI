# walletAgeDays: 100 uncached inputs and overall results

Completed: 2026-11-05T20:08:39.800179Z

**Known-used age availability: 40/40.** Their uncached median was **1.3741 s**, p95 **1.8004 s**, maximum **1.9461 s**. Overall **100/100** requests finished within three seconds.

This is a new live run of the same 100 distinct inputs, with a fresh isolated application cache. It contains no warm or alias passes. Provider caches cannot be cleared by this benchmark. The real production factory and local HTTP endpoint return bare integer ages on success.

## Uncached latency

Fast errors and local invalid-input rejections are included in the overall figures. Successful-age latency and availability are shown separately so they cannot be masked by those responses.

| Cohort | Inputs | Integer ages | Mean s | Median s | p95 s | p99 s | Max s | ≤3 s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Overall | 100 | 40 | 0.6940 | 0.4722 | 1.7449 | 1.8652 | 1.9461 | 100/100 |
| All valid, uncached | 80 | 40 | 0.8660 | 0.8421 | 1.7528 | 1.8816 | 1.9461 | 80/80 |
| Known-used controls | 40 | 40 | 1.3207 | 1.3741 | 1.8004 | 1.9143 | 1.9461 | 40/40 |
| Successful age lookups | 40 | 40 | 1.3207 | 1.3741 | 1.8004 | 1.9143 | 1.9461 | 40/40 |
| Synthetic valid candidates | 40 | 0 | 0.4113 | 0.3447 | 0.6298 | 0.7980 | 0.8746 | 40/40 |
| Malformed inputs | 20 | 0 | 0.0060 | 0.0050 | 0.0141 | 0.0222 | 0.0242 | 20/20 |

## Overall outcomes and accuracy

| Cohort | HTTP outcomes |
|---|---|
| Overall | `{"200": 40, "400": 20, "503": 40}` |
| Known-used controls | `{"200": 40}` |
| Synthetic valid candidates | `{"503": 40}` |
| Malformed inputs | `{"400": 20}` |

- Syntax: 80/80 valid strings accepted; 20/20 malformed inputs correctly returned `400 "invalid"`. Malformed inputs caused 0 provider calls.
- Known-used age availability: 40/40; missing ages count against availability.
- Epoch-to-days arithmetic: 40/40 successful responses matched `floor((as_of - saved_epoch) / 86400)`. Arithmetic agreement does not establish that an epoch is globally earliest.
- Deployment agreement: 20/20 controls matched; 0 missing, 0 later and 0 earlier than the reference deployment.
- Synthetic candidates returned 0 zero-day ages. A `404` is scoped no-observation; `503` is unavailable or incomplete evidence. Neither proves the address never appeared.
- Transport failures: 0. No single universal wallet-age accuracy percentage is asserted.

The 20 contract references were independently checked using deployment receipts, canonical blocks and archive code transitions. Matching deployment time is not proof that no prefunding or earlier address mention existed. The 20 additional existing-account controls in the historical `existing_eoa` cohort prove prior use but do not provide independent first-use timestamps. Account-type labels are historical corpus labels and were not independently revalidated in this run. Synthetic addresses are deterministic unobserved candidates, not independently proven never-used addresses.

## Cache and measurement checks

- Initial application-cache rows: **0**. Final rows: **40**.
- Every valid input was absent immediately before its request: **80/80**.
- Observed cache-hit response headers: **0**.
- Requests: **100**, concurrency: **1**, request retries: **0**, shuffle seed: **20261105**.
- Sequential API time: **69.403 s**; measured loop wall time including 0.15 s pauses: **85.929 s**. Pauses and cache-proof reads are excluded from response latency.
- Paid providers configured: **none; public sources only**.
- Runtime source files unchanged during run: **True**.
- Enrichment RPC methods (balance/nonce/code): **0**.
- Unfinished HTTP calls at capture: **0**; unfinished source jobs: **0**.

The 2.6-second application budget bounds waiting for sources. It cannot guarantee complete provider data or bound arbitrary operating-system stalls and internet propagation. This measures localhost HTTP including full JSON body read, not remote-client transit, server startup, cold OS disk caches, or concurrent-load capacity.

## Provider observations

| Provider | HTTP-client attempts | JSON returned | Error categories | Locally expired attempts |
|---|---:|---:|---|---:|
| 1rpc | 30 | 30 | `{}` | 0 |
| blockcypher | 80 | 15 | `{"http_429": 65}` | 0 |
| blockpi | 60 | 60 | `{}` | 0 |
| blockscout | 160 | 0 | `{"http_429": 160}` | 0 |
| publicnode | 98 | 98 | `{}` | 0 |
| routescan | 160 | 160 | `{}` | 0 |

Source-job final states: `{"empty": 106, "error": 233, "verified_observation": 61}`.

Verified observations by indexed provider: `{"routescan": 61}`. Provider availability must be assessed separately from response latency. blockcypher: 65/80 HTTP 429; blockscout: 160/160 HTTP 429. Only one indexed provider supplied verified observations, so independent indexed-fallback success remains unverified by this run. Synthetic-candidate outcomes are included in the overall outcome table; errors are not counted as usable ages.

An HTTP JSON response is not necessarily a usable history result. Source jobs are reported separately as verified observation, unverified observation, empty, or error. Attempts include calls locally rejected after their deadline. HTTP calls and jobs carry the originating case ID, so late work is not attributed to the next address. Raw rows preserve the job snapshot at response time; final job states can include late results which the API did not use.

## Deployment controls

| Contract | Returned epoch | Reference epoch | Difference s |
|---|---:|---:|---:|
| WETH9 | 1513077455 | 1513077455 | 0 |
| USDC | 1533324504 | 1533324504 | 0 |
| USDT | 1511829681 | 1511829681 | 0 |
| DAI | 1573672677 | 1573672677 | 0 |
| UNI | 1600107086 | 1600107086 | 0 |
| LINK | 1505597189 | 1505597189 | 0 |
| WBTC | 1543095952 | 1543095952 | 0 |
| AAVE | 1600970788 | 1600970788 | 0 |
| MKR | 1511634257 | 1511634257 | 0 |
| COMP | 1583280535 | 1583280535 | 0 |
| Uniswap V2 Factory | 1588610042 | 1588610042 | 0 |
| Uniswap V2 Router02 | 1591388241 | 1591388241 | 0 |
| Uniswap V3 Factory | 1620156420 | 1620156420 | 0 |
| Uniswap V3 SwapRouter | 1620156641 | 1620156641 | 0 |
| Uniswap SwapRouter02 | 1639503446 | 1639503446 | 0 |
| Uniswap V3 NonfungiblePositionManager | 1620156913 | 1620156913 | 0 |
| Permit2 | 1668647879 | 1668647879 | 0 |
| ENS Registry | 1580344632 | 1580344632 | 0 |
| ENS BaseRegistrarImplementation | 1580345034 | 1580345034 | 0 |
| Beacon deposit contract | 1602667372 | 1602667372 | 0 |

## Previous run comparison

| Same 40 known-used controls | Ages returned | Median s | p95 s | Max s |
|---|---:|---:|---:|---:|
| Prior cold run | 40/40 | 1.5043 | 2.4680 | 2.4740 |
| This cold run | 40/40 | 1.3741 | 1.8004 | 1.9461 |

These runs use the same corpus and API. Public-provider conditions and upstream caches vary; differences are observations, not an isolated causal performance improvement.

## Reproduce and inspect

- Dataset SHA-256: `72d1a2fa26ad76e467e771e60c448aae16b49d319d27243278bdf751fd46dee0`.
- Python: `3.13.7 (main, Aug 14 2025, 11:12:11) [Clang 17.0.0 (clang-1700.0.13.3)]`.
- Files: `results.jsonl`, `summary.json`, `metadata.json`, `upstream-events.json`, `source-jobs.json`.
- Ethereum mainnet is the only searched chain. Reported ages are lower bounds from verified observed activity within covered histories.
- The sample is not representative of all Ethereum wallets: 18 transaction senders came from one block and popular contracts were chosen deliberately.

```bash
python3 benchmarks/run_uncached_wallet_age_benchmark.py --output benchmarks/run-uncached-new
```

## All 100 inputs

| ID | Address | Cohort | Seconds | HTTP | Result | Saved first epoch |
|---|---|---|---:|---:|---|---:|
| eth-001 | `"0xd8da6bf26964af9d7eed9e03e53415d37aa96045"` | existing_eoa | 1.8644 | 200 | `4056` | 1443428683 |
| eth-002 | `"0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"` | existing_eoa | 1.7742 | 200 | `4107` | 1439048640 |
| eth-003 | `"0xc917c3fa468f2c4b9c84c72caa46420eb9825249"` | existing_eoa | 0.8907 | 200 | `198` | 1776798083 |
| eth-004 | `"0x37c728c153eeb40ee907cb5b41b342d896878846"` | existing_eoa | 0.8928 | 200 | `36` | 1790780723 |
| eth-005 | `"0x35f7cff299f48b1b2a542224fd823152e437c654"` | existing_eoa | 0.8558 | 200 | `7` | 1793292215 |
| eth-006 | `"0xccba2faab763603b2894a5f203bd335dc26d16b8"` | existing_eoa | 1.3766 | 200 | `8` | 1793202539 |
| eth-007 | `"0xbdc8f547b06f06d38a102217ca28ce7ed9340805"` | existing_eoa | 1.4103 | 200 | `182` | 1778143487 |
| eth-008 | `"0xb5e5ba15a5aa58df03d91549496d8cd67a15f858"` | existing_eoa | 1.5760 | 200 | `55` | 1789129859 |
| eth-009 | `"0xd82c5660cac4169de051d7df34323f99c21176a7"` | existing_eoa | 1.1036 | 200 | `76` | 1787320835 |
| eth-010 | `"0x4817a075bb59c1ed2d21e8240ff1561750175515"` | existing_eoa | 1.3700 | 200 | `540` | 1747201343 |
| eth-011 | `"0x403e2b67f70ee3d10ec88c9b3d6de9eb1c7200d8"` | existing_eoa | 1.0035 | 200 | `15` | 1792556987 |
| eth-012 | `"0x91e33655188280afd5042a1fba723a731a117f1b"` | existing_eoa | 0.9219 | 200 | `18` | 1792341875 |
| eth-013 | `"0xc066ac5d385419b1a8c43a0e146fa439837a8b8c"` | existing_eoa | 1.3752 | 200 | `731` | 1730734559 |
| eth-014 | `"0x6d14296c706813c642045bc6102ee6e2fcaf8528"` | existing_eoa | 0.8283 | 200 | `114` | 1784052479 |
| eth-015 | `"0x51649cf810ea9f1f61165044b7149070c67f79c8"` | existing_eoa | 0.8782 | 200 | `24` | 1791777815 |
| eth-016 | `"0x07fcdbe8283f1b67cee5d5a4151cec8e8184301f"` | existing_eoa | 1.3531 | 200 | `153` | 1780687259 |
| eth-017 | `"0xb42f812a44c22cc6b861478900401ee759ebead6"` | existing_eoa | 1.0796 | 200 | `731` | 1730736719 |
| eth-018 | `"0x7a0e37f1543d0adb4e7ba7a27ea16b926b35a67c"` | existing_eoa | 1.3977 | 200 | `1128` | 1696442603 |
| eth-019 | `"0xaa8ba7d4611437141192e7ceced531bc0a133efb"` | existing_eoa | 1.4930 | 200 | `420` | 1757563883 |
| eth-020 | `"0xd37ac934502619cee14908cef22138f4b98e06b4"` | existing_eoa | 1.4121 | 200 | `332` | 1765201931 |
| eth-021 | `"0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"` | existing_contract | 1.6709 | 200 | `3250` | 1513077455 |
| eth-022 | `"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"` | existing_contract | 1.7970 | 200 | `3016` | 1533324504 |
| eth-023 | `"0xdac17f958d2ee523a2206206994597c13d831ec7"` | existing_contract | 1.9461 | 200 | `3264` | 1511829681 |
| eth-024 | `"0x6b175474e89094c44da98b954eedeac495271d0f"` | existing_contract | 1.3598 | 200 | `2549` | 1573672677 |
| eth-025 | `"0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"` | existing_contract | 1.0239 | 200 | `2243` | 1600107086 |
| eth-026 | `"0x514910771af9ca656af840dff83e8264ecf986ca"` | existing_contract | 0.9180 | 200 | `3336` | 1505597189 |
| eth-027 | `"0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"` | existing_contract | 1.3763 | 200 | `2902` | 1543095952 |
| eth-028 | `"0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"` | existing_contract | 0.9054 | 200 | `2233` | 1600970788 |
| eth-029 | `"0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"` | existing_contract | 1.3352 | 200 | `3267` | 1511634257 |
| eth-030 | `"0xc00e94cb662c3520282e6f5717214004a7f26888"` | existing_contract | 1.5892 | 200 | `2437` | 1583280535 |
| eth-031 | `"0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"` | existing_contract | 1.4684 | 200 | `2376` | 1588610042 |
| eth-032 | `"0x7a250d5630b4cf539739df2c5dacb4c659f2488d"` | existing_contract | 0.8896 | 200 | `2343` | 1591388241 |
| eth-033 | `"0x1f98431c8ad98523631ae4a59f267346ea31f984"` | existing_contract | 0.9429 | 200 | `2011` | 1620156420 |
| eth-034 | `"0xe592427a0aece92de3edee1f18e0157c05861564"` | existing_contract | 1.7517 | 200 | `2011` | 1620156641 |
| eth-035 | `"0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"` | existing_contract | 1.5087 | 200 | `1787` | 1639503446 |
| eth-036 | `"0xc36442b4a4522e871399cd717abdd847ab11fe88"` | existing_contract | 1.3654 | 200 | `2011` | 1620156913 |
| eth-037 | `"0x000000000022d473030f116ddee9f6b43ac78ba3"` | existing_contract | 1.3729 | 200 | `1449` | 1668647879 |
| eth-038 | `"0x00000000000c2e074ec69a0dfb2997ba6c7d2e1e"` | existing_contract | 1.4953 | 200 | `2471` | 1580344632 |
| eth-039 | `"0x57f1887a8bf19b14fc0df6fd9b2acc9af147ea85"` | existing_contract | 1.5114 | 200 | `2471` | 1580345034 |
| eth-040 | `"0x00000000219ab540356cbb839cbe05303d7705fa"` | existing_contract | 1.7445 | 200 | `2213` | 1602667372 |
| eth-041 | `"0x941c9014c10ccf1f90228453b75f6c9f66ab3237"` | generated_valid | 0.3332 | 503 | `"unknown"` | — |
| eth-042 | `"0xd5ff6dcf5153245be026debac13e4516a91481a7"` | generated_valid | 0.3229 | 503 | `"unknown"` | — |
| eth-043 | `"0x6d8abcc3de765f917d8a0d61a25bfaf84015fab6"` | generated_valid | 0.3298 | 503 | `"unknown"` | — |
| eth-044 | `"0x1abdd483a6bedb4a6e8c56d7cc21a472bd322947"` | generated_valid | 0.3300 | 503 | `"unknown"` | — |
| eth-045 | `"0x6d2b789677686865ffe5f493e7737a3953763657"` | generated_valid | 0.3721 | 503 | `"unknown"` | — |
| eth-046 | `"0x68a16f5a188af63ff96b847319a37a18a42471d3"` | generated_valid | 0.5369 | 503 | `"unknown"` | — |
| eth-047 | `"0x0d9f7e0f240d000303b18efdf7cbd7c41049527e"` | generated_valid | 0.3235 | 503 | `"unknown"` | — |
| eth-048 | `"0xe4ae5f19b13d1b383cb6c950d0dc5c4206402b77"` | generated_valid | 0.8746 | 503 | `"unknown"` | — |
| eth-049 | `"0x0ec23ed32cc5d9663b4f7052548d3c37264efd4f"` | generated_valid | 0.3090 | 503 | `"unknown"` | — |
| eth-050 | `"0x55c30b5034ce3e77c6c64eb42946a112287b6a28"` | generated_valid | 0.5221 | 503 | `"unknown"` | — |
| eth-051 | `"0xc09cc5c8b959b53d56cab2c78adb00e469490fde"` | generated_valid | 0.4614 | 503 | `"unknown"` | — |
| eth-052 | `"0x34d2ded544ec43d3f76262f346326d9c73c637db"` | generated_valid | 0.5382 | 503 | `"unknown"` | — |
| eth-053 | `"0xa7a024d96066322cd9ef0eb90277263fe1922948"` | generated_valid | 0.3433 | 503 | `"unknown"` | — |
| eth-054 | `"0xb9e83f94ea3768822f32d529fe98ebf911f09f9f"` | generated_valid | 0.3337 | 503 | `"unknown"` | — |
| eth-055 | `"0xc45a2acbbdec947e5b16a5102098854d516e88cb"` | generated_valid | 0.3334 | 503 | `"unknown"` | — |
| eth-056 | `"0x91fc52dbdacecf60132a46a0f2e72015d37e9e6c"` | generated_valid | 0.3139 | 503 | `"unknown"` | — |
| eth-057 | `"0xe63a40cfee31a745de2761daf89905d1c823258a"` | generated_valid | 0.3352 | 503 | `"unknown"` | — |
| eth-058 | `"0xf3c0fb4452bc1ff845f23443f5e5143a649f186f"` | generated_valid | 0.5577 | 503 | `"unknown"` | — |
| eth-059 | `"0x4031d3cdd02f329e9af1b494e78270255b724ada"` | generated_valid | 0.3450 | 503 | `"unknown"` | — |
| eth-060 | `"0x15dd314a2763474e0d5768c82a88aa686c38f5ae"` | generated_valid | 0.3133 | 503 | `"unknown"` | — |
| eth-061 | `"0x81328784d16a252808fab1b17ca695151eb79729"` | generated_valid | 0.3876 | 503 | `"unknown"` | — |
| eth-062 | `"0x5325fa1c98d091c07d63aa31cfa6abe8b2cb8343"` | generated_valid | 0.6781 | 503 | `"unknown"` | — |
| eth-063 | `"0xc29cbe4dcc1d39b7d18c0c16a6cf43b47e38ba51"` | generated_valid | 0.3212 | 503 | `"unknown"` | — |
| eth-064 | `"0x2e0fe7a6e908ad05efe54e954a70c022cadd1467"` | generated_valid | 0.3162 | 503 | `"unknown"` | — |
| eth-065 | `"0xadc677dc2cdad427d17b921fd158fc454860bf5d"` | generated_valid | 0.3556 | 503 | `"unknown"` | — |
| eth-066 | `"0x0b2a274c002b9469cd112ee4842ecaa347e61cb7"` | generated_valid | 0.4808 | 503 | `"unknown"` | — |
| eth-067 | `"0xe3685a40fa74deba76da12d2a3b914cd677f7ed7"` | generated_valid | 0.6145 | 503 | `"unknown"` | — |
| eth-068 | `"0x8af22802dfd21cb8f21736d203a3a142b9edf1ff"` | generated_valid | 0.6273 | 503 | `"unknown"` | — |
| eth-069 | `"0x9a33fc9010949bdef4fce8285ce842174592fbc4"` | generated_valid | 0.5590 | 503 | `"unknown"` | — |
| eth-070 | `"0xe1993bd0dc5ba0e38fd4bd6e0907061e96ebaf99"` | generated_valid | 0.3270 | 503 | `"unknown"` | — |
| eth-071 | `"0x48577c94da33e60423f741022e4842d65f5e4954"` | generated_valid | 0.3215 | 503 | `"unknown"` | — |
| eth-072 | `"0x614870240de6379b49a00dd45f3c384939d36964"` | generated_valid | 0.4595 | 503 | `"unknown"` | — |
| eth-073 | `"0xdcb2afaf2cb6e4f35807e2c996af8f9857518062"` | generated_valid | 0.3444 | 503 | `"unknown"` | — |
| eth-074 | `"0xbbe35e12f8779e9162dc31fafe1b12f0a7b12c94"` | generated_valid | 0.3693 | 503 | `"unknown"` | — |
| eth-075 | `"0xf8fd1362b19ba5dc5ac2506d9c0e78ff4df6daf7"` | generated_valid | 0.3529 | 503 | `"unknown"` | — |
| eth-076 | `"0x084c2026aef4c3ba51736174a1d4f5272f516e90"` | generated_valid | 0.3241 | 503 | `"unknown"` | — |
| eth-077 | `"0xcac36747d1cd34199cab69721cb6163b384ffe33"` | generated_valid | 0.4636 | 503 | `"unknown"` | — |
| eth-078 | `"0x8acd8c981d5e97fb3a1c03afe34548fb3cc6e61d"` | generated_valid | 0.3258 | 503 | `"unknown"` | — |
| eth-079 | `"0xc931ebb8f72fb21d9cc1f72ac847396fcd3e0721"` | generated_valid | 0.3314 | 503 | `"unknown"` | — |
| eth-080 | `"0x5d736d69aaf0f48fbe1f4129e2b476991b0ef36d"` | generated_valid | 0.3638 | 503 | `"unknown"` | — |
| eth-081 | `""` | malformed | 0.0038 | 400 | `"invalid"` | — |
| eth-082 | `"0x"` | malformed | 0.0030 | 400 | `"invalid"` | — |
| eth-083 | `"0x1234"` | malformed | 0.0050 | 400 | `"invalid"` | — |
| eth-084 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0051 | 400 | `"invalid"` | — |
| eth-085 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0013 | 400 | `"invalid"` | — |
| eth-086 | `"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0033 | 400 | `"invalid"` | — |
| eth-087 | `"0Xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0054 | 400 | `"invalid"` | — |
| eth-088 | `"0xgggggggggggggggggggggggggggggggggggggggg"` | malformed | 0.0045 | 400 | `"invalid"` | — |
| eth-089 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaz"` | malformed | 0.0056 | 400 | `"invalid"` | — |
| eth-090 | `" 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0061 | 400 | `"invalid"` | — |
| eth-091 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "` | malformed | 0.0050 | 400 | `"invalid"` | — |
| eth-092 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"` | malformed | 0.0135 | 400 | `"invalid"` | — |
| eth-093 | `"\t0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0055 | 400 | `"invalid"` | — |
| eth-094 | `"0x\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21\uff21"` | malformed | 0.0032 | 400 | `"invalid"` | — |
| eth-095 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\ud83d\ude00"` | malformed | 0.0047 | 400 | `"invalid"` | — |
| eth-096 | `"0\u0445aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0039 | 400 | `"invalid"` | — |
| eth-097 | `"vitalik.eth"` | malformed | 0.0057 | 400 | `"invalid"` | — |
| eth-098 | `"https://etherscan.io/address/0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"` | malformed | 0.0044 | 400 | `"invalid"` | — |
| eth-099 | `"0xaaaaaaaaaaaaaaaaaaaa_aaaaaaaaaaaaaaaaaaa"` | malformed | 0.0242 | 400 | `"invalid"` | — |
| eth-100 | `"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\u0000"` | malformed | 0.0067 | 400 | `"invalid"` | — |
