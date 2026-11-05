import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).resolve().parent.parent / "benchmarks" / "analyze_benchmark.py"
SPEC = importlib.util.spec_from_file_location("analyze_benchmark", PATH)
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def case(identifier, category, valid=True, appeared=True):
    return {"id": identifier, "category": category, "label": identifier,
            "address": "0x" + identifier.zfill(40) if valid else "bad",
            "expected_valid_format": valid, "expected_has_appeared": appeared if valid else None,
            "expected_new_address": not appeared if valid else None}


def row(item, seen=True, phase="detailed_cold", first=None, hit=False, seconds=1):
    valid = item["expected_valid_format"]
    status = 400 if not valid else 503 if seen is None else 200
    value = {"is_address_valid": valid, "stats": {
        "has_appeared": seen if valid else None,
        "new_address": None if seen is None or not valid else not seen,
        "first_txn": first, "first_txn_block": 10 if first is not None else None,
    }}
    stored = valid and seen is not None
    return {"id": item["id"], "category": item["category"], "phase": phase,
            "http_status": status, "transport_error": None, "latency_seconds": seconds,
            "response": {"data": {item["address"]: value}, "meta": {"cache": {
                "hit": hit, "stored": stored,
            } if valid else None}},
            "upstream_http_requests": 0 if hit or not valid else 3,
            "upstream_rpc_methods": 0 if hit or not valid else 5,
            "upstream_events": []}


class BenchmarkAnalysisTests(unittest.TestCase):
    def test_type_seven_quantiles_and_empty_inputs(self):
        self.assertAlmostEqual(analysis.quantile([0, 10, 20, 30], .95), 28.5)
        self.assertIsNone(analysis.quantile([], .5))
        self.assertEqual(analysis.latency([])["n"], 0)
        self.assertIsNone(analysis.rate(0, 0))

    def test_unknown_is_not_counted_as_correct_or_as_a_negative(self):
        items = [case("1", "existing_eoa"), case("2", "existing_contract"),
                 case("3", "generated_valid", appeared=False), case("4", "malformed", valid=False)]
        rows = [row(items[0], True), row(items[1], None), row(items[2], False), row(items[3], None)]
        result = analysis.summarize(rows, items, {})
        metrics = result["phase_accuracy"]["detailed_cold"]
        self.assertEqual(metrics["format_accuracy"], 1)
        self.assertEqual(metrics["known_used_recall"]["correct"], 1)
        self.assertEqual(metrics["known_used_recall"]["unknown_or_unusable"], 1)
        self.assertEqual(metrics["valid_dataset_agreement"]["agreement_including_unknown_as_not_correct"], 2 / 3)
        self.assertEqual(metrics["valid_dataset_agreement"]["agreement_among_conclusive"], 1)
        self.assertEqual(result["phase_metrics"]["detailed_cold"]["cache"]["invalid_not_cacheable"], 1)

    def test_missing_address_global_400_is_a_correct_syntax_rejection(self):
        item = case("1", "malformed", valid=False)
        actual = row(item, None)
        actual["response"] = {"status": 400, "err_msg": "address is required", "data": {}}
        self.assertTrue(analysis.format_correct(actual, item))
        actual["transport_error"] = "JSONDecodeError"
        self.assertFalse(analysis.format_correct(actual, item))

    def test_deployment_reference_requires_canonical_verification(self):
        item = case("1", "existing_contract")
        actual = row(item, True, first=200)
        ref = {"reference_kind": "contract_deployment", "timestamp": 100, "block_number": 10}
        references = {"by_address": {item["address"]: ref}}
        age = analysis.age_analysis([actual], {item["id"]: item}, references, "2026-11-05T00:00:00Z")
        self.assertEqual(age["deployment_comparison"]["eligible_controls"], 0)
        self.assertEqual(age["deployment_comparison"]["ignored_unverified_reference_ids"], ["1"])
        ref.update(receipt_source="independent", block_source="independent", direct_creation_receipt_matches=True)
        age = analysis.age_analysis([actual], {item["id"]: item}, references, "2026-11-05T00:00:00Z")
        self.assertEqual(age["deployment_comparison"]["later_than_deployment"], 1)
        self.assertEqual(age["deployment_comparison"]["cases"][0]["error_seconds"], 100)

    def test_missing_age_is_coverage_failure_not_exact_agreement(self):
        item = case("1", "existing_contract")
        ref = {"reference_kind": "contract_deployment", "timestamp": 100, "block_number": 10,
               "receipt_source": "independent", "block_source": "independent", "direct_creation_receipt_matches": True}
        age = analysis.age_analysis([row(item, first=None)], {item["id"]: item},
                                    {"by_address": {item["address"]: ref}}, None)
        self.assertEqual(age["deployment_comparison"]["missing_first_txn"], 1)
        self.assertEqual(age["deployment_comparison"]["exact_agreement_all_controls"], 0)
        self.assertIsNone(age["deployment_comparison"]["exact_agreement_among_reported"])

    def test_earlier_than_deployment_is_ambiguous_not_labeled_wrong(self):
        item = case("1", "existing_contract")
        ref = {"reference_kind": "contract_deployment", "timestamp": 200, "block_number": 10,
               "receipt_source": "independent", "block_source": "independent", "direct_creation_receipt_matches": True}
        age = analysis.age_analysis([row(item, first=100)], {item["id"]: item},
                                    {"by_address": {item["address"]: ref}}, None)
        self.assertEqual(age["deployment_comparison"]["earlier_ambiguous"], 1)
        self.assertEqual(age["deployment_comparison"]["later_than_deployment"], 0)

    def test_recent_eoa_proof_is_only_an_upper_bound(self):
        item = case("1", "existing_eoa")
        item["proof"] = {"block_timestamp": 100}
        age = analysis.age_analysis([row(item, first=200)], {item["id"]: item}, {}, None)
        self.assertEqual(age["deployment_comparison"]["eligible_controls"], 0)
        self.assertEqual(age["eoa_positive_proof_bounds"]["reported_first_later_than_known_prior_use"], 1)

    def test_warm_cache_consistency_and_no_upstream(self):
        item = case("1", "existing_eoa")
        cold = row(item, first=100, seconds=5)
        warm = row(item, phase="detailed_warm", first=100, hit=True, seconds=.001)
        result = analysis.summarize([cold, warm], [item], {})
        self.assertEqual(result["cache_analysis"]["detailed"]["stable_stats_fraction"], 1)
        self.assertEqual(result["cache_analysis"]["detailed"]["warm_hits_with_zero_upstream"], 1)
        self.assertEqual(result["cache_analysis"]["detailed"]["valid_mean_speedup"], 5000)

    def test_partial_final_line_is_skipped_but_corrupt_complete_line_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text(json.dumps({"id": "1"}) + '\n{"id":')
            records, skipped = analysis.load_jsonl(path)
            self.assertEqual(records, [{"id": "1"}])
            self.assertEqual(skipped, 1)
            path.write_text('{"id":\n')
            with self.assertRaises(ValueError):
                analysis.load_jsonl(path)


if __name__ == "__main__":
    unittest.main()
