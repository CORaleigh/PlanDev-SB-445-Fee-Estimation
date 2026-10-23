import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_estimator_catalog as catalog
import clean_permit_fee_templates as cleaner
import compare_fee_template_snapshots as comparator
from option_runtime import compile_option_schema, evaluate_option
from option_ui_schema import build_option_ui_schema
from project_public_catalog import OptionProjection


def raw_option():
    fees = []
    for key, amount, value in [("base", 5, None), ("current", 10, "CURRENT-1"), ("other", 20, "OTHER-1")]:
        fee = {
            "feeTemplateFeeID": key, "feeID": key, "feeName": key,
            "active": True, "feeSetups": [{"amount": amount}],
        }
        if value:
            fee["condition"] = {
                "isOrGroup": False,
                "childConditions": [{
                    "operandTypeID": 3, "operandValue": "Zone",
                    "operandFriendlyName": "Zone", "operatorID": 1,
                    "compareExpression": {"value": value, "label": "Zone 1"},
                }],
            }
        fees.append(fee)
    return {
        "PermitTypeID": "TYPE", "PermitType": "Generic Permit",
        "WorkClassID": "WORK", "WorkClass": "Generic Work",
        "Template": {
            "feeTemplate": {"feeTemplateID": "TEMPLATE", "fees": fees},
            "customFieldsLayout": {
                "customFieldLayoutID": "CURRENT-LAYOUT",
                "customFields": [{
                    "fieldName": "Zone", "customFieldID": "CURRENT-FIELD",
                    "label": "Zone", "isActive": True, "isPickList": True,
                    "allowMultipleSelections": False,
                    "pickListItems": [
                        {"customFieldPickListItemID": "CURRENT-1", "value": "Zone 1", "retire": False},
                        {"customFieldPickListItemID": "CURRENT-2", "value": "Zone 2", "retire": False},
                        {"customFieldPickListItemID": "RETIRED", "value": "Old zone", "retire": True},
                    ],
                }],
            },
        },
    }


def normalized(raw):
    return cleaner.clean_record(raw, date(2026, 10, 14), defaultdict(set))


def internal_document(raw=None):
    option = normalized(raw or raw_option())
    disclosures = json.loads((ROOT / "schema/disclosure-codes.json").read_text(encoding="utf-8"))
    return catalog.build_option_document(
        option, None, {}, {}, {}, catalog.disclosure_index(disclosures),
        {"asOfDate": "2026-10-14", "historicalPeriod": {"startDate": "2024-01-01", "endDate": "2026-09-15"}},
        "test", "2026-10-22T00:00:00Z",
    )


