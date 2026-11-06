# walletAgeDays: first-two / one-second policy benchmark

Completed: 2026-11-06T05:28:20.613520Z

Actual local HTTP requests used live configured Alchemy/Etherscan/QuickNode plus existing public sources. Production was not changed or deployed. Both primary arms used the unchanged 100-address corpus, separate empty LRU databases, and interleaved randomized arm order. The candidate has a 1-second soft deadline, 2.6-second hard deadline, no exact timestamp-agreement requirement, and bounded background MIN refinement until its original hard deadline.

## Response latency and availability

Fast invalid/error responses are reported separately from successful ages and the 40 known-used controls.

| Policy / cohort | Inputs | Integer ages | Median s | p95 s | Maximum s | >3 s |
|---|---:|---:|---:|---:|---:|---:|
| baseline / overall_cold | 100 | 40 | 1.3929 | 2.4663 | 2.4756 | 0 |
| baseline / valid_cold | 80 | 40 | 1.4409 | 2.4682 | 2.4756 | 0 |
| baseline / known_used_cold | 40 | 40 | 1.4775 | 2.4745 | 2.4756 | 0 |
| baseline / successful_cold | 40 | 40 | 1.4775 | 2.4745 | 2.4756 | 0 |
| baseline / positive_cache_warm | 40 | 40 | 0.0040 | 0.0058 | 0.0065 | 0 |
| candidate_2_6 / overall_cold | 100 | 40 | 1.0177 | 1.9256 | 2.6185 | 0 |
| candidate_2_6 / valid_cold | 80 | 40 | 1.0838 | 1.9564 | 2.6185 | 0 |
| candidate_2_6 / known_used_cold | 40 | 40 | 1.1818 | 1.4734 | 1.7633 | 0 |
| candidate_2_6 / successful_cold | 40 | 40 | 1.1818 | 1.4734 | 1.7633 | 0 |
| candidate_2_6 / positive_cache_warm | 40 | 40 | 0.0033 | 0.0050 | 0.0057 | 0 |
| candidate_5_0 / overall_cold | 30 | 20 | 1.0240 | 1.3676 | 1.7517 | 0 |
| candidate_5_0 / valid_cold | 30 | 20 | 1.0240 | 1.3676 | 1.7517 | 0 |
| candidate_5_0 / known_used_cold | 20 | 20 | 1.1104 | 1.4321 | 1.7517 | 0 |
| candidate_5_0 / successful_cold | 20 | 20 | 1.1104 | 1.4321 | 1.7517 | 0 |
| candidate_5_0 / positive_cache_warm | 20 | 20 | 0.0042 | 0.0050 | 0.0051 | 0 |

The 5-second sensitivity arm, when present, uses only 30 inputs: the first ten EOAs, ten contracts and ten synthetics from the same corpus. It is a real additional run, not a replay, but its sample and provider conditions differ from the full primary runs. Provider caches cannot be cleared, and these localhost measurements exclude remote-client transit and concurrent-load capacity.

## Result quality and refinement

| Policy | Known-used ages | Initial deployment exact / eligible | Missing deployment ages | Later than deployment | Earlier than deployment | Late earlier cache updates |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 40/40 | 17/20 | 0 | 3 | 0 | 0 |
| candidate_2_6 | 40/40 | 17/20 | 0 | 3 | 0 | 4 |
| candidate_5_0 | 20/20 | 8/10 | 0 | 2 | 0 | 3 |

Paired successful initial responses: 40. Candidate returned a later epoch in 4 pairs and an earlier epoch in 0 pairs.

Initial epochs matched the current policy in 36/40 successful pairs; initial integer ages matched in 39/40. After bounded background refinement, cached epochs matched in 40/40. This is agreement with the current observed result, not universal accuracy.

Deployment timestamps are independent receipt/block/archive-code controls; they are not proof that an address never received prefunding or appeared earlier. Existing EOAs prove prior use but have no independently established exact first-use timestamp. Synthetic addresses are unobserved candidates, not proven never-used addresses. Consequently no universal age-accuracy or fraud-detection percentage is claimed.

