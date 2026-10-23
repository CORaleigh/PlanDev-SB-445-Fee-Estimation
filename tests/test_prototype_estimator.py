import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "prototype_estimator.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("prototype_estimator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fee(
    fee_id,
    name,
    *,
    section="configured",
    mode="fixed",
    fixed_amount=None,
    rule=None,
    conditions=None,
    assessment_role=None,
):
    row = {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "section": section,
        "applicability": {
            "status": (
                section if section in {"conditional", "event-contingent"} else "configured"
            ),
            "conditions": list(conditions or []),
        },
        "calculation": {
            "mode": mode,
            "fixedAmount": fixed_amount,
            "rule": rule or {},
        },
    }
    if assessment_role is not None:
        row["assessmentRole"] = assessment_role
    return row


def expression_condition(field, value):
    return {
        "kind": "expression",
        "sourceRule": {
            "isOrGroup": False,
            "childConditions": [
                {
                    "operandValue": field,
                    "operatorID": 1,
                    "compareExpression": {"value": value},
                }
            ],
        },
    }


class PrototypeEstimatorTests(unittest.TestCase):
    def fixture(self):
        return {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "OPT",
                "permitType": "Combo Changeout",
                "workClass": "Change Out",
            },
            "fees": [
                fee("BASE", "Base", fixed_amount=100),
                fee(
                    "OPTIONAL",
                    "Optional electrical",
                    section="conditional",
                    fixed_amount=50,
                    conditions=[expression_condition("EleChoice", "TARGET")],
                ),
                fee(
                    "TECH",
                    "MISC - Technology Surcharge",
                    mode="input",
                    rule={
                        "amount": 4,
                        "roundingType": 1,
                        "roundingValue": 0,
                        "inputs": [
                            {"inputKey": "BASE", "source": "fee"},
                            {"inputKey": "OPTIONAL", "source": "fee"},
                        ],
                    },
                ),
                fee(
                    "WWP",
                    "Working without a Permit (Electrical)",
                    section="conditional",
                    mode="input",
                    conditions=[
                        expression_condition("WorkingwithoutaPermit", True)
                    ],
                    rule={
                        "amount": 100,
                        "roundingType": 1,
                        "roundingValue": 0,
                        "inputs": [{"inputKey": "BASE", "source": "fee"}],
                    },
                ),
                fee("MANUAL", "Manual review", section="manual-review", mode="manual"),
                fee("INACTIVE", "Inactive", section="inactive", mode="unsupported"),
            ],
            "disclosures": [],
        }

    def test_baseline_excludes_conditional_fee_and_keeps_review_separate(self):
        report = MODULE.build_report_model(self.fixture())

        self.assertEqual(report["regularPermitFeesSubtotal"], 104.0)
        self.assertEqual(
            report["workingWithoutPermitAdditionalFeeSubtotal"],
            0.0,
        )
        self.assertEqual(report["subtotal"], 104.0)
        self.assertEqual(
            [item["name"] for item in report["calculatedFees"]],
            ["Base", "MISC - Technology Surcharge"],
        )
        self.assertEqual(
            [item["name"] for item in report["excludedFees"]],
            ["Optional electrical", "Working without a Permit (Electrical)"],
        )
        self.assertEqual(report["staffReviewFees"], [])
        self.assertEqual(
            [item["name"] for item in report["possibleAdditionalFees"]],
            ["Manual review"],
        )
        self.assertEqual(len(report["inactiveFees"]), 1)
        self.assertEqual(report["blockedFees"], [])

    def test_unconditional_manual_fee_is_possible_not_current_review(self):
        report = MODULE.build_report_model(self.fixture())

        self.assertFalse(report["requiresStaffReview"])
        self.assertEqual(
            report["estimateStatus"],
            "estimate-calculated-possible-additions",
        )
        self.assertEqual(
            report["estimateStatusLabel"],
            "Estimate Calculated",
        )
        self.assertEqual(report["staffReviewFees"], [])
        self.assertEqual(
            [item["name"] for item in report["possibleAdditionalFees"]],
            ["Manual review"],
        )
        self.assertEqual(report["estimatedTotal"], 104.0)

    def test_triggered_manual_fee_requires_staff_review(self):
        document = self.fixture()
        document["fees"] = [
            item
            for item in document["fees"]
            if item["feeTemplateFeeId"] != "MANUAL"
        ]
        document["fees"].append(
            fee(
                "MANUAL_CONDITIONAL",
                "Conditional manual fee",
                section="manual-review",
                mode="manual",
                conditions=[expression_condition("NeedsReview", True)],
            )
        )

        report = MODULE.build_report_model(
            document,
            field_values={
                "EleChoice": "OTHER",
                "WorkingwithoutaPermit": False,
                "NeedsReview": True,
            },
        )

        self.assertTrue(report["requiresStaffReview"])
        self.assertEqual(
            [item["name"] for item in report["staffReviewFees"]],
            ["Conditional manual fee"],
        )
        self.assertEqual(report["possibleAdditionalFees"], [])

    def test_clean_report_sets_automatic_estimate_complete(self):
        document = self.fixture()
        document["fees"] = [
            item
            for item in document["fees"]
            if item["feeTemplateFeeId"] != "MANUAL"
        ]

        report = MODULE.build_report_model(document)

        self.assertFalse(report["requiresStaffReview"])
        self.assertEqual(
            report["estimateStatus"],
            "automatic-estimate-complete",
        )
        self.assertEqual(
            report["estimateStatusLabel"],
            "Estimate Ready",
        )
        self.assertEqual(report["staffReviewReasons"], [])
        self.assertEqual(report["estimatedTotal"], 104.0)

    def test_estimate_status_classifier_reports_all_review_reasons(self):
        status = MODULE.classify_estimate_status(
            blocked_fees=[{"name": "Blocked"}],
            unresolved_applicability_fees=[{"name": "Unknown"}],
            staff_review_fees=[{"name": "Manual"}],
        )

        self.assertTrue(status["requiresStaffReview"])
        self.assertEqual(status["code"], "staff-review-required")
        self.assertEqual(
            [item["code"] for item in status["reasons"]],
            [
                "MANUAL_REVIEW_FEES",
                "UNRESOLVED_APPLICABILITY",
                "CALCULATION_BLOCKERS",
            ],
        )

    def test_manual_event_fee_is_not_an_initial_review_item(self):
        document = self.fixture()
        document["fees"] = [
            item
            for item in document["fees"]
            if item["feeTemplateFeeId"] != "MANUAL"
        ]
        document["fees"].append(
            fee(
                "REINSPECT",
                "Residential Electrical Reinspection",
                section="event-contingent",
                mode="manual",
            )
        )

        report = MODULE.build_report_model(document)

        self.assertFalse(report["requiresStaffReview"])
        self.assertEqual(report["staffReviewFees"], [])
        self.assertIn(
            "REINSPECT",
            [
                item["feeTemplateFeeId"]
                for item in report["excludedFees"]
            ],
        )

    def test_explicit_later_event_manual_fee_is_not_initial_review(self):
        document = self.fixture()
        document["fees"] = [
            item
            for item in document["fees"]
            if item["feeTemplateFeeId"] != "MANUAL"
        ]
        document["fees"].append(
            fee(
                "VOID",
                "DSENG - Voiding permit fee - Engineering (ROW)",
                section="manual-review",
                mode="manual",
            )
        )

        report = MODULE.build_report_model(document)

        self.assertFalse(report["requiresStaffReview"])
        self.assertEqual(report["staffReviewFees"], [])
        self.assertIn(
            "VOID",
            [
                item["feeTemplateFeeId"]
                for item in report["excludedFees"]
            ],
        )

    def test_including_conditional_fee_updates_dependency_and_subtotal(self):
        report = MODULE.build_report_model(
            self.fixture(),
            include_fee_names=["Optional electrical"],
        )

        self.assertEqual(report["subtotal"], 156.0)
        self.assertEqual(len(report["calculatedFees"]), 3)
        self.assertEqual(
            [item["name"] for item in report["excludedFees"]],
            ["Working without a Permit (Electrical)"],
        )

    def test_working_without_permit_uses_validated_100_percent_semantics(self):
        report = MODULE.build_report_model(
            self.fixture(),
            include_fee_names=["Working without a Permit (Electrical)"],
        )

        self.assertEqual(report["regularPermitFeesSubtotal"], 104.0)
        self.assertEqual(
            report["workingWithoutPermitAdditionalFeeSubtotal"],
            100.0,
        )
        self.assertEqual(report["estimatedTotal"], 204.0)
        self.assertEqual(
            [item["name"] for item in report["regularPermitFees"]],
            ["Base", "MISC - Technology Surcharge"],
        )
        self.assertEqual(
            [
                item["name"]
                for item in report["workingWithoutPermitAdditionalFees"]
            ],
            ["Working without a Permit (Electrical)"],
        )
        wwp = report["workingWithoutPermitAdditionalFees"][0]
        self.assertEqual(wwp["amount"], 100.0)


    def test_validated_condition_mode_resolves_permit_fields(self):
        report = MODULE.build_report_model(
            self.fixture(),
            field_values={
                "EleChoice": "TARGET",
                "WorkingwithoutaPermit": False,
            },
        )

        self.assertEqual(
            report["scenario"]["applicabilityMode"],
            "validated-condition-rules",
        )
        self.assertEqual(report["subtotal"], 156.0)
        self.assertEqual(
            [item["name"] for item in report["calculatedFees"]],
            ["Base", "Optional electrical", "MISC - Technology Surcharge"],
        )
        self.assertEqual(report["unresolvedApplicabilityFees"], [])

    def test_prototype_field_choices_map_current_picklist_values(self):
        values = MODULE.prototype_field_values(
            "no",
            "yes",
            True,
        )

        self.assertEqual(
            values["Ele_Contractor"],
            "4cdf5136-cd0d-4269-8324-8a7138bda694",
        )
        self.assertEqual(
            values["Mec_Contractor"],
            "ebec88f2-3633-48b7-b903-f5ecd8580d1b",
        )
        self.assertTrue(values["WorkingwithoutaPermit"])

    def test_second_slice_field_choices_use_resolved_picklist_labels(self):
        yes_values = MODULE.second_slice_field_values("yes", False)
        no_values = MODULE.second_slice_field_values("no", True)

        self.assertEqual(
            yes_values["Both_Mech_and_Ele"],
            "11506d22-fd36-40c3-b878-b8c8517ca7bb",
        )
        self.assertFalse(yes_values["WorkingwithoutaPermit"])
        self.assertEqual(
            no_values["Both_Mech_and_Ele"],
            "f91f9b5a-bcd3-4baa-bcec-2404a7199b88",
        )
        self.assertTrue(no_values["WorkingwithoutaPermit"])

    def test_third_slice_field_values_only_set_wwp_flag(self):
        values = MODULE.third_slice_field_values(True)

        self.assertEqual(
            values,
            {"WorkingwithoutaPermit": True},
        )

    def test_dependency_only_role_is_not_counted_as_separate_charge(self):
        baseline_id = "GENERIC-DEPENDENCY"
        document = self.fixture()
        document["fees"].insert(
            0,
            fee(
                baseline_id,
                "Generic calculation dependency",
                fixed_amount=124,
                assessment_role="dependency-only",
            ),
        )

        report = MODULE.build_report_model(document)

        self.assertEqual(report["regularPermitFeesSubtotal"], 104.0)
        self.assertEqual(
            [item["feeTemplateFeeId"] for item in report["dependencyOnlyFees"]],
            [baseline_id],
        )
        self.assertNotIn(
            baseline_id,
            [
                item["feeTemplateFeeId"]
                for item in report["regularPermitFees"]
            ],
        )

    def test_zero_amount_configured_fee_remains_visible(self):
        document = self.fixture()
        document["fees"].insert(
            1,
            fee(
                "ZERO",
                "Configured zero-dollar fee",
                fixed_amount=0,
            ),
        )

        report = MODULE.build_report_model(document)

        zero_rows = [
            item
            for item in report["regularPermitFees"]
            if item["feeTemplateFeeId"] == "ZERO"
        ]
        self.assertEqual(len(zero_rows), 1)
        self.assertEqual(zero_rows[0]["amount"], 0.0)
        self.assertEqual(report["regularPermitFeesSubtotal"], 104.0)

    def test_distinct_same_name_assignments_are_not_collapsed(self):
        document = self.fixture()
        document["fees"].insert(
            1,
            fee(
                "REPEAT_A",
                "Repeated operational fee",
                fixed_amount=25,
            ),
        )
        document["fees"].insert(
            2,
            fee(
                "REPEAT_B",
                "Repeated operational fee",
                fixed_amount=25,
            ),
        )

        report = MODULE.build_report_model(document)

        repeated = [
            item
            for item in report["regularPermitFees"]
            if item["name"] == "Repeated operational fee"
        ]
        self.assertEqual(
            [item["feeTemplateFeeId"] for item in repeated],
            ["REPEAT_A", "REPEAT_B"],
        )
        self.assertEqual(
            [item["amount"] for item in repeated],
            [25.0, 25.0],
        )
        self.assertEqual(report["regularPermitFeesSubtotal"], 154.0)

    def test_repeat_metadata_is_reported_without_multiplying_fee(self):
        document = self.fixture()
        document["fees"][0]["occurrence"] = {
            "status": "available",
            "numerator": 20,
            "denominator": 100,
            "rate": 0.2,
        }
        document["fees"][0]["repeat"] = {
            "repeatable": True,
            "basis": "historical",
            "trigger": "Historical records contain repeat instances",
            "averageInstancesPerChargedFamily": 1.75,
        }

        report = MODULE.build_report_model(document)

        base = next(
            item
            for item in report["regularPermitFees"]
            if item["feeTemplateFeeId"] == "BASE"
        )
        self.assertEqual(
            base["repeat"]["averageInstancesPerChargedFamily"],
            1.75,
        )
        self.assertEqual(base["occurrence"]["rate"], 0.2)
        self.assertEqual(base["amount"], 100.0)
        self.assertEqual(report["regularPermitFeesSubtotal"], 104.0)

        rendered = MODULE.render_html(report)
        self.assertNotIn("<th>Notes</th>", rendered)
        self.assertNotIn(
            "On past permits where this fee was charged",
            rendered,
        )
        self.assertNotIn("1.75 times on average", rendered)
        self.assertNotIn("Repeat behavior", rendered)

    def test_event_repeatability_gets_plain_actionable_note(self):
        item = {
            "repeat": {
                "repeatable": True,
                "basis": "event",
                "trigger": "Additional reinspection event",
                "averageInstancesPerChargedFamily": 1.5,
            }
        }

        self.assertEqual(
            MODULE._repeat_display(item),
            "Another reinspection may result in another fee.",
        )

    def test_staff_report_hides_duplicate_review_name_already_included(self):
        document = self.fixture()
        document["fees"].append(
            fee(
                "TECH_MANUAL",
                "MISC - Technology Surcharge",
                section="manual-review",
                mode="manual",
            )
        )

        report = MODULE.build_report_model(document)
        rendered = MODULE.render_html(report)

        self.assertEqual(
            rendered.count(">Technology Fee<"),
            1,
        )
        self.assertIn("Fees Included in This Estimate", rendered)

    def test_staff_report_deduplicates_review_names(self):
        document = self.fixture()
        document["fees"] = [
            item
            for item in document["fees"]
            if item["feeTemplateFeeId"] != "MANUAL"
        ]
        document["fees"].extend(
            [
                fee(
                    "MANUAL_A",
                    "Residential Zoning Fee",
                    section="manual-review",
                    mode="manual",
                ),
                fee(
                    "MANUAL_B",
                    "Residential Zoning Fee",
                    section="manual-review",
                    mode="manual",
                ),
            ]
        )

        report = MODULE.build_report_model(document)
        rendered = MODULE.render_html(report)

        self.assertEqual(
            rendered.count(">Residential Zoning Fee<"),
            1,
        )
        self.assertIn("Other Fees That May Apply", rendered)
        self.assertNotIn("staff must review 1 item", rendered)

    def test_staff_report_lists_excluded_fees_once_in_plain_language(self):
        report = MODULE.build_report_model(self.fixture())
        rendered = MODULE.render_html(report)

        self.assertIn("Other Fees Not Included in This Estimate", rendered)
        self.assertIn("Optional electrical", rendered)
        self.assertIn(
            "Additional Fee for Work Started Before a Permit (Electrical)",
            rendered,
        )
        self.assertNotIn("Individual assignment records", rendered)
        self.assertNotIn("JSON output", rendered)
        self.assertNotIn("Internal data version", rendered)
        self.assertNotIn("Internal scenario reference", rendered)

    def test_unresolved_later_event_manual_fee_does_not_require_current_review(self):
        document = self.fixture()
        document["fees"] = [
            item
            for item in document["fees"]
            if item["feeTemplateFeeId"] != "MANUAL"
        ]
        document["fees"].append(
            fee(
                "VOID_CONDITIONAL",
                "DSINSP - Voiding Permit Fee - Building",
                section="manual-review",
                mode="manual",
                conditions=[expression_condition("VoidReason", "YES")],
            )
        )

        report = MODULE.build_report_model(
            document,
            field_values={
                "EleChoice": "OTHER",
                "WorkingwithoutaPermit": False,
            },
        )

        self.assertFalse(report["requiresStaffReview"])
        self.assertEqual(report["unresolvedApplicabilityFees"], [])
        self.assertIn(
            "VOID_CONDITIONAL",
            [
                item["feeTemplateFeeId"]
                for item in report["excludedFees"]
            ],
        )

    def test_unresolved_field_revision_fee_is_excluded_as_later_event(self):
        document = self.fixture()
        document["fees"] = [
            item
            for item in document["fees"]
            if item["feeTemplateFeeId"] != "MANUAL"
        ]
        document["fees"].append(
            fee(
                "FIELD_REVISION",
                "FR - Building (Res/Non-Res)",
                section="manual-review",
                mode="manual",
                conditions=[expression_condition("RevisionType", "YES")],
            )
        )

        report = MODULE.build_report_model(
            document,
            field_values={
                "EleChoice": "OTHER",
                "WorkingwithoutaPermit": False,
            },
        )

        self.assertFalse(report["requiresStaffReview"])
        self.assertEqual(report["unresolvedApplicabilityFees"], [])
        self.assertEqual(report["staffReviewFees"], [])
        self.assertEqual(report["possibleAdditionalFees"], [])
        self.assertIn(
            "FIELD_REVISION",
            [
                item["feeTemplateFeeId"]
                for item in report["excludedFees"]
            ],
        )

    def test_revision_comparison_detects_changed_project_inputs(self):
        previous = MODULE.build_report_model(
            self.fixture(),
            field_values={
                "EleChoice": "OTHER",
                "WorkingwithoutaPermit": False,
            },
        )
        current = MODULE.build_report_model(
            self.fixture(),
            field_values={
                "EleChoice": "TARGET",
                "WorkingwithoutaPermit": False,
            },
        )

        comparison = MODULE.compare_estimate_reports(previous, current)

        self.assertTrue(comparison["revisionRequired"])
        self.assertTrue(comparison["materialChangeDetected"])
        self.assertIn(
            "APPLICABILITY_INPUTS_CHANGED",
            [item["code"] for item in comparison["revisionReasons"]],
        )
        self.assertIn(
            "FEE_RESULTS_CHANGED",
            [item["code"] for item in comparison["revisionReasons"]],
        )
        self.assertIn(
            "ESTIMATED_TOTAL_CHANGED",
            [item["code"] for item in comparison["revisionReasons"]],
        )
        self.assertFalse(comparison["revalidationRequired"])

    def test_revision_comparison_treats_snapshot_only_change_as_revalidation(self):
        previous = MODULE.build_report_model(self.fixture())
        current_document = self.fixture()
        current_document["configuration"]["asOfDate"] = "2026-09-18"
        current = MODULE.build_report_model(current_document)

        comparison = MODULE.compare_estimate_reports(previous, current)

        self.assertFalse(comparison["revisionRequired"])
        self.assertFalse(comparison["materialChangeDetected"])
        self.assertTrue(comparison["revalidationRequired"])
        self.assertEqual(
            [item["code"] for item in comparison["revalidationReasons"]],
            ["CONFIGURATION_SNAPSHOT_CHANGED"],
        )

    def test_revision_comparison_ignores_identical_reports(self):
        previous = MODULE.build_report_model(self.fixture())
        current = MODULE.build_report_model(self.fixture())

        comparison = MODULE.compare_estimate_reports(previous, current)

        self.assertFalse(comparison["revisionRequired"])
        self.assertFalse(comparison["materialChangeDetected"])
        self.assertFalse(comparison["revalidationRequired"])
        self.assertEqual(comparison["revisionReasons"], [])
        self.assertEqual(comparison["revalidationReasons"], [])

    def test_unknown_fee_name_fails_closed(self):
        with self.assertRaises(KeyError):
            MODULE.build_report_model(
                self.fixture(),
                include_fee_names=["Does not exist"],
            )

    def test_html_uses_plain_staff_language(self):
        report = MODULE.build_report_model(self.fixture())
        rendered = MODULE.render_html(report)

        self.assertIn("Permit Fee Estimate", rendered)
        self.assertIn("Estimated Initial Permit Fees: $104.00", rendered)
        self.assertIn("Fees Included in This Estimate", rendered)
        self.assertIn("Other Fees That May Apply", rendered)
        self.assertIn("Estimate Calculated", rendered)
        self.assertIn("More Details", rendered)
        self.assertNotIn("Staff-review items", rendered)
        self.assertNotIn("Calculation blockers", rendered)
        self.assertNotIn("Unresolved applicability", rendered)
        self.assertNotIn("<th>Classification</th>", rendered)

    def test_display_names_hide_known_energov_abbreviations(self):
        self.assertEqual(
            MODULE.display_permit_type(
                "DSINSP - Online Only MENR/ELNR Changeout - Combo"
            ),
            "Mechanical / Electrical Change-Out",
        )
        self.assertEqual(
            MODULE.display_fee_name("DSINSP - Change Out [MENR]"),
            "Mechanical Change-Out Permit Fee",
        )
        self.assertEqual(
            MODULE.display_fee_name("MISC - Technology Surcharge"),
            "Technology Fee",
        )


if __name__ == "__main__":
    unittest.main()
