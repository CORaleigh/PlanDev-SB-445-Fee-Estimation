import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / "formula_fee_calculation.py"
SPEC = importlib.util.spec_from_file_location("formula_fee_calculation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fee_input(input_id, fee_id):
    return {
        "feeInputId": input_id,
        "inputKey": fee_id,
        "label": fee_id,
        "source": "fee",
    }


def dependency(fee_id, name):
    return {"feeTemplateFeeId": fee_id, "name": name}


def formula_fee(expression_type=3, name="ROW - (P) STREET LANE - Major LF"):
    expression = None if expression_type is None else {"expressionType": expression_type}
    rule = {
        "inputs": [
            fee_input("rate-input", "rate"),
            fee_input("days-input", "days"),
        ]
    }
    if expression is not None:
        rule["expression"] = expression
    return {
        "feeTemplateFeeId": "formula",
        "name": name,
        "calculation": {"mode": "formula", "rule": rule},
    }




def tree_formula_fee(
    fee_id="17133e4e-ef4e-4ed9-af59-6c4810bfcba1",
    first_id="c64859af-2ace-4e44-9945-35a850bc4f16",
    second_id="67708793-e39f-4507-92ae-037db95f2b1b",
    amount=1296,
):
    return {
        "feeTemplateFeeId": fee_id,
        "name": "UF - Tree Conservation 10% Fee (All Zones)",
        "calculation": {
            "mode": "formula",
            "rule": {
                "amount": amount,
                "inputs": [
                    fee_input("tree-first", first_id),
                    fee_input("tree-second", second_id),
                ],
                "expression": {"expressionType": 5},
            },
        },
    }

class FormulaFeeCalculationTests(unittest.TestCase):
    def setUp(self):
        self.by_id = {
            "rate": dependency(
                "rate", "ROW - Street Lane - (P) Major Rate (Hidden)"
            ),
            "days": dependency("days", "ROW - Occupancy Global Days (Hidden)"),
        }

    def test_row_expression_type_3_multiplies_raw_dependency_values(self):
        actual = MODULE.calculate_formula_amount(
            formula_fee(),
            {"rate": 64.8, "days": 32},
            self.by_id,
        )
        self.assertAlmostEqual(actual["amount"], 2073.6)
        self.assertEqual(actual["operation"], "multiply")
        self.assertEqual(actual["expressionType"], 3)

    def test_existing_r3_row_shape_without_expression_is_supported(self):
        actual = MODULE.calculate_formula_amount(
            formula_fee(expression_type=None),
            {"rate": 94.7, "days": 5},
            self.by_id,
        )
        self.assertAlmostEqual(actual["amount"], 473.5)
        self.assertIsNone(actual["expressionType"])


    def test_tree_conservation_expression_type_5_uses_cent_rounded_minimum(self):
        first_id = "c64859af-2ace-4e44-9945-35a850bc4f16"
        second_id = "67708793-e39f-4507-92ae-037db95f2b1b"
        by_id = {
            first_id: dependency(first_id, "PRCR - HIDDEN FEE NSA 10% [All Other Zones]"),
            second_id: dependency(second_id, "PRCR - HIDDEN FEE TCA"),
        }

        actual = MODULE.calculate_formula_amount(
            tree_formula_fee(),
            {
                first_id: 3.719,
                second_id: 3.79,
            },
            by_id,
        )

        self.assertEqual(actual["operation"], "minimum-cent-rounded-times-rate")
        self.assertEqual(actual["expressionType"], 5)
        self.assertEqual(actual["baseValue"], 3.72)
        self.assertEqual(actual["configuredRate"], 1296.0)
        self.assertAlmostEqual(actual["amount"], 4821.12)

    def test_unvalidated_expression_type_5_assignment_remains_blocked(self):
        fee = tree_formula_fee(fee_id="unknown-tree-formula")
        by_id = {
            "c64859af-2ace-4e44-9945-35a850bc4f16": dependency(
                "c64859af-2ace-4e44-9945-35a850bc4f16",
                "PRCR - HIDDEN FEE NSA 10% [All Other Zones]",
            ),
            "67708793-e39f-4507-92ae-037db95f2b1b": dependency(
                "67708793-e39f-4507-92ae-037db95f2b1b",
                "PRCR - HIDDEN FEE TCA",
            ),
        }
        with self.assertRaises(MODULE.FormulaCalculationUnsupported):
            MODULE.calculate_formula_amount(
                fee,
                {
                    "c64859af-2ace-4e44-9945-35a850bc4f16": 3.719,
                    "67708793-e39f-4507-92ae-037db95f2b1b": 3.79,
                },
                by_id,
            )

    def test_pump_station_expression_type_7_uses_configured_fixed_amount(self):
        fee = {
            "feeTemplateFeeId": "1484297c-4e7f-0dab-46c4-f72ef0863990",
            "name": "PU - Pump Station Inspection Fee",
            "calculation": {
                "mode": "formula",
                "rule": {
                    "amount": 3000,
                    "inputs": [],
                    "expression": {"expressionType": 7},
                },
            },
        }

        actual = MODULE.calculate_formula_amount(
            fee,
            {},
            {},
        )

        self.assertEqual(actual["operation"], "validated-fixed-formula")
        self.assertEqual(actual["expressionType"], 7)
        self.assertEqual(actual["amount"], 3000.0)
        self.assertEqual(actual["configuredRate"], 3000.0)
        self.assertTrue(actual["validatedFixedBehavior"])

    def test_unknown_expression_type_7_formula_remains_blocked(self):
        fee = {
            "feeTemplateFeeId": "unknown-expression-7",
            "name": "Unknown expression type 7 fee",
            "calculation": {
                "mode": "formula",
                "rule": {
                    "amount": 3000,
                    "inputs": [],
                    "expression": {"expressionType": 7},
                },
            },
        }

        with self.assertRaises(MODULE.FormulaCalculationUnsupported):
            MODULE.calculate_formula_amount(
                fee,
                {},
                {},
            )

    def test_other_expression_type_remains_blocked(self):
        with self.assertRaises(MODULE.FormulaCalculationUnsupported):
            MODULE.calculate_formula_amount(
                formula_fee(expression_type=5),
                {"rate": 39, "days": 5},
                self.by_id,
            )

    def test_non_row_formula_remains_blocked(self):
        with self.assertRaises(MODULE.FormulaCalculationUnsupported):
            MODULE.calculate_formula_amount(
                formula_fee(name="UF - Tree Conservation Fee"),
                {"rate": 39, "days": 5},
                self.by_id,
            )

    def test_wrong_dependency_shape_remains_blocked(self):
        bad_by_id = {
            "rate": dependency("rate", "Some Hidden Fee"),
            "days": dependency("days", "Another Hidden Fee"),
        }
        with self.assertRaises(MODULE.FormulaCalculationUnsupported):
            MODULE.calculate_formula_amount(
                formula_fee(),
                {"rate": 39, "days": 5},
                bad_by_id,
            )


if __name__ == "__main__":
    unittest.main()