## Stop reasons, outcomes and provider work

- **baseline:** HTTP outcomes `{"200": 40, "400": 20, "503": 40}`; stop reasons `{"none": 100}`; evidence `{"corroborated": 24, "multiple-sources": 16, "none": 60}`; total attributed upstream attempts including refinement: 1146.
  Malformed correct: 20; malformed upstream calls: 0; synthetic zero-day ages: 0.
- **candidate_2_6:** HTTP outcomes `{"200": 40, "400": 20, "503": 40}`; stop reasons `{"all_finished": 39, "hard_deadline": 1, "none": 20, "one_provider_soft_deadline": 40}`; evidence `{"multiple-sources": 15, "none": 60, "single-source": 25}`; total attributed upstream attempts including refinement: 1131.
  Malformed correct: 20; malformed upstream calls: 0; synthetic zero-day ages: 0.
- **candidate_5_0:** HTTP outcomes `{"200": 20, "503": 10}`; stop reasons `{"all_finished": 10, "one_provider_soft_deadline": 20}`; evidence `{"multiple-sources": 2, "none": 10, "single-source": 18}`; total attributed upstream attempts including refinement: 486.
  Malformed correct: 0; malformed upstream calls: 0; synthetic zero-day ages: 0.

Two independent providers count only after all their group jobs have settled successfully and supplied a finalized positive observation. Alchemy incoming/outgoing count as one provider. A partly completed provider can contribute an earlier verified epoch to the minimum, but cannot count as a second provider. The candidate intentionally omits the current policy's exact-agreement and Alchemy-settled guard. On a single valid observation after one second it returns immediately; one second is a soft deadline, not a guaranteed response time. No observation yields scoped not_found only if all histories were empty, otherwise unknown; neither becomes age zero.

Background refinement does not delay the initial response. The runner drains it before the next cold lookup and warm request to avoid overlapping benchmark traffic. Drain time is excluded from response latency but included in total wall time and API-attempt accounting. Only positive cached records are warmed; uncached errors are not re-labeled cache hits. The network lock prevents overlap with the separate vendor benchmark.

The primary candidate's 40 successful known-used responses stopped on the single-provider soft-deadline branch 40 times and the two-completed-provider branch 0 times. The observed speedup therefore does not demonstrate a fast two-provider path. A provider's raw JSON response and a completed usable provider group are different events.

## Deadline-window accounting

The baseline's 2.6-second application budget reserves 150 ms for persistence, leaving about 2.45 s of source waiting. The experimental candidate uses its full 2.6-second hard deadline and guards persistence against that deadline. This 150 ms difference can affect availability; the comparison is not an isolated stopping-rule experiment.

Post-hoc inspection of the primary candidate's actual traces found 0 cases whose only verified positive arrived after 2.45 s and 0 cases whose minimum improved during 2.45–2.6 s. 2 HTTP responses exceeded 2.45 s, of which 0 were successful ages. Initial cache bypasses: 0; positive responses without a retained epoch: 0. These are observed-window counts, not a counterfactual HTTP run.

Background refinement initiated 28 additional attributed HTTP-client attempts after the primary candidate's full response was received. Discovery jobs were not restarted, but already-started jobs could continue necessary verification RPC calls. Already sent requests may still be charged. Client attempts include local errors/cancellation and are not an invoice or billable-request count. Rejected refinement admissions after early returns: 0.

Cache warm timings use these small isolated databases after population, not a new one-million-row benchmark. The LRU capacity remains one million; prior dedicated capacity tests are separate.

## Reproduction and raw evidence

```bash
python3 benchmarks/run_first_response_benchmark.py --env-file .env --output benchmarks/run-first-response-new
```

Artifacts: results.jsonl, summary.json, metadata.json, upstream-events.json, source-jobs.json. No credentials or configured URLs are recorded. SQLite files are isolated experiment state and excluded from publication.

## All cold results

