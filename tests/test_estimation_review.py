import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_estimator_catalog import apply_estimation_review, load_estimation_review, ESTIMATION_REVIEW_PATH
from input_fee_evaluator import evaluate_fee_graph
from option_runtime import evaluate_option
from project_public_catalog import OptionProjection
import refresh_estimator


def document():
    def fixed(key, amount):
        return {
            "feeTemplateFeeId": key, "feeId": key, "name": key,
            "section": "configured", "applicability": {"status": "configured", "conditions": []},
            "calculation": {"mode": "fixed", "fixedAmount": amount, "rule": {}},
            "validation": {"status": "validated", "codes": [], "notes": []},
        }
    held = fixed("HELD", 100)
    dependent = fixed("DEPENDENT", None)
    dependent["calculation"] = {
        "mode": "input", "rule": {"amount": 0.04, "inputs": [{"source": "fee", "inputKey": "HELD"}]},
    }
    return {
        "catalogVersion": "test", "configuration": {"asOfDate": "2026-10-14", "historicalPeriod": {}},
        "option": {"optionKey": "TEST", "permitType": "Generic permit", "workClass": "Generic work"},
        "fees": [held, dependent, fixed("BASE", 5)],
        "relatedPermits": [], "disclosures": [],
    }


class EstimationReviewTests(unittest.TestCase):
    def test_review_preserves_configuration_and_applies_to_related_fees(self):
        d = document()
        before = copy.deepcopy(d["fees"][0])
        d["relatedPermits"] = [{"fees": [copy.deepcopy(before)]}]
        apply_estimation_review(d, {"held": "Staff confirmation is required."})
        for fee in [d["fees"][0], d["relatedPermits"][0]["fees"][0]]:
            self.assertEqual(fee["section"], before["section"])
            self.assertEqual(fee["calculation"], before["calculation"])
            self.assertEqual(fee["applicability"], before["applicability"])
            self.assertEqual(fee["estimationReview"], "Staff confirmation is required.")
            self.assertIn("ESTIMATION_REVIEW_REQUIRED", fee["validation"]["codes"])
        self.assertNotIn("estimationReview", d["fees"][2])

    def test_held_fee_and_its_dependent_cannot_enter_total(self):
        d = document()
        self.assertEqual(evaluate_option(d)["report"]["estimatedTotal"], 109)
        apply_estimation_review(d, {"held": "Staff confirmation is required."})
        report = evaluate_option(d)["report"]
        self.assertEqual(report["estimatedTotal"], 5)
        self.assertEqual({f["name"] for f in report["blockedFees"]}, {"HELD", "DEPENDENT"})
        self.assertTrue(report["requiresStaffReview"])
        results = evaluate_fee_graph(d["fees"], ["HELD", "DEPENDENT"])
        for result in results.values():
            self.assertEqual(result["status"], "blocked")
            self.assertIn("ESTIMATION_REVIEW_REQUIRED", result["validationCodes"])

    def test_known_inapplicable_held_fee_does_not_block_other_fees(self):
        d = document()
        held = d["fees"][0]
        held["section"] = "conditional"
        held["applicability"]["status"] = "conditional"
        apply_estimation_review(d, {"held": "Staff confirmation is required."})
        result = evaluate_fee_graph(d["fees"], ["HELD", "DEPENDENT"], applicability={"HELD": False})
        self.assertEqual(result["HELD"]["status"], "not-applicable")
        self.assertEqual(result["DEPENDENT"]["amount"], 0)

    def test_policy_loads_and_rejects_invalid_entries(self):
        policy, reviews = load_estimation_review(ESTIMATION_REVIEW_PATH)
        self.assertEqual(policy["schemaVersion"], "1.0")
        self.assertTrue(reviews)
        for payload in [{"schemaVersion": "2.0", "fees": {}},
                        {"schemaVersion": "1.0", "fees": {"invalid": "Review"}},
                        {"schemaVersion": "1.0", "fees": {"11111111-1111-1111-1111-111111111111": ""}}]:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                file = Path(tmp) / "review.json"
                file.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_estimation_review(file)

    def test_invalid_review_configuration_stops_before_refresh_or_api_work(self):
        with patch.object(refresh_estimator, "parse_args"), \
             patch.object(refresh_estimator, "load_estimation_review", side_effect=ValueError("Invalid review configuration")), \
             patch.object(refresh_estimator, "load_refresh_inputs") as inputs, \
             patch.object(refresh_estimator, "run") as run:
            with self.assertRaisesRegex(ValueError, "Invalid review configuration"):
                refresh_estimator.main()
            inputs.assert_not_called()
            run.assert_not_called()

    @unittest.skipUnless(shutil.which("node"), "Node is required for browser runtime checks.")
    def test_public_projection_and_browser_keep_review_restriction(self):
        d = document()
        apply_estimation_review(d, {"held": "Staff confirmation is required."})
        public = OptionProjection(d, option_id="o001", permit_type_id="t001", work_class_id="w001", minimum_sample_size=10).project()
        self.assertEqual(public["fees"][0]["estimationReview"], d["fees"][0]["estimationReview"])
        source = (ROOT / "docs/runtime.js").read_text(encoding="utf-8")
        code = source + "\nconst docs=" + json.dumps([d, public]) + ";\n"
        code += """
const results=docs.map(d=>{
  const r=evaluateOption(d).report;
  return {total:r.estimatedTotal,blocked:r.blockedFees.map(f=>f.name).sort(),review:r.requiresStaffReview,
          holds:analyzeOptionCapabilities(d).blockerArchetypes['staff-review']};
});
console.log(JSON.stringify(results));
"""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "review.mjs"
            script.write_text(code, encoding="utf-8")
            result = subprocess.run(["node", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for result in json.loads(result.stdout):
            self.assertEqual(result["total"], 5)
            self.assertEqual(result["blocked"], ["DEPENDENT", "HELD"])
            self.assertTrue(result["review"])
            self.assertEqual(result["holds"], 1)


if __name__ == "__main__":
    unittest.main()
