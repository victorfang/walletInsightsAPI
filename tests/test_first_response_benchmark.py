import importlib.util
from pathlib import Path
import unittest

PATH=Path(__file__).resolve().parents[1]/"benchmarks/run_first_response_benchmark.py"
spec=importlib.util.spec_from_file_location("first_response_benchmark",PATH)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def row(arm="baseline", ident="known", category="existing_contract", status=200, epoch=100, seconds=1):
    return {"arm":arm,"phase":"cold","id":ident,"category":category,"status":status,
        "body":1 if status==200 else "unknown","seconds":seconds,"headers":{},"transport_error":None,
        "response_reason":None,"late_earlier_epoch":False,"formula_correct":status==200,
        "upstream_attempts_after_drain":0,"initial_epoch":epoch,"final_epoch":epoch}


class FirstResponseBenchmarkTests(unittest.TestCase):
    def test_success_latency_is_not_masked_by_fast_errors(self):
        values=[row(seconds=2),row(ident="bad",category="malformed",status=400,epoch=None,seconds=.001)]
        result=module.summarize(values,{"creation_candidates":[]},{},[],[])["arms"]["baseline"]
        self.assertEqual(result["overall_cold"]["inputs"],2)
        self.assertEqual(result["known_used_cold"]["inputs"],1)
        self.assertEqual(result["successful_cold"]["latency_seconds"]["median"],2)

    def test_earlier_deployment_observation_is_separate_from_exact(self):
        result=module.summarize([row(epoch=90)],{"creation_candidates":[{"id":"known","label":"control","timestamp":100}]},{},[],[])
        agreement=result["arms"]["baseline"]["deployment_agreement"]
        self.assertEqual(agreement["initial_exact"],0)
        self.assertEqual(agreement["initial_earlier"],1)
        self.assertEqual(agreement["initial_later"],0)

    def test_paired_and_late_refinement_are_not_initial_accuracy(self):
        candidate=row(arm="candidate_2_6",epoch=120)
        candidate.update(final_epoch=90,late_earlier_epoch=True)
        result=module.summarize([row(epoch=100),candidate],{"creation_candidates":[]},{},[],[])
        self.assertEqual(result["paired_initial_candidate_later"],1)
        self.assertEqual(result["paired_comparison"][0]["final_epoch_change_seconds"],-10)
        self.assertEqual(result["arms"]["candidate_2_6"]["overall_cold"]["late_earlier_epochs"],1)

    def test_cancelled_preflight_is_not_counted_as_potential_network_attempt(self):
        value=row(arm="candidate_2_6")
        value["upstream_attempts_at_response"]=1
        events=[{"request_id":"candidate_2_6/cold/known","json_returned":True},
                {"request_id":"candidate_2_6/cold/known","error":"cancelled"}]
        counts=module.attempt_accounting([value],events)["candidate_2_6"]
        self.assertEqual(counts["client_attempts"],2)
        self.assertEqual(counts["attempts_not_known_locally_rejected"],1)
        self.assertEqual(counts["after_response_preflight_rejections"],1)


if __name__=="__main__":unittest.main()