| Policy | ID | Category | HTTP | Seconds | Initial epoch | Refined epoch |
|---|---|---|---:|---:|---:|---:|
| baseline | eth-001 | existing_eoa | 200 | 1.4319 | 1443428683 | 1443428683 |
| baseline | eth-002 | existing_eoa | 200 | 1.3581 | 1439048640 | 1439048640 |
| baseline | eth-003 | existing_eoa | 200 | 1.5309 | 1776798083 | 1776798083 |
| baseline | eth-004 | existing_eoa | 200 | 1.5640 | 1790780723 | 1790780723 |
| baseline | eth-005 | existing_eoa | 200 | 1.4119 | 1793288447 | 1793288447 |
| baseline | eth-006 | existing_eoa | 200 | 1.4753 | 1793202383 | 1793202383 |
| baseline | eth-007 | existing_eoa | 200 | 1.3946 | 1778143487 | 1778143487 |
| baseline | eth-008 | existing_eoa | 200 | 1.4424 | 1787730431 | 1787730431 |
| baseline | eth-009 | existing_eoa | 200 | 1.3903 | 1787320835 | 1787320835 |
| baseline | eth-010 | existing_eoa | 200 | 1.4798 | 1747201343 | 1747201343 |
| baseline | eth-011 | existing_eoa | 200 | 1.4056 | 1792556987 | 1792556987 |
| baseline | eth-012 | existing_eoa | 200 | 2.4721 | 1792341875 | 1792341875 |
| baseline | eth-013 | existing_eoa | 200 | 2.4745 | 1730734559 | 1730734559 |
| baseline | eth-014 | existing_eoa | 200 | 1.4580 | 1783102199 | 1783102199 |
| baseline | eth-015 | existing_eoa | 200 | 2.4599 | 1791761771 | 1791761771 |
| baseline | eth-016 | existing_eoa | 200 | 1.4236 | 1780591799 | 1780591799 |
| baseline | eth-017 | existing_eoa | 200 | 1.4394 | 1730736719 | 1730736719 |
| baseline | eth-018 | existing_eoa | 200 | 1.3813 | 1696442603 | 1696442603 |
| baseline | eth-019 | existing_eoa | 200 | 1.4998 | 1757563883 | 1757563883 |
| baseline | eth-020 | existing_eoa | 200 | 2.4748 | 1765201931 | 1765201931 |
| baseline | eth-021 | existing_contract | 200 | 1.4104 | 1513077455 | 1513077455 |
| baseline | eth-022 | existing_contract | 200 | 1.4206 | 1533324504 | 1533324504 |
| baseline | eth-023 | existing_contract | 200 | 1.4037 | 1511829681 | 1511829681 |
| baseline | eth-024 | existing_contract | 200 | 2.2722 | 1573993299 | 1573993299 |
| baseline | eth-025 | existing_contract | 200 | 1.5549 | 1600107086 | 1600107086 |
| baseline | eth-026 | existing_contract | 200 | 1.6388 | 1505597189 | 1505597189 |
| baseline | eth-027 | existing_contract | 200 | 1.3428 | 1543095952 | 1543095952 |
| baseline | eth-028 | existing_contract | 200 | 1.5244 | 1600970788 | 1600970788 |
| baseline | eth-029 | existing_contract | 200 | 2.3364 | 1511637118 | 1511637118 |
| baseline | eth-030 | existing_contract | 200 | 1.3912 | 1583280535 | 1583280535 |
| baseline | eth-031 | existing_contract | 200 | 1.3835 | 1588610042 | 1588610042 |
| baseline | eth-032 | existing_contract | 200 | 2.4756 | 1591388241 | 1591388241 |
| baseline | eth-033 | existing_contract | 200 | 1.9402 | 1620156420 | 1620156420 |
| baseline | eth-034 | existing_contract | 200 | 1.4866 | 1620156641 | 1620156641 |
| baseline | eth-035 | existing_contract | 200 | 2.4599 | 1639503446 | 1639503446 |
| baseline | eth-036 | existing_contract | 200 | 2.4662 | 1620156913 | 1620156913 |
| baseline | eth-037 | existing_contract | 200 | 2.4653 | 1668653243 | 1668653243 |
| baseline | eth-038 | existing_contract | 200 | 1.4564 | 1580344632 | 1580344632 |
| baseline | eth-039 | existing_contract | 200 | 2.4679 | 1580345034 | 1580345034 |
| baseline | eth-040 | existing_contract | 200 | 1.3313 | 1602667372 | 1602667372 |
| baseline | eth-041 | generated_valid | 503 | 0.6144 | None | None |
| baseline | eth-042 | generated_valid | 503 | 0.5866 | None | None |
| baseline | eth-043 | generated_valid | 503 | 1.4193 | None | None |
| baseline | eth-044 | generated_valid | 503 | 0.5693 | None | None |
| baseline | eth-045 | generated_valid | 503 | 2.2466 | None | None |
| baseline | eth-046 | generated_valid | 503 | 1.3659 | None | None |
| baseline | eth-047 | generated_valid | 503 | 0.8529 | None | None |
| baseline | eth-048 | generated_valid | 503 | 0.4830 | None | None |
| baseline | eth-049 | generated_valid | 503 | 1.2856 | None | None |
| baseline | eth-050 | generated_valid | 503 | 2.3359 | None | None |
| baseline | eth-051 | generated_valid | 503 | 1.3745 | None | None |
| baseline | eth-052 | generated_valid | 503 | 0.9786 | None | None |
| baseline | eth-053 | generated_valid | 503 | 2.4621 | None | None |
| baseline | eth-054 | generated_valid | 503 | 1.5702 | None | None |
| baseline | eth-055 | generated_valid | 503 | 1.3389 | None | None |
| baseline | eth-056 | generated_valid | 503 | 1.3336 | None | None |
| baseline | eth-057 | generated_valid | 503 | 0.5971 | None | None |
| baseline | eth-058 | generated_valid | 503 | 0.6136 | None | None |
| baseline | eth-059 | generated_valid | 503 | 1.5301 | None | None |
| baseline | eth-060 | generated_valid | 503 | 2.4644 | None | None |
| baseline | eth-061 | generated_valid | 503 | 0.8244 | None | None |
| baseline | eth-062 | generated_valid | 503 | 0.6645 | None | None |
| baseline | eth-063 | generated_valid | 503 | 2.4555 | None | None |
| baseline | eth-064 | generated_valid | 503 | 1.5119 | None | None |
| baseline | eth-065 | generated_valid | 503 | 2.2294 | None | None |
| baseline | eth-066 | generated_valid | 503 | 1.5192 | None | None |
| baseline | eth-067 | generated_valid | 503 | 1.7878 | None | None |
| baseline | eth-068 | generated_valid | 503 | 1.0353 | None | None |
| baseline | eth-069 | generated_valid | 503 | 2.4649 | None | None |
| baseline | eth-070 | generated_valid | 503 | 0.6990 | None | None |
| baseline | eth-071 | generated_valid | 503 | 0.6143 | None | None |
| baseline | eth-072 | generated_valid | 503 | 0.5885 | None | None |
| baseline | eth-073 | generated_valid | 503 | 0.8040 | None | None |
| baseline | eth-074 | generated_valid | 503 | 2.4579 | None | None |
| baseline | eth-075 | generated_valid | 503 | 2.1901 | None | None |
| baseline | eth-076 | generated_valid | 503 | 2.1971 | None | None |
| baseline | eth-077 | generated_valid | 503 | 0.7703 | None | None |
| baseline | eth-078 | generated_valid | 503 | 0.5806 | None | None |
| baseline | eth-079 | generated_valid | 503 | 2.3502 | None | None |
| baseline | eth-080 | generated_valid | 503 | 1.0486 | None | None |
| baseline | eth-081 | malformed | 400 | 0.0013 | None | None |
| baseline | eth-082 | malformed | 400 | 0.0012 | None | None |
| baseline | eth-083 | malformed | 400 | 0.0013 | None | None |
| baseline | eth-084 | malformed | 400 | 0.0014 | None | None |
| baseline | eth-085 | malformed | 400 | 0.0024 | None | None |
| baseline | eth-086 | malformed | 400 | 0.0008 | None | None |
| baseline | eth-087 | malformed | 400 | 0.0016 | None | None |
| baseline | eth-088 | malformed | 400 | 0.0010 | None | None |
| baseline | eth-089 | malformed | 400 | 0.0007 | None | None |
| baseline | eth-090 | malformed | 400 | 0.0016 | None | None |
| baseline | eth-091 | malformed | 400 | 0.0024 | None | None |
| baseline | eth-092 | malformed | 400 | 0.0014 | None | None |
| baseline | eth-093 | malformed | 400 | 0.0013 | None | None |
| baseline | eth-094 | malformed | 400 | 0.0022 | None | None |
| baseline | eth-095 | malformed | 400 | 0.0024 | None | None |
| baseline | eth-096 | malformed | 400 | 0.0007 | None | None |
| baseline | eth-097 | malformed | 400 | 0.0025 | None | None |
| baseline | eth-098 | malformed | 400 | 0.0011 | None | None |
| baseline | eth-099 | malformed | 400 | 0.0786 | None | None |
| baseline | eth-100 | malformed | 400 | 0.0017 | None | None |
| candidate_2_6 | eth-001 | existing_eoa | 200 | 1.0147 | 1443428683 | 1443428683 |
| candidate_2_6 | eth-002 | existing_eoa | 200 | 1.3271 | 1439048640 | 1439048640 |
| candidate_2_6 | eth-003 | existing_eoa | 200 | 1.1254 | 1776798083 | 1776798083 |
| candidate_2_6 | eth-004 | existing_eoa | 200 | 1.0239 | 1790780723 | 1790780723 |
| candidate_2_6 | eth-005 | existing_eoa | 200 | 1.1269 | 1793292215 | 1793288447 |
| candidate_2_6 | eth-006 | existing_eoa | 200 | 1.2534 | 1793202539 | 1793202383 |
| candidate_2_6 | eth-007 | existing_eoa | 200 | 1.1243 | 1778143487 | 1778143487 |
| candidate_2_6 | eth-008 | existing_eoa | 200 | 1.0859 | 1789129859 | 1787730431 |
| candidate_2_6 | eth-009 | existing_eoa | 200 | 1.0615 | 1787320835 | 1787320835 |
| candidate_2_6 | eth-010 | existing_eoa | 200 | 1.0358 | 1747201343 | 1747201343 |
| candidate_2_6 | eth-011 | existing_eoa | 200 | 1.4725 | 1792556987 | 1792556987 |
| candidate_2_6 | eth-012 | existing_eoa | 200 | 1.1231 | 1792341875 | 1792341875 |
| candidate_2_6 | eth-013 | existing_eoa | 200 | 1.0514 | 1730734559 | 1730734559 |
| candidate_2_6 | eth-014 | existing_eoa | 200 | 1.3806 | 1783102199 | 1783102199 |
| candidate_2_6 | eth-015 | existing_eoa | 200 | 1.2424 | 1791777815 | 1791761771 |
| candidate_2_6 | eth-016 | existing_eoa | 200 | 1.4018 | 1780591799 | 1780591799 |
| candidate_2_6 | eth-017 | existing_eoa | 200 | 1.3870 | 1730736719 | 1730736719 |
| candidate_2_6 | eth-018 | existing_eoa | 200 | 1.3814 | 1696442603 | 1696442603 |
| candidate_2_6 | eth-019 | existing_eoa | 200 | 1.4905 | 1757563883 | 1757563883 |
| candidate_2_6 | eth-020 | existing_eoa | 200 | 1.2183 | 1765201931 | 1765201931 |
| candidate_2_6 | eth-021 | existing_contract | 200 | 1.0069 | 1513077455 | 1513077455 |
| candidate_2_6 | eth-022 | existing_contract | 200 | 1.0679 | 1533324504 | 1533324504 |
| candidate_2_6 | eth-023 | existing_contract | 200 | 1.3254 | 1511829681 | 1511829681 |
| candidate_2_6 | eth-024 | existing_contract | 200 | 1.2592 | 1573993299 | 1573993299 |
| candidate_2_6 | eth-025 | existing_contract | 200 | 1.1564 | 1600107086 | 1600107086 |
| candidate_2_6 | eth-026 | existing_contract | 200 | 1.0571 | 1505597189 | 1505597189 |
| candidate_2_6 | eth-027 | existing_contract | 200 | 1.3069 | 1543095952 | 1543095952 |
| candidate_2_6 | eth-028 | existing_contract | 200 | 1.1814 | 1600970788 | 1600970788 |
| candidate_2_6 | eth-029 | existing_contract | 200 | 1.7633 | 1511637118 | 1511637118 |
| candidate_2_6 | eth-030 | existing_contract | 200 | 1.0243 | 1583280535 | 1583280535 |
| candidate_2_6 | eth-031 | existing_contract | 200 | 1.3204 | 1588610042 | 1588610042 |
| candidate_2_6 | eth-032 | existing_contract | 200 | 1.4499 | 1591388241 | 1591388241 |
| candidate_2_6 | eth-033 | existing_contract | 200 | 1.0817 | 1620156420 | 1620156420 |
| candidate_2_6 | eth-034 | existing_contract | 200 | 1.3880 | 1620156641 | 1620156641 |
| candidate_2_6 | eth-035 | existing_contract | 200 | 1.1042 | 1639503446 | 1639503446 |
| candidate_2_6 | eth-036 | existing_contract | 200 | 1.0673 | 1620156913 | 1620156913 |
| candidate_2_6 | eth-037 | existing_contract | 200 | 1.4046 | 1668653243 | 1668653243 |
| candidate_2_6 | eth-038 | existing_contract | 200 | 1.3676 | 1580344632 | 1580344632 |
| candidate_2_6 | eth-039 | existing_contract | 200 | 1.0207 | 1580345034 | 1580345034 |
| candidate_2_6 | eth-040 | existing_contract | 200 | 1.1822 | 1602667372 | 1602667372 |
| candidate_2_6 | eth-041 | generated_valid | 503 | 2.6185 | None | None |
| candidate_2_6 | eth-042 | generated_valid | 503 | 1.3961 | None | None |
| candidate_2_6 | eth-043 | generated_valid | 503 | 0.5869 | None | None |
| candidate_2_6 | eth-044 | generated_valid | 503 | 0.6824 | None | None |
| candidate_2_6 | eth-045 | generated_valid | 503 | 0.5873 | None | None |
| candidate_2_6 | eth-046 | generated_valid | 503 | 0.5951 | None | None |
| candidate_2_6 | eth-047 | generated_valid | 503 | 0.5747 | None | None |
| candidate_2_6 | eth-048 | generated_valid | 503 | 2.5976 | None | None |
| candidate_2_6 | eth-049 | generated_valid | 503 | 0.6045 | None | None |
| candidate_2_6 | eth-050 | generated_valid | 503 | 0.5873 | None | None |
| candidate_2_6 | eth-051 | generated_valid | 503 | 0.6062 | None | None |
| candidate_2_6 | eth-052 | generated_valid | 503 | 0.5855 | None | None |
| candidate_2_6 | eth-053 | generated_valid | 503 | 0.6040 | None | None |
| candidate_2_6 | eth-054 | generated_valid | 503 | 0.5978 | None | None |
| candidate_2_6 | eth-055 | generated_valid | 503 | 0.6376 | None | None |
| candidate_2_6 | eth-056 | generated_valid | 503 | 0.4609 | None | None |
| candidate_2_6 | eth-057 | generated_valid | 503 | 1.6943 | None | None |
| candidate_2_6 | eth-058 | generated_valid | 503 | 1.7712 | None | None |
| candidate_2_6 | eth-059 | generated_valid | 503 | 0.6040 | None | None |
| candidate_2_6 | eth-060 | generated_valid | 503 | 0.6832 | None | None |
| candidate_2_6 | eth-061 | generated_valid | 503 | 0.7217 | None | None |
| candidate_2_6 | eth-062 | generated_valid | 503 | 1.2620 | None | None |
| candidate_2_6 | eth-063 | generated_valid | 503 | 0.4572 | None | None |
| candidate_2_6 | eth-064 | generated_valid | 503 | 0.5757 | None | None |
| candidate_2_6 | eth-065 | generated_valid | 503 | 0.6567 | None | None |
| candidate_2_6 | eth-066 | generated_valid | 503 | 0.7744 | None | None |
| candidate_2_6 | eth-067 | generated_valid | 503 | 0.6011 | None | None |
| candidate_2_6 | eth-068 | generated_valid | 503 | 0.5781 | None | None |
| candidate_2_6 | eth-069 | generated_valid | 503 | 0.6841 | None | None |
| candidate_2_6 | eth-070 | generated_valid | 503 | 2.0949 | None | None |
| candidate_2_6 | eth-071 | generated_valid | 503 | 1.7620 | None | None |
| candidate_2_6 | eth-072 | generated_valid | 503 | 2.3386 | None | None |
| candidate_2_6 | eth-073 | generated_valid | 503 | 0.5724 | None | None |
| candidate_2_6 | eth-074 | generated_valid | 503 | 0.6057 | None | None |
| candidate_2_6 | eth-075 | generated_valid | 503 | 0.6562 | None | None |
| candidate_2_6 | eth-076 | generated_valid | 503 | 1.9491 | None | None |
| candidate_2_6 | eth-077 | generated_valid | 503 | 1.9244 | None | None |
| candidate_2_6 | eth-078 | generated_valid | 503 | 1.4072 | None | None |
| candidate_2_6 | eth-079 | generated_valid | 503 | 0.6103 | None | None |
| candidate_2_6 | eth-080 | generated_valid | 503 | 0.5835 | None | None |
| candidate_2_6 | eth-081 | malformed | 400 | 0.0011 | None | None |
| candidate_2_6 | eth-082 | malformed | 400 | 0.0017 | None | None |
| candidate_2_6 | eth-083 | malformed | 400 | 0.0012 | None | None |
| candidate_2_6 | eth-084 | malformed | 400 | 0.0019 | None | None |
| candidate_2_6 | eth-085 | malformed | 400 | 0.0017 | None | None |
| candidate_2_6 | eth-086 | malformed | 400 | 0.0010 | None | None |
| candidate_2_6 | eth-087 | malformed | 400 | 0.0012 | None | None |
| candidate_2_6 | eth-088 | malformed | 400 | 0.0013 | None | None |
| candidate_2_6 | eth-089 | malformed | 400 | 0.0010 | None | None |
| candidate_2_6 | eth-090 | malformed | 400 | 0.0021 | None | None |
| candidate_2_6 | eth-091 | malformed | 400 | 0.0015 | None | None |
| candidate_2_6 | eth-092 | malformed | 400 | 0.0018 | None | None |
| candidate_2_6 | eth-093 | malformed | 400 | 0.0015 | None | None |
| candidate_2_6 | eth-094 | malformed | 400 | 0.0014 | None | None |
| candidate_2_6 | eth-095 | malformed | 400 | 0.0024 | None | None |
| candidate_2_6 | eth-096 | malformed | 400 | 0.0011 | None | None |
| candidate_2_6 | eth-097 | malformed | 400 | 0.0017 | None | None |
| candidate_2_6 | eth-098 | malformed | 400 | 0.0015 | None | None |
| candidate_2_6 | eth-099 | malformed | 400 | 0.0010 | None | None |
| candidate_2_6 | eth-100 | malformed | 400 | 0.0012 | None | None |
| candidate_5_0 | eth-001 | existing_eoa | 200 | 1.0143 | 1443428683 | 1443428683 |
| candidate_5_0 | eth-002 | existing_eoa | 200 | 1.1126 | 1439048640 | 1439048640 |
| candidate_5_0 | eth-003 | existing_eoa | 200 | 1.1207 | 1776798083 | 1776798083 |
| candidate_5_0 | eth-004 | existing_eoa | 200 | 1.1158 | 1790780723 | 1790780723 |
| candidate_5_0 | eth-005 | existing_eoa | 200 | 1.2027 | 1793292215 | 1793288447 |
| candidate_5_0 | eth-006 | existing_eoa | 200 | 1.1204 | 1793202539 | 1793202383 |
| candidate_5_0 | eth-007 | existing_eoa | 200 | 1.0855 | 1778143487 | 1778143487 |
| candidate_5_0 | eth-008 | existing_eoa | 200 | 1.1304 | 1789129859 | 1787730431 |
| candidate_5_0 | eth-009 | existing_eoa | 200 | 1.1280 | 1787320835 | 1787320835 |
| candidate_5_0 | eth-010 | existing_eoa | 200 | 1.1081 | 1747201343 | 1747201343 |
| candidate_5_0 | eth-021 | existing_contract | 200 | 1.0216 | 1513077455 | 1513077455 |
| candidate_5_0 | eth-022 | existing_contract | 200 | 1.0833 | 1533324504 | 1533324504 |
| candidate_5_0 | eth-023 | existing_contract | 200 | 1.0264 | 1511829681 | 1511829681 |
| candidate_5_0 | eth-024 | existing_contract | 200 | 1.4153 | 1573993299 | 1573993299 |
| candidate_5_0 | eth-025 | existing_contract | 200 | 1.0074 | 1600107086 | 1600107086 |
| candidate_5_0 | eth-026 | existing_contract | 200 | 1.3094 | 1505597189 | 1505597189 |
| candidate_5_0 | eth-027 | existing_contract | 200 | 1.0165 | 1543095952 | 1543095952 |
| candidate_5_0 | eth-028 | existing_contract | 200 | 1.0212 | 1600970788 | 1600970788 |
| candidate_5_0 | eth-029 | existing_contract | 200 | 1.7517 | 1511637118 | 1511637118 |
| candidate_5_0 | eth-030 | existing_contract | 200 | 1.0473 | 1583280535 | 1583280535 |
| candidate_5_0 | eth-041 | generated_valid | 503 | 0.4596 | None | None |
| candidate_5_0 | eth-042 | generated_valid | 503 | 0.5268 | None | None |
| candidate_5_0 | eth-043 | generated_valid | 503 | 0.4666 | None | None |
| candidate_5_0 | eth-044 | generated_valid | 503 | 0.5922 | None | None |
| candidate_5_0 | eth-045 | generated_valid | 503 | 0.5848 | None | None |
| candidate_5_0 | eth-046 | generated_valid | 503 | 0.7161 | None | None |
| candidate_5_0 | eth-047 | generated_valid | 503 | 0.5950 | None | None |
| candidate_5_0 | eth-048 | generated_valid | 503 | 0.5973 | None | None |
| candidate_5_0 | eth-049 | generated_valid | 503 | 0.6020 | None | None |
| candidate_5_0 | eth-050 | generated_valid | 503 | 0.6087 | None | None |

## HTTP-client attempt accounting

The meter records entry before the HTTP client's deadline/cancellation preflight. Locally cancelled/expired entries did not dispatch HTTP. Other entries may still fail during DNS/connect and are therefore potential network attempts, not proved wire requests or billable calls.

| Policy | Client attempts | Local preflight rejection | Not known locally rejected | Attempts logged after response snapshot | Post-response local rejection | Post-response not known locally rejected |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 1146 | 12 | 1134 | 12 | 12 | 0 |
| candidate_2_6 | 1131 | 5 | 1126 | 28 | 5 | 23 |
| candidate_5_0 | 486 | 0 | 486 | 5 | 0 | 5 |
