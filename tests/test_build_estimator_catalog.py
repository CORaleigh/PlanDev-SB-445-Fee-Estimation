import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_estimator_catalog.py"
)
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))

SPEC = importlib.util.spec_from_file_location("build_estimator_catalog", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildEstimatorCatalogTests(unittest.TestCase):
    def test_history_count_parser_accepts_integral_nullable_csv_values(self):
        self.assertEqual(MODULE._csv_int("100.0"), 100)
        self.assertEqual(MODULE._csv_int(0), 0)
        self.assertIsNone(MODULE._csv_int(""))
        for value in ("1.5", "NaN", "Infinity", "not a count"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE._csv_int(value)

    def test_field_history_coverage_requires_rows_not_invented_percentages(self):
        doc = {"option": {"permitType": "Permit A", "workClass": "Work A"}}
        controls = [{"key": "Quantity", "displayReady": True, "controlType": "number"},
                    {"key": "Choice", "displayReady": True, "controlType": "select"},
                    {"key": "Hidden", "displayReady": False, "controlType": "select"}]
        index = {("permit a", "work a", "Quantity"): {"valueKind": "numeric", "status": "available"}}
        with patch.object(MODULE, "build_option_ui_schema", return_value={"controls": controls}):
            with self.assertRaisesRegex(ValueError, "Missing field-history rows"):
                MODULE.validate_input_prevalence_coverage(doc, index)
            index[("permit a", "work a", "Choice")] = {"valueKind": "choice", "status": "unavailable"}
            self.assertEqual(MODULE.validate_input_prevalence_coverage(doc, index), 2)
            index[("permit a", "work a", "Choice")]["valueKind"] = "numeric"
            with self.assertRaisesRegex(ValueError, "type changed"):
                MODULE.validate_input_prevalence_coverage(doc, index)

    def test_field_history_distinguishes_zero_usage_from_unavailable(self):
        base = {"PermitType": "Permit A", "WorkClass": "Work A", "DenominatorName": "MATURE_ISSUED_OR_FINALIZED",
                "PermitRows": "10.0", "InputKey": "ZeroUsage", "InputLabel": "Quantity", "SourceKind": "custom-saver",
                "SourceColumn": "Quantity", "ValueKind": "numeric", "PhysicalColumnFound": "1", "PopulatedRows": "10.0",
                "PopulatedPct": "100", "ActiveRows": "0.0", "ActivePct": "0", "ActiveDefinition": "numeric <> 0"}
        missing = {**base, "InputKey": "MissingColumn", "PhysicalColumnFound": "0",
                   "PopulatedRows": "", "PopulatedPct": "", "ActiveRows": "", "ActivePct": ""}
        empty = {**missing, "InputKey": "EmptyCohort", "PhysicalColumnFound": "1", "PermitRows": "0.0"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(base))
                writer.writeheader()
                writer.writerows([base, missing, empty])
            index = MODULE.build_input_prevalence_index([path])
            zero = index[("permit a", "work a", "ZeroUsage")]
            self.assertEqual(zero["status"], "available")
            self.assertEqual(zero["activeRate"], 0)
            for key in ("MissingColumn", "EmptyCohort"):
                self.assertEqual(index[("permit a", "work a", key)]["status"], "unavailable")
                self.assertIsNone(index[("permit a", "work a", key)]["activeRate"])
            for changes in ({"ActiveRows": "11"}, {"ActivePct": "NaN"}, {"ActivePct": "101"}, {"ActiveRows": "0.5"}):
                with self.subTest(changes=changes):
                    with path.open("w", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=list(base))
                        writer.writeheader()
                        writer.writerow({**base, **changes})
                    with self.assertRaises(ValueError):
                        MODULE.build_input_prevalence_index([path])

    def test_csv_source_kinds_match_schema_and_do_not_invent_dates(self):
        schema_path = SCRIPT.parents[1] / "schema" / "estimator-catalog.schema.json"
        source_schema = json.loads(schema_path.read_text(encoding="utf-8"))["$defs"]["source"]
        for kind in ("fixed-fee-behavior", "condition-choice-labels",
                     "direct-input-prevalence", "direct-fee-assessment-evidence"):
            with self.subTest(kind=kind):
                source = MODULE.csv_source_entry(kind, Path("evidence.csv"))
                self.assertIn(source["kind"], source_schema["properties"]["kind"]["enum"])
                self.assertIsNone(source["asOfDate"])
                self.assertEqual(source["name"], "evidence.csv")
                self.assertTrue(set(source).issubset(source_schema["properties"]))

    def test_unit_multiplier_blocks_automatic_fixed_total(self):
        fee = {
            "feeTemplateFeeId": "F1",
            "calculation": {
                "amountStatus": "calculable",
                "fixedAmount": 124,
                "formulaDisplay": None,
            },
            "sources": ["permit-template:T1"],
            "validation": {
                "status": "partially-validated",
                "codes": ["CONFIGURATION_NORMALIZED"],
                "notes": [],
            },
        }
        evidence = {
            ("OPTION", "F1"): {
                "BehaviorStatus": "unit-multiplier-review",
                "DistinctObservedMultipliers": "9",
            }
        }

        actual = MODULE.apply_fixed_fee_behavior(fee, "OPTION", evidence)

        self.assertEqual(actual["calculation"]["amountStatus"], "unsupported")
        self.assertEqual(actual["validation"]["status"], "blocked")
        self.assertIn(
            "FIXED_UNIT_MULTIPLIER_NOT_MAPPED",
            actual["validation"]["codes"],
        )
        self.assertIn("observed multiplier(s): 9", actual["calculation"]["formulaDisplay"])

    def test_build_fee_list_assigns_generic_dependency_only_role(self):
        option = {
            "key": "OPTION",
            "fees": [
                {
                    "feeTemplateFeeId": "HIDDEN",
                    "feeId": "HIDDEN-STANDARD",
                    "name": "Intermediate Rate (Hidden)",
                    "selectable": True,
                    "estimateMode": "fixed",
                    "calculation": {"amount": 25},
                    "applicability": {"hasCondition": False},
                    "warnings": [],
                },
                {
                    "feeTemplateFeeId": "VISIBLE",
                    "feeId": "VISIBLE-STANDARD",
                    "name": "Visible Fee",
                    "selectable": True,
                    "estimateMode": "input-rule-review",
                    "requiresInput": True,
                    "canAutoCalculateAmount": True,
                    "calculation": {
                        "amount": 2,
                        "inputs": [
                            {
                                "inputKey": "HIDDEN",
                                "label": "Hidden dependency",
                                "source": "fee",
                            }
                        ],
                    },
                    "applicability": {"hasCondition": False},
                    "warnings": [],
                },
            ],
        }

        actual = MODULE.build_fee_list(
            option,
            historical_fees=[],
            inspection_index={},
            fixed_behavior_index={},
            period={},
            denominator=100,
        )
        by_id = {
            item["feeTemplateFeeId"]: item
            for item in actual
        }

        self.assertEqual(
            by_id["HIDDEN"]["assessmentRole"],
            "dependency-only",
        )
        self.assertEqual(
            by_id["HIDDEN"]["assessmentEvidence"][
                "referencedByFeeIds"
            ],
            ["VISIBLE"],
        )
        self.assertEqual(
            by_id["VISIBLE"]["assessmentRole"],
            "standalone-or-assessed",
        )

    def test_transform_fee_preserves_fee_type_metadata(self):
        fee = {
            "feeTemplateFeeId": "F1",
            "feeId": "BASE",
            "name": "Percentage fee",
            "description": "Example",
            "feeTypeId": 2,
            "feeType": "2",
            "selectable": True,
            "estimateMode": "fixed",
            "calculation": {"amount": 100},
            "applicability": {},
            "warnings": [],
        }

        actual = MODULE.transform_fee(
            fee,
            history=None,
            period={},
            denominator=0,
            source_kind="permit-template",
        )

        self.assertEqual(actual["feeTypeId"], 2)
        self.assertEqual(actual["feeType"], "2")
        schema = json.loads((SCRIPT.parents[1] / "schema/estimator-catalog.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(set(actual).issubset(schema["$defs"]["fee"]["properties"]))

    def test_structural_child_template_does_not_create_applicability_condition(self):
        fee = {
            "templateRelationship": "Child",
            "applicability": {
                "hasCondition": False,
            },
        }

        actual = MODULE.condition_documents(
            fee,
            source_kind="permit-template",
        )

        self.assertEqual(actual, [])

    def test_structural_child_fixed_fee_is_configured_not_conditional(self):
        fee = {
            "templateRelationship": "Child",
            "selectable": True,
            "estimateMode": "fixed",
            "applicability": {
                "hasCondition": False,
            },
        }

        self.assertEqual(
            MODULE.fee_section(fee, "permit-template"),
            "configured",
        )

    def test_transform_fee_preserves_source_template_provenance(self):
        fee = {
            "feeTemplateFeeId": "F-CHILD",
            "feeId": "BASE",
            "name": "Child template fee",
            "feeTypeId": 2,
            "feeType": "2",
            "sourceTemplateId": "CHILD-TEMPLATE",
            "sourceTemplate": "Child Template",
            "templateRelationship": "Child",
            "selectable": True,
            "estimateMode": "fixed",
            "calculation": {"amount": 100},
            "applicability": {"hasCondition": False},
            "warnings": [],
        }

        actual = MODULE.transform_fee(
            fee,
            history=None,
            period={},
            denominator=0,
            source_kind="permit-template",
        )

        self.assertEqual(actual["sourceTemplateId"], "CHILD-TEMPLATE")
        self.assertEqual(actual["sourceTemplate"], "Child Template")
        self.assertEqual(actual["templateRelationship"], "Child")
        self.assertEqual(actual["section"], "configured")
        self.assertEqual(actual["applicability"]["conditions"], [])

    def test_choice_label_enrichment_preserves_guid_and_adds_label(self):
        rule = {
            "isOrGroup": False,
            "childConditions": [
                {
                    "operandValue": "MeterSize",
                    "className": "EnerGovBusiness.PermitManagement.Permit",
                    "operatorID": 1,
                    "compareExpression": {
                        "value": "004BFA83-E701-47D5-BA88-76FA87B21CC4"
                    },
                }
            ],
        }
        index = {
            "exact": {
                MODULE._choice_key(
                    "MeterSize",
                    "EnerGovBusiness.PermitManagement.Permit",
                    "004BFA83-E701-47D5-BA88-76FA87B21CC4",
                ): {
                    "label": '4"',
                    "source": "CUSTOMFIELDPICKLISTITEM.SVALUE",
                }
            },
            "fallback": {},
        }

        MODULE.enrich_condition_rule_labels(rule, index)

        expression = rule["childConditions"][0]["compareExpression"]
        self.assertEqual(
            expression["value"],
            "004BFA83-E701-47D5-BA88-76FA87B21CC4",
        )
        self.assertEqual(expression["label"], '4"')
        self.assertEqual(
            expression["labelSource"],
            "CUSTOMFIELDPICKLISTITEM.SVALUE",
        )

    def test_choice_label_enrichment_leaves_unmapped_guid_untouched(self):
        rule = {
            "isOrGroup": False,
            "childConditions": [
                {
                    "operandValue": "UnknownChoice",
                    "operatorID": 1,
                    "compareExpression": {
                        "value": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
                    },
                }
            ],
        }

        MODULE.enrich_condition_rule_labels(
            rule,
            {"exact": {}, "fallback": {}},
        )

        expression = rule["childConditions"][0]["compareExpression"]
        self.assertNotIn("label", expression)
        self.assertNotIn("labelSource", expression)

    def test_template_condition_is_preserved_as_expression_rule(self):
        template_rule = {
            "isOrGroup": False,
            "childConditions": [
                {
                    "operandValue": "TemplateField",
                    "operatorID": 1,
                    "compareExpression": {"value": "TARGET"},
                }
            ],
        }
        fee = {
            "templateRelationship": "Direct",
            "applicability": {
                "hasCondition": True,
                "templateCondition": template_rule,
            },
        }

        actual = MODULE.condition_documents(
            fee,
            source_kind="permit-template",
        )

        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["kind"], "expression")
        self.assertEqual(actual[0]["scope"], "template")
        self.assertEqual(actual[0]["sourceRule"], template_rule)

    def test_fee_and_template_conditions_are_both_preserved(self):
        fee_rule = {
            "isOrGroup": False,
            "childConditions": [
                {
                    "operandValue": "FeeField",
                    "operatorID": 1,
                    "compareExpression": {"value": "A"},
                }
            ],
        }
        template_rule = {
            "isOrGroup": False,
            "childConditions": [
                {
                    "operandValue": "TemplateField",
                    "operatorID": 1,
                    "compareExpression": {"value": "B"},
                }
            ],
        }
        fee = {
            "templateRelationship": "Direct",
            "applicability": {
                "hasCondition": True,
                "feeCondition": fee_rule,
                "templateCondition": template_rule,
            },
        }

        actual = MODULE.condition_documents(
            fee,
            source_kind="permit-template",
        )

        self.assertEqual(
            [(item["kind"], item["scope"]) for item in actual],
            [("expression", "fee"), ("expression", "template")],
        )
        self.assertEqual(actual[0]["sourceRule"], fee_rule)
        self.assertEqual(actual[1]["sourceRule"], template_rule)

    def test_template_expression_is_preserved_fail_closed(self):
        template_expression = {
            "expressionType": 2,
            "children": [{"value": "opaque"}],
        }
        fee = {
            "templateRelationship": "Direct",
            "applicability": {
                "hasCondition": True,
                "templateExpression": template_expression,
            },
        }

        actual = MODULE.condition_documents(
            fee,
            source_kind="permit-template",
        )

        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["kind"], "template-expression")
        self.assertEqual(actual[0]["scope"], "template")
        self.assertEqual(actual[0]["sourceRule"], template_expression)
        self.assertFalse(actual[0]["supported"])

    def test_conditional_marker_without_rule_is_explicitly_unresolved(self):
        fee = {
            "templateRelationship": "Direct",
            "applicability": {
                "hasCondition": True,
            },
        }

        actual = MODULE.condition_documents(
            fee,
            source_kind="permit-template",
        )

        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["kind"], "unresolved-condition")

    def test_repeat_document_uses_historical_instances_per_charged_family(self):
        fee = {"name": "Permit fee"}
        history = {
            "overall": {
                "averageInstancesPerChargedFamily": 1.75,
            }
        }

        actual = MODULE.repeat_document(
            fee,
            history,
            source_kind="permit-template",
        )

        self.assertTrue(actual["repeatable"])
        self.assertEqual(actual["basis"], "historical")
        self.assertEqual(
            actual["trigger"],
            "Historical records contain repeat instances",
        )
        self.assertEqual(
            actual["averageInstancesPerChargedFamily"],
            1.75,
        )

    def test_reinspection_repeatability_is_event_based(self):
        actual = MODULE.repeat_document(
            {"name": "Reinspection fee"},
            history=None,
            source_kind="inspection-template",
        )

        self.assertTrue(actual["repeatable"])
        self.assertEqual(actual["basis"], "event")
        self.assertEqual(actual["trigger"], "Additional reinspection event")
        self.assertIsNone(actual["averageInstancesPerChargedFamily"])

    def test_calculation_document_normalizes_input_key_whitespace(self):
        fee = {
            "estimateMode": "input-rule-review",
            "calculation": {
                "amount": 10,
                "inputs": [
                    {
                        "inputKey": " Quantity ",
                        "label": "Quantity",
                        "source": "custom-field",
                    }
                ],
            },
        }

        actual = MODULE.calculation_document(fee)

        self.assertEqual(actual["inputKeys"], ["Quantity"])
        self.assertEqual(
            actual["rule"]["inputs"][0]["inputKey"],
            "Quantity",
        )

    def test_required_input_registry_merges_trimmed_keys(self):
        fees = [
            {
                "feeKey": "permit-template:F1",
                "calculation": {
                    "inputKeys": [" Quantity "],
                    "rule": {
                        "inputs": [
                            {
                                "inputKey": " Quantity ",
                                "label": "Embedded Quantity",
                                "source": "custom-field",
                            }
                        ]
                    },
                },
            }
        ]
        configured = [
            {
                "inputKey": "Quantity",
                "label": "Configured Quantity",
                "source": "custom-field",
            }
        ]

        actual = MODULE.required_input_documents(fees, configured)

        self.assertEqual([item["key"] for item in actual], ["Quantity"])
        self.assertEqual(actual[0]["label"], "Configured Quantity")

    def test_required_inputs_include_embedded_fee_inputs(self):
        fees = [
            {
                "feeKey": "permit-template:F1",
                "calculation": {
                    "inputKeys": ["A", "B"],
                    "rule": {
                        "inputs": [
                            {
                                "inputKey": "A",
                                "label": "Embedded A",
                                "source": "custom-field",
                            },
                            {
                                "inputKey": "B",
                                "label": "Inspection input B",
                                "source": "system",
                            },
                        ]
                    },
                },
            },
            {
                "feeKey": "inspection-template:F2",
                "calculation": {
                    "inputKeys": ["B"],
                    "rule": {
                        "inputs": [
                            {
                                "inputKey": "B",
                                "label": "Inspection input B",
                                "source": "system",
                            }
                        ]
                    },
                },
            },
        ]
        configured = [
            {
                "inputKey": "A",
                "label": "Configured A",
                "source": "custom-field",
            },
            {
                "inputKey": "UNUSED",
                "label": "Unused",
                "source": "custom-field",
            },
        ]

        actual = MODULE.required_input_documents(fees, configured)

        self.assertEqual([item["key"] for item in actual], ["A", "B"])
        self.assertEqual(actual[0]["label"], "Configured A")
        self.assertEqual(
            actual[1]["usedByFeeKeys"],
            ["inspection-template:F2", "permit-template:F1"],
        )


    def test_direct_input_prevalence_csv_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prevalence.csv"
            rows = [
                {
                    "PermitType": "Permit A",
                    "WorkClass": "Work A",
                    "DenominatorName": "MATURE_ISSUED_OR_FINALIZED",
                    "PermitRows": "100",
                    "FieldOrder": "1",
                    "InputKey": "Quantity",
                    "InputLabel": "Quantity",
                    "SourceKind": "custom-saver",
                    "SourceColumn": "Quantity",
                    "ValueKind": "numeric",
                    "PhysicalColumnFound": "1",
                    "PopulatedRows": "80",
                    "PopulatedPct": "80.0000",
                    "ActiveRows": "42",
                    "ActivePct": "42.0000",
                    "ActiveDefinition": "numeric <> 0",
                }
            ]
            with path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=list(rows[0]),
                )
                writer.writeheader()
                writer.writerows(rows)

            index = MODULE.build_input_prevalence_index([path])
            evidence = index[("permit a", "work a", "Quantity")]

            self.assertEqual(evidence["denominator"], 100)
            self.assertEqual(evidence["activeRows"], 42)
            self.assertEqual(evidence["activeRate"], 0.42)
            self.assertEqual(evidence["populatedRate"], 0.8)
            self.assertEqual(
                evidence["basis"],
                "direct-input-prevalence-query",
            )

            option_rows = MODULE.option_input_prevalence(
                {
                    "permitType": "Permit A",
                    "workClass": "Work A",
                },
                index,
            )
            self.assertEqual(len(option_rows), 1)
            self.assertEqual(option_rows[0]["inputKey"], "Quantity")


    def test_fee_assessment_evidence_csv_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assessment.csv"
            rows = [
                {
                    "PermitType": "Permit A",
                    "WorkClass": "Work A",
                    "DenominatorName": "MATURE_ISSUED_OR_FINALIZED",
                    "MaturePermitRows": "2737",
                    "FeeTemplateFeeID": "HIDDEN",
                    "ExpectedName": "Rate (Hidden)",
                    "ExplicitHidden": "1",
                    "CasesDirectlyCharged": "0",
                    "ActiveDirectFeeRows": "0",
                    "PositiveAmountRows": "0",
                }
            ]
            with path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=list(rows[0]),
                )
                writer.writeheader()
                writer.writerows(rows)

            index = MODULE.build_fee_assessment_evidence_index([path])
            evidence = index[("permit a", "work a", "HIDDEN")]

            self.assertEqual(evidence["historicalDenominator"], 2737)
            self.assertEqual(evidence["activeDirectFeeRows"], 0)
            self.assertEqual(
                evidence["basis"],
                "direct-fee-assessment-query",
            )

            option_evidence = MODULE.option_fee_assessment_evidence(
                {
                    "permitType": "Permit A",
                    "workClass": "Work A",
                },
                index,
            )
            self.assertEqual(
                option_evidence["HIDDEN"]["historicalDenominator"],
                2737,
            )

    def test_build_fee_list_uses_imported_assessment_evidence_without_usage_history(self):
        option = {
            "key": "OPTION",
            "permitType": "Permit A",
            "workClass": "Work A",
            "fees": [
                {
                    "feeTemplateFeeId": "HIDDEN",
                    "feeId": "HIDDEN-STANDARD",
                    "name": "Intermediate Rate (Hidden)",
                    "selectable": True,
                    "estimateMode": "fixed",
                    "calculation": {"amount": 25},
                    "applicability": {"hasCondition": False},
                    "warnings": [],
                },
                {
                    "feeTemplateFeeId": "VISIBLE",
                    "feeId": "VISIBLE-STANDARD",
                    "name": "Visible Fee",
                    "selectable": True,
                    "estimateMode": "input-rule-review",
                    "requiresInput": True,
                    "canAutoCalculateAmount": True,
                    "calculation": {
                        "amount": 2,
                        "inputs": [
                            {
                                "inputKey": "HIDDEN",
                                "label": "Hidden dependency",
                                "source": "fee",
                            }
                        ],
                    },
                    "applicability": {"hasCondition": False},
                    "warnings": [],
                },
            ],
        }

        actual = MODULE.build_fee_list(
            option,
            historical_fees=[],
            inspection_index={},
            fixed_behavior_index={},
            period={},
            denominator=0,
            assessment_evidence={
                "HIDDEN": {
                    "historicalDenominator": 2737,
                    "activeDirectFeeRows": 0,
                }
            },
        )
        by_id = {
            item["feeTemplateFeeId"]: item
            for item in actual
        }

        self.assertEqual(
            by_id["HIDDEN"]["assessmentRole"],
            "dependency-only",
        )
        self.assertEqual(
            by_id["HIDDEN"]["assessmentEvidence"][
                "directAssessmentEvidenceSource"
            ],
            "direct-assessment-import",
        )


    def test_fee_assessment_evidence_rejects_positive_rows_with_zero_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assessment.csv"
            rows = [
                {
                    "PermitType": "Permit A",
                    "WorkClass": "Work A",
                    "DenominatorName": "MATURE_ISSUED_OR_FINALIZED",
                    "MaturePermitRows": "0",
                    "FeeTemplateFeeID": "FEE",
                    "ActiveDirectFeeRows": "1",
                }
            ]
            with path.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=list(rows[0]),
                )
                writer.writeheader()
                writer.writerows(rows)

            with self.assertRaises(ValueError):
                MODULE.build_fee_assessment_evidence_index([path])


    def test_validated_dependency_evidence_is_exact_option_scoped(self):
        fee_id = "7728caad-06b5-438f-88b3-e637cdec2b97"

        exact = MODULE.option_fee_assessment_evidence(
            {
                "permitType": "DSINSP - Electrical (Non-Residential)",
                "workClass": "Sub Permit",
            },
            {},
        )
        other = MODULE.option_fee_assessment_evidence(
            {
                "permitType": "DSINSP - Electrical (Non-Residential)",
                "workClass": "Alteration/Repairs",
            },
            {},
        )

        self.assertIn(fee_id, exact)
        self.assertIn(
            "validatedDependencyOnlyBasis",
            exact[fee_id],
        )
        self.assertNotIn(fee_id, other)


    def test_related_active_fee_occurrence_uses_exact_parent_family_metric(self):
        relation = {
            "overall": {
                "parentFamiliesWithRelatedPermit": 80,
                "shareOfEligibleParentFamilies": 0.8,
                "parentFamiliesWithRelatedPermitAndActiveFee": 60,
                "shareOfEligibleParentFamiliesWithRelatedPermitAndActiveFee": 0.6,
            }
        }

        actual = MODULE.related_active_fee_occurrence(
            relation,
            {"basis": "ParentAppliedDate"},
            100,
        )

        self.assertEqual(actual["status"], "available")
        self.assertEqual(actual["numerator"], 60)
        self.assertEqual(actual["denominator"], 100)
        self.assertEqual(actual["rate"], 0.6)
        self.assertIn(
            "at least one active fee",
            actual["denominatorDefinition"],
        )


if __name__ == "__main__":
    unittest.main()
