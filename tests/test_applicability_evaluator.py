import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "applicability_evaluator.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("applicability_evaluator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def expression_condition(field, value, *, is_or=False):
    return {
        "kind": "expression",
        "sourceRule": {
            "isOrGroup": is_or,
            "childConditions": [
                {
                    "operandValue": field,
                    "operatorID": 1,
                    "compareExpression": {"value": value},
                }
            ],
        },
    }


def fee(
    fee_id,
    name,
    *,
    section="configured",
    conditions=None,
    rule=None,
):
    return {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "section": section,
        "applicability": {
            "status": section if section in {"conditional", "event-contingent"} else "configured",
            "conditions": list(conditions or []),
        },
        "calculation": {
            "mode": "input" if rule else "fixed",
            "rule": rule or {},
        },
    }


class ApplicabilityEvaluatorTests(unittest.TestCase):
    def test_guid_equality_condition_is_case_insensitive(self):
        condition = expression_condition(
            "Ele_Contractor",
            "4cdf5136-cd0d-4269-8324-8a7138bda694",
        )

        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {
                    "Ele_Contractor":
                        "4CDF5136-CD0D-4269-8324-8A7138BDA694"
                },
            )
        )

    def test_boolean_condition_accepts_numeric_true(self):
        condition = expression_condition("WorkingwithoutaPermit", True)

        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {"WorkingwithoutaPermit": 1},
            )
        )

    def test_operator_2_checks_not_equal(self):
        condition = {
            "kind": "expression",
            "sourceRule": {
                "isOrGroup": False,
                "childConditions": [
                    {
                        "operandValue": "PermitWorkClassID",
                        "operatorID": 2,
                        "compareExpression": {"value": "EXCLUDED"},
                    }
                ],
            },
        }

        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {"PermitWorkClassID": "OTHER"},
            )
        )
        self.assertFalse(
            MODULE.evaluate_expression_condition(
                condition,
                {"PermitWorkClassID": "EXCLUDED"},
            )
        )

    def test_operator_3_checks_numeric_greater_than(self):
        condition = {
            "kind": "expression",
            "sourceRule": {
                "isOrGroup": False,
                "childConditions": [
                    {
                        "operandValue": "SingleFamily1000to1999",
                        "operatorID": 3,
                        "compareExpression": {"value": 0},
                    }
                ],
            },
        }

        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {"SingleFamily1000to1999": 1},
            )
        )
        self.assertFalse(
            MODULE.evaluate_expression_condition(
                condition,
                {"SingleFamily1000to1999": 0},
            )
        )
        self.assertFalse(
            MODULE.evaluate_expression_condition(
                condition,
                {"SingleFamily1000to1999": -1},
            )
        )

    def test_operator_3_explicit_blank_is_not_greater_than(self):
        condition = {
            "operandValue": "DisturbedAcres",
            "operatorID": 3,
            "compareExpression": {"value": 0},
        }

        self.assertFalse(
            MODULE.evaluate_condition(
                condition,
                {"DisturbedAcres": None},
            )
        )

    def test_operator_3_rejects_nonnumeric_values(self):
        condition = {
            "kind": "expression",
            "sourceRule": {
                "isOrGroup": False,
                "childConditions": [
                    {
                        "operandValue": "Quantity",
                        "operatorID": 3,
                        "compareExpression": {"value": 0},
                    }
                ],
            },
        }

        with self.assertRaises(MODULE.ApplicabilityBlocked) as context:
            MODULE.evaluate_expression_condition(
                condition,
                {"Quantity": "not-a-number"},
            )
        self.assertEqual(
            context.exception.code,
            "INVALID_NUMERIC_CONDITION_VALUE",
        )

    def test_operator_7_checks_blank_as_inverse_of_operator_8(self):
        condition = {
            "kind": "expression",
            "sourceRule": {
                "isOrGroup": False,
                "childConditions": [
                    {
                        "operandValue": "DisturbedAcres",
                        "operatorID": 7,
                        "compareExpression": {"value": 0},
                    }
                ],
            },
        }

        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {"DisturbedAcres": False},
            )
        )
        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {"DisturbedAcres": "   "},
            )
        )
        self.assertFalse(
            MODULE.evaluate_expression_condition(
                condition,
                {"DisturbedAcres": 0},
            )
        )
        self.assertFalse(
            MODULE.evaluate_expression_condition(
                condition,
                {"DisturbedAcres": 1.25},
            )
        )

    def test_operator_8_checks_presence_not_comparison_value(self):
        condition = {
            "kind": "expression",
            "sourceRule": {
                "isOrGroup": False,
                "childConditions": [
                    {
                        "operandValue": "DomesticStubSize",
                        "operatorID": 8,
                        "compareExpression": {
                            "value": "configured-guid-is-not-an-equality-target"
                        },
                    }
                ],
            },
        }

        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {"DomesticStubSize": "different-guid"},
            )
        )
        self.assertTrue(
            MODULE.evaluate_expression_condition(
                condition,
                {"DomesticStubSize": True},
            )
        )
        self.assertFalse(
            MODULE.evaluate_expression_condition(
                condition,
                {"DomesticStubSize": False},
            )
        )
        self.assertFalse(
            MODULE.evaluate_expression_condition(
                condition,
                {"DomesticStubSize": "   "},
            )
        )

    def test_missing_field_fails_closed_and_is_reported(self):
        fees = [
            fee(
                "ELR",
                "Electrical",
                section="conditional",
                conditions=[expression_condition("Ele_Contractor", "TARGET")],
            )
        ]

        mapping, unresolved = MODULE.resolve_fee_applicability(fees, {})

        self.assertFalse(mapping["ELR"])
        self.assertEqual(unresolved[0]["code"], "MISSING_CONDITION_INPUT")

    def test_event_fee_is_excluded_from_initial_estimate(self):
        fees = [
            fee(
                "REINSPECT",
                "Reinspection",
                section="event-contingent",
                conditions=[expression_condition("InspectionStatusID", "FAIL")],
            )
        ]

        mapping, unresolved = MODULE.resolve_fee_applicability(
            fees,
            {"InspectionStatusID": "FAIL"},
        )

        self.assertFalse(mapping["REINSPECT"])
        self.assertEqual(unresolved, [])

    def test_child_template_condition_remains_unresolved(self):
        fees = [
            fee(
                "CHILD",
                "Child surcharge",
                section="conditional",
                conditions=[
                    {
                        "kind": "child-template",
                        "supported": False,
                    }
                ],
            )
        ]

        mapping, unresolved = MODULE.resolve_fee_applicability(fees, {})

        self.assertFalse(mapping["CHILD"])
        self.assertEqual(
            unresolved[0]["code"],
            "UNSUPPORTED_APPLICABILITY_CONDITION",
        )

    def test_manual_review_fee_with_condition_is_evaluated(self):
        fees = [
            fee(
                "MANUAL",
                "Conditional manual fee",
                section="manual-review",
                conditions=[expression_condition("NeedsReview", True)],
            )
        ]

        mapping, unresolved = MODULE.resolve_fee_applicability(
            fees,
            {"NeedsReview": True},
        )

        self.assertTrue(mapping["MANUAL"])
        self.assertEqual(unresolved, [])

    def test_working_without_permit_requires_conditional_dependency(self):
        fees = [
            fee(
                "ELR",
                "Electrical base",
                section="conditional",
                conditions=[expression_condition("Ele_Contractor", "TARGET")],
            ),
            fee(
                "WWP",
                "Working without a Permit (Electrical)",
                section="conditional",
                conditions=[
                    expression_condition("WorkingwithoutaPermit", True)
                ],
                rule={
                    "amount": 100,
                    "inputs": [
                        {
                            "inputKey": "ELR",
                            "source": "fee",
                        }
                    ],
                },
            ),
        ]

        mapping, unresolved = MODULE.resolve_fee_applicability(
            fees,
            {
                "Ele_Contractor": "OTHER",
                "WorkingwithoutaPermit": True,
            },
        )

        self.assertFalse(mapping["ELR"])
        self.assertFalse(mapping["WWP"])
        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
