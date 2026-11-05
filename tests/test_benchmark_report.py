import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parent.parent / "benchmarks" / "write_report.py"
SPEC = importlib.util.spec_from_file_location("write_report", PATH)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class BenchmarkReportTests(unittest.TestCase):
    def fixture(self):
        metadata = {"started_at": "2026-11-05T18:00:00Z", "finished_at": "2026-11-05T18:10:00Z",
                    "production_unchanged": True, "total_api_requests": 1000}
        summary = {"metadata": dict(metadata), "dataset": {"cases": 100},
                   "results": {"run_finished": True, "rows": 1000, "phase_case_counts": {
                       phase: 100 for phase in ["detailed_cold", "detailed_warm", "basic_cold", "basic_warm"]}}}
        return summary, metadata

    def test_complete_unchanged_run_is_accepted(self):
        summary, metadata = self.fixture()
        report.validate_complete(summary, metadata)

    def test_unfinished_run_is_refused(self):
        summary, metadata = self.fixture()
        metadata.pop("finished_at")
        with self.assertRaisesRegex(ValueError, "not finished"):
            report.validate_complete(summary, metadata)

    def test_changed_source_is_refused(self):
        summary, metadata = self.fixture()
        metadata["production_unchanged"] = False
        with self.assertRaisesRegex(ValueError, "source changed"):
            report.validate_complete(summary, metadata)

    def test_stale_or_partial_summary_is_refused(self):
        summary, metadata = self.fixture()
        summary["results"]["rows"] = 999
        with self.assertRaisesRegex(ValueError, "row count"):
            report.validate_complete(summary, metadata)
        summary, metadata = self.fixture()
        summary["results"]["phase_case_counts"]["basic_warm"] = 99
        with self.assertRaisesRegex(ValueError, "Incomplete primary phase"):
            report.validate_complete(summary, metadata)

    def test_malformed_addresses_remain_visible_without_breaking_tables(self):
        actual = report.address_cell("a|b`\n Ω")
        self.assertNotIn("|", actual)
        self.assertEqual(actual.count("`"), 2)
        self.assertIn("\\n", actual)
        self.assertIn("\\u03a9", actual)
        self.assertIn("\\u007c", actual)

    def test_client_timezone_and_zero_denominators(self):
        self.assertEqual(report.local_time("2026-11-05T18:00:00Z"), "2026-11-05 12:00:00 CST")
        self.assertEqual(report.ratio(0, 0), "0/0 (not assessable)")
        self.assertEqual(report.percent(None), "—")


if __name__ == "__main__":
    unittest.main()