class ChoiceLayoutContextTests(unittest.TestCase):
    def test_normalization_and_catalog_preserve_selected_choices(self):
        option = normalized(raw_option())
        choices = option["choiceContext"]["fields"][0]["choices"]
        self.assertEqual([c["value"] for c in choices], ["CURRENT-1", "CURRENT-2"])
        document = internal_document()
        self.assertEqual(document["choiceContext"], option["choiceContext"])
        field = compile_option_schema(document)["fields"][0]
        self.assertEqual(field["choices"], choices)
        self.assertEqual(evaluate_option(document, {"Zone": "CURRENT-1"})["report"]["estimatedTotal"], 15)
        self.assertEqual(evaluate_option(document, {"Zone": "CURRENT-2"})["report"]["estimatedTotal"], 5)

    def test_values_from_other_layouts_and_retired_values_are_rejected(self):
        document = internal_document()
        for value in ["OTHER-1", "RETIRED", "Zone 1"]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "selected form"):
                evaluate_option(document, {"Zone": value})

    def test_unreferenced_blank_choices_are_omitted_without_assuming_a_value(self):
        for label in [None, "", "  "]:
            with self.subTest(label=label):
                raw = raw_option()
                raw["Template"]["customFieldsLayout"]["customFields"][0]["pickListItems"].append({
                    "customFieldPickListItemID": "UNLABELED", "value": label, "retire": False,
                })
                document = internal_document(raw)
                self.assertEqual(len(document["choiceContext"]["fields"][0]["choices"]), 3)
                ui = build_option_ui_schema(document)
                self.assertTrue(ui["displayReady"])
                self.assertEqual([c["value"] for c in ui["controls"][0]["choices"]], ["CURRENT-1", "CURRENT-2"])
                self.assertEqual(evaluate_option(document, {"Zone": "CURRENT-1"})["report"]["estimatedTotal"], 15)
                report = evaluate_option(document)["report"]
                self.assertEqual(report["estimatedTotal"], 5)
                self.assertTrue(report["requiresStaffReview"])
                with self.assertRaisesRegex(ValueError, "selected form"):
                    evaluate_option(document, {"Zone": "UNLABELED"})

    def test_referenced_blank_choices_still_block_display(self):
        raw = raw_option()
        raw["Template"]["customFieldsLayout"]["customFields"][0]["pickListItems"].append({
            "customFieldPickListItemID": "UNLABELED", "value": "", "retire": False,
        })
        for section in ["conditional", "manual-review", "event-contingent", "inactive"]:
            with self.subTest(section=section):
                document = internal_document(raw)
                fee = next(f for f in document["fees"] if f["name"] == "other")
                fee["section"] = section
                group = fee["applicability"]["conditions"][0]["sourceRule"]
                group["childConditions"][0]["compareExpression"] = {"value": "UNLABELED", "label": ""}
                group["childConditionGroups"] = [{"isOrGroup": False, "childConditions": group.pop("childConditions")}]
                ui = build_option_ui_schema(document)
                self.assertFalse(ui["displayReady"])
                self.assertEqual(ui["uiBlockers"][0]["code"], "CHOICE_LABELS_NOT_DISPLAY_READY")
                self.assertIn("UNLABELED", [c["value"] for c in ui["controls"][0]["choices"]])

    def test_missing_or_ambiguous_layout_stays_subject_to_review(self):
        for variant in ["absent", "duplicate-field", "inactive", "multiple", "no-layout-id", "missing-layout"]:
            with self.subTest(variant=variant):
                raw = raw_option()
                layout = raw["Template"]["customFieldsLayout"]
                field = layout["customFields"][0]
                if variant == "absent": layout["customFields"] = []
                elif variant == "duplicate-field": layout["customFields"].append(copy.deepcopy(field))
                elif variant == "inactive": field["isActive"] = False
                elif variant == "multiple": field["allowMultipleSelections"] = True
                elif variant == "missing-layout": del raw["Template"]["customFieldsLayout"]
                else: layout["customFieldLayoutID"] = None
                document = internal_document(raw)
                ui = build_option_ui_schema(document)
                self.assertFalse(ui["controls"][0]["displayReady"])
                self.assertEqual(ui["uiBlockers"][0]["code"], "CHOICE_CONTEXT_NOT_AVAILABLE")
                report = evaluate_option(document)["report"]
                self.assertEqual(report["estimatedTotal"], 5)
                self.assertTrue(report["requiresStaffReview"])
                with self.assertRaisesRegex(ValueError, "selected form"):
                    evaluate_option(document, {"Zone": "CURRENT-1"})

    def test_layout_changes_require_refresh_review(self):
        before = normalized(raw_option())
        after = copy.deepcopy(before)
        after["choiceContext"]["fields"][0]["choices"].pop()
        changes = comparator.compare_option(before, after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["classification"], "requires-revalidation")
        self.assertEqual(changes[0]["changes"][0]["path"], "choiceContext")

    def test_inequality_compares_values_not_labels(self):
        raw = raw_option()
        raw["Template"]["feeTemplate"]["fees"][2]["condition"]["childConditions"][0]["operatorID"] = 2
        document = internal_document(raw)
        self.assertEqual(evaluate_option(document, {"Zone": "CURRENT-1"})["report"]["estimatedTotal"], 35)

    @unittest.skipUnless(shutil.which("node"), "Node is required for browser parity.")
    def test_browser_internal_and_public_choice_values_match_python(self):
        raw = raw_option()
        raw["Template"]["customFieldsLayout"]["customFields"][0]["pickListItems"].append({
            "customFieldPickListItemID": "UNLABELED", "value": "", "retire": False,
        })
        document = internal_document(raw)
        public = OptionProjection(
            document, option_id="o001", permit_type_id="t001",
            work_class_id="w001", minimum_sample_size=10,
        ).project()
        control = public["ui"]["controls"][0]
        self.assertEqual([c["label"] for c in control["choices"]], ["Zone 1", "Zone 2"])
        self.assertNotIn("CURRENT-FIELD", json.dumps(public))
        self.assertNotIn("CURRENT-LAYOUT", json.dumps(public))
        runtime = (ROOT / "docs/runtime.js").read_text(encoding="utf-8")
        code = runtime + "\nconst internal = " + json.dumps(document) + ";\n"
        code += "const published = " + json.dumps(public) + ";\n"
        code += """
const selected = published.ui.controls[0];
const allowed = selected.choices.find(c => c.label === 'Zone 1').value;
const a = evaluateOption(internal, {Zone: 'CURRENT-1'}).report;
const b = evaluateOption(published, {[selected.key]: allowed}).report;
const rejected = [];
for (const [doc, values] of [
  [internal, {Zone: 'OTHER-1'}],
  [internal, {Zone: 'RETIRED'}],
  [internal, {Zone: 'UNLABELED'}],
  [published, {[selected.key]: 'not-a-choice'}]
]) {
  try { evaluateOption(doc, values); rejected.push(false); }
  catch { rejected.push(true); }
}
internal.fees.find(f => f.name === 'other').applicability.conditions[0].sourceRule.childConditions[0].operatorID = 2;
const inequalityTotal = evaluateOption(internal, {Zone: 'CURRENT-1'}).report.estimatedTotal;
const referencedBlank = structuredClone(internal);
const manualFee = referencedBlank.fees.find(f => f.name === 'other');
manualFee.section = 'manual-review';
const group = manualFee.applicability.conditions[0].sourceRule;
group.childConditions[0].compareExpression = {value: 'UNLABELED', label: ''};
group.childConditionGroups = [{isOrGroup: false, childConditions: group.childConditions}];
group.childConditions = [];
const blocked = buildOptionUiSchema(referencedBlank);
console.log(JSON.stringify({
  internalTotal: a.estimatedTotal, publicTotal: b.estimatedTotal,
  internalFees: a.calculatedFees.map(f => f.name).sort(), rejected, inequalityTotal,
  referencedBlankBlocked: !blocked.displayReady && blocked.uiBlockers[0].code === 'CHOICE_LABELS_NOT_DISPLAY_READY'
}));
"""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "choices.mjs"
            script.write_text(code, encoding="utf-8")
            result = subprocess.run(["node", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = json.loads(result.stdout)
        self.assertEqual(result["internalTotal"], 15)
        self.assertEqual(result["publicTotal"], 15)
        self.assertEqual(result["internalFees"], ["base", "current"])
        self.assertEqual(result["rejected"], [True, True, True, True])
        self.assertEqual(result["inequalityTotal"], 35)
        self.assertTrue(result["referencedBlankBlocked"])


if __name__ == "__main__":
    unittest.main()
