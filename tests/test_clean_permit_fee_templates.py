import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / "clean_permit_fee_templates.py"
SPEC = importlib.util.spec_from_file_location("clean_permit_fee_templates", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def formula_fee(expression):
    return {
        "feeTemplateFeeID": "fee-formula",
        "feeID": "fee-definition",
        "feeName": "Formula fee",
        "isActive": True,
        "expression": expression,
        "feeSetups": [
            {
                "startDate": "2026-07-01",
                "endDate": "2027-06-30",
                "amount": 1.0,
            }
        ],
    }


class CleanPermitFeeTemplatesTests(unittest.TestCase):
    def test_structural_child_fixed_fee_is_not_conditional_by_relationship_alone(self):
        self.assertEqual(
            MODULE.estimate_mode(
                "Fixed-dollar",
                "Child",
                False,
                False,
            ),
            "fixed",
        )

    def test_structural_child_relationship_does_not_emit_applicability_warning(self):
        warnings = MODULE.fee_warnings(
            "Fixed-dollar",
            "Child",
            False,
            1,
            False,
        )

        self.assertNotIn(
            "Child-template applicability must be evaluated.",
            warnings,
        )

    def test_child_relationship_is_not_a_required_rule_type(self):
        fees = [
            {
                "estimateMode": "fixed",
                "templateRelationship": "Child",
                "applicability": {"hasCondition": False},
                "calculation": {"advancedRules": {}},
            }
        ]

        rules = MODULE.required_rule_types(fees)

        self.assertNotIn("child-template-applicability", rules)
        self.assertIn("fixed", rules)

    def test_fee_expression_is_separate_from_template_applicability_expression(self):
        fee_expression = {
            "expressionType": 3,
            "parameters": [{"expressionType": 7, "value": "2"}],
        }
        template_expression = {
            "expressionType": 9,
            "parameters": [{"expressionType": 7, "value": "1"}],
        }

        normalized = MODULE.normalize_fee(
            formula_fee(fee_expression),
            {"expression": template_expression},
            depth=0,
            path=["template"],
            as_of=date(2026, 9, 15),
        )

        self.assertEqual(normalized["classification"], "Formula-based")
        self.assertEqual(
            normalized["calculation"]["expression"],
            fee_expression,
        )
        self.assertEqual(
            normalized["applicability"]["templateExpression"],
            template_expression,
        )
        self.assertTrue(normalized["applicability"]["hasCondition"])

    def test_fee_expression_alone_does_not_create_applicability_condition(self):
        fee_expression = {
            "expressionType": 2,
            "parameters": [{"expressionType": 7, "value": "340"}],
        }

        normalized = MODULE.normalize_fee(
            formula_fee(fee_expression),
            {},
            depth=0,
            path=["template"],
            as_of=date(2026, 9, 15),
        )

        self.assertEqual(
            normalized["calculation"]["expression"],
            fee_expression,
        )
        self.assertFalse(
            normalized.get("applicability", {}).get("hasCondition", False)
        )
        self.assertNotIn(
            "templateExpression",
            normalized.get("applicability", {}),
        )


if __name__ == "__main__":
    unittest.main()
