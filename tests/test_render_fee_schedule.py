import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_fee_schedule import DRAFT, Schedule, load_option, main, money, render_html


def fee(key, section="configured", mode="fixed", name=None):
    return {
        "feeTemplateFeeId": key, "feeId": key, "name": name or key, "section": section,
        "applicability": {"status": section, "conditions": []},
        "calculation": {"mode": mode, "fixedAmount": 123, "rule": {"amount": 123, "inputs": []}},
        "validation": {"status": "unvalidated", "codes": [], "notes": []},
    }


def document():
    return {
        "schemaVersion": "1.0",
        "configuration": {"asOfDate": "2026-10-14", "historicalPeriod": {}},
        "option": {"optionKey": "option", "permitType": "Permit", "workClass": "Work"},
        "fees": [fee("BASE")], "requiredInputs": [], "relatedPermits": [], "disclosures": [],
    }


class FeeScheduleTests(unittest.TestCase):
    def test_preserves_configured_rates_smaller_than_one_cent(self):
        self.assertEqual(money(0.0021), "$0.0021")
        self.assertEqual(money(0.0001), "$0.0001")
        self.assertEqual(money(1000), "$1,000.00")
        doc = document()
        tier = fee("TIER", mode="tiered")
        tier["calculation"]["rule"]["tiers"] = [dict(
            lowerLimit=0, upperLimit=1000, amount=5,
            additionalAmount=0.0021, additionalUnit=1, over=0,
        )]
        doc["fees"] = [tier]
        method = Schedule(doc).project()["primaryFees"][0]["method"]
        self.assertIn("0 to 1,000: $5.00 plus $0.0021 per 1 units above 0", method)

    def test_separates_manual_held_events_components_and_inactive_without_a_total(self):
        doc = document()
        component = fee("COMPONENT")
        component["assessmentRole"] = "dependency-only"
        held = fee("HELD")
        held["estimationReview"] = "Staff must confirm assessment."
        doc["fees"] += [fee("MANUAL", "manual-review", "manual"), held,
                        fee("EVENT", "event-contingent"), fee("LATER", name="Reinspection Fee"),
                        component, fee("OFF", "inactive"), fee("UNKNOWN", mode="unsupported")]
        before = copy.deepcopy(doc)
        model = Schedule(doc).project()
        self.assertEqual([r["name"] for r in model["primaryFees"]], ["BASE"])
        self.assertEqual(len(model["additionalFees"]), 5)
        self.assertEqual([r["name"] for r in model["calculationComponents"]], ["COMPONENT"])
        self.assertEqual(model["inactiveFeesOmitted"], 1)
        self.assertEqual(model["status"], DRAFT)
        self.assertNotIn("total", model)
        self.assertEqual(doc, before)

    def test_percentage_bounds_rounding_and_source_tiers_are_described_not_evaluated(self):
        doc = document()
        percent = fee("PERCENT", "input-required", "input", "MISC - Technology Surcharge")
        percent["calculation"].update(minimumAmount=5, maximumAmount=100)
        percent["calculation"]["rule"].update(amount=4, roundingType=2, roundingValue=1,
                                             inputs=[{"inputKey": "BASE", "source": "fee"}])
        tier = fee("TIER", mode="tiered")
        tier["calculation"]["rule"]["tiers"] = [dict(lowerLimit=0, upperLimit=150, unlimited=False,
                                                       amount=65, additionalAmount=0)]
        doc["fees"] += [percent, tier]
        methods = Schedule(doc).project()["primaryFees"]
        self.assertIn("4% of F001", methods[1]["method"])
        self.assertIn("Minimum: $5.00", methods[1]["method"])
        self.assertIn("Maximum: $100.00", methods[1]["method"])
        self.assertIn("Round up to a whole dollar.", methods[1]["method"])
        self.assertIn("0 to 150: $65.00", methods[2]["method"])

    def test_conditions_preserve_groups_and_reject_unavailable_stored_choices(self):
        schedule = Schedule(document())
        schedule.fields["Zone"] = {"displayLabel": "Zone", "displayReady": True,
                                   "choices": [{"value": "selected", "label": "Zone 1"}]}
        def condition(value):
            return dict(operandValue="Zone", operatorID=1,
                        compareExpression={"expressionType": 7, "value": value, "label": "Zone 1"})
        self.assertEqual(schedule.condition(condition("selected")), "Zone is Zone 1")
        self.assertIn("staff review required", schedule.condition(condition("other-layout")))
        self.assertNotIn("other-layout", schedule.condition(condition("other-layout")))
        group = {"isOrGroup": True, "childConditions": [condition("selected"), condition("other-layout")]}
        self.assertIn(" OR ", schedule.group(group))
        schedule.fields["Zone"]["displayReady"] = False
        self.assertIn("staff must confirm", schedule.condition(condition("selected")))

    def test_duplicate_fee_inputs_are_not_described_as_a_validated_sum(self):
        doc = document()
        percent = fee("PERCENT", "input-required", "input", "MISC - Technology Surcharge")
        percent["calculation"]["rule"].update(amount=4, inputs=[{"inputKey": "BASE", "source": "fee"}] * 2)
        doc["fees"].append(percent)
        lines = Schedule(doc).project()["primaryFees"][1]["method"]
        self.assertIn("Staff must confirm how the inputs are combined.", lines)
        self.assertFalse(any("% of sum" in line for line in lines))

    def test_related_history_is_informational_rounded_and_suppressed_for_small_samples(self):
        doc = document()
        for denominator in (100, 5):
            doc["relatedPermits"].append({
                "permitType": "Related", "workClass": str(denominator), "fees": [],
                "activeFeeOccurrence": {"status": "available", "denominator": denominator, "rate": 0.23456},
            })
        related_fee = fee("RELATED")
        related_fee["occurrence"] = {"status": "available", "denominator": 100, "rate": 0.004}
        doc["relatedPermits"][0]["fees"] = [related_fee]
        model = Schedule(doc).project()
        self.assertEqual(model["relatedPermits"][0]["history"], "23% of similar past projects")
        self.assertIn("Limited history", model["relatedPermits"][1]["history"])
        self.assertEqual(len(model["primaryFees"]), 1)
        self.assertNotIn("denominator", json.dumps(model["relatedPermits"]))
        self.assertEqual(model["relatedPermits"][0]["fees"][0]["history"],
                         "Less than 1%")
        self.assertEqual(model["relatedPermits"][0]["feeHistoryBasis"], "Past projects containing this related permit")

    def test_schedule_does_not_call_a_project_evaluator(self):
        with patch("input_fee_evaluator.evaluate_fee_graph", side_effect=AssertionError("fee evaluation")), \
             patch("formula_fee_calculation.calculate_formula_amount", side_effect=AssertionError("formula evaluation")), \
             patch("applicability_evaluator.resolve_fee_applicability", side_effect=AssertionError("applicability evaluation")), \
             patch("prototype_estimator.build_report_model", side_effect=AssertionError("estimate evaluation")):
            self.assertEqual(Schedule(document()).project()["primaryFees"][0]["method"], ["Fixed fee: $123.00"])

    def test_html_is_escaped_no_em_dash_and_no_external_resources(self):
        doc = document()
        doc["fees"][0]["name"] = "<script>alert(1)</script>\u2014Fee"
        model = Schedule(doc).project()
        page = render_html(model)
        self.assertNotIn("<script>", page)
        self.assertNotIn("\u2014", page)
        for heading in ("Fee Schedule and Calculation Methods", "Primary Fee Schedule and Methodology",
                        "Additional Fees That May Apply", "Related Permits", "Source and Limitations"):
            self.assertRegex(page, "<h[12]>" + heading + "</h[12]>")
        for header in ("Fee / Reference", "Configured Method", "When It May Apply"):
            self.assertIn("<th>" + header + "</th>", page)
        self.assertIn("Permit / Work: Draft Fee Schedule</title>", page)
        self.assertIn("default-src 'none'", page)
        self.assertNotRegex(page, r'(?:src|href)=["\x27]https?://')

    def test_source_dates_are_an_internal_only_italic_footnote(self):
        doc = document()
        doc["configuration"] = {"asOfDate": "2024-02-01", "historicalPeriod": {
            "startDate": "2020-01-01", "endDate": "2024-01-01",
        }}
        external = Schedule(doc).project()
        self.assertNotIn("configurationDate", external)
        self.assertNotIn("historicalPeriod", external)
        self.assertNotIn("2024-02-01", render_html(external))
        internal = Schedule(doc, internal=True).project()
        page = render_html(internal)
        self.assertIn("<strong>Internal Draft: Staff Review Pending</strong>", page)
        marker = '<p class="internal-note"><em>'
        self.assertIn(marker, page)
        self.assertGreater(page.index(marker), page.index("Related Permits"))
        self.assertNotIn("2024-02-01", page[:page.index(marker)])
        self.assertIn("Fee configuration as of 2024-02-01", page[page.index(marker):])
        self.assertIn("Past projects reviewed: 2020-01-01 to 2024-01-01", page)
        internal["internal"] = False
        self.assertNotIn(marker, render_html(internal))
        self.assertNotIn("2024-02-01", render_html(internal))

    def test_internal_source_contract_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "options").mkdir()
            doc = document()
            source = root / "options/one.json"
            source.write_text(json.dumps(doc), encoding="utf-8")
            manifest = {"schemaVersion": "1.0", "configurationAsOfDate": "2026-10-14",
                        "options": [{"permitType": "Permit", "workClass": "Work", "optionKey": "option", "path": "options/one.json"}]}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(load_option(root, "Permit", "Work")["permitType"], "Permit")
            for changed in ({**doc, "option": {**doc["option"], "optionKey": "wrong"}},
                            {**doc, "configuration": {"asOfDate": "2026-09-24"}}):
                source.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_option(root, "Permit", "Work")
            source.write_text(json.dumps(doc), encoding="utf-8")
            with patch.object(sys, "argv", ["render", str(root), "--permit-type", "Permit", "--work-class", "Work",
                                            "--output-dir", str(root)]):
                with self.assertRaises(FileExistsError):
                    main()
            changed = {**manifest, "schemaVersion": "public-1.0"}
            (root / "manifest.json").write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_option(root, "Permit", "Work")


if __name__ == "__main__":
    unittest.main()
