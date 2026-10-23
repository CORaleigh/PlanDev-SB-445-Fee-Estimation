import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / "input_fee_evaluator.py"
SPEC = importlib.util.spec_from_file_location("input_fee_evaluator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def input_fee(
    fee_id,
    name,
    inputs,
    rate=1,
    rounding_type=1,
    rounding_value=0,
    section="input-required",
):
    return {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "section": section,
        "calculation": {
            "mode": "input",
            "rule": {
                "amount": rate,
                "minimum": 0,
                "maximum": 0,
                "roundingType": rounding_type,
                "roundingValue": rounding_value,
                "inputs": inputs,
            },
        },
    }


def tiered_fee(fee_id, name, input_key, tiers):
    return {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "section": "input-required",
        "calculation": {
            "mode": "tiered",
            "rule": {
                "minimum": 0,
                "maximum": 0,
                "roundingType": 1,
                "roundingValue": 0,
                "inputs": [custom_input(input_key)],
                "tiers": tiers,
            },
        },
    }


def fixed_fee(fee_id, name, amount):
    return {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "section": "configured",
        "calculation": {
            "mode": "fixed",
            "fixedAmount": amount,
        },
    }


def custom_input(key):
    return {
        "feeInputId": f"input-{key}",
        "inputKey": key,
        "label": key,
        "source": "custom-field",
    }


def fee_input(fee_input_id, fee_id):
    return {
        "feeInputId": fee_input_id,
        "inputKey": fee_id,
        "label": fee_id,
        "source": "fee",
        "inputTypeId": 3,
        "calculationInputType": 1,
    }


def total_input():
    return {
        "feeInputId": "total-input",
        "inputKey": "Total",
        "label": "Total of Fees in Template",
        "source": "total",
        "inputTypeId": 4,
        "calculationInputType": 0,
    }


class InputFeeEvaluatorTests(unittest.TestCase):
    def test_land_disturbing_rounds_calculated_result_to_whole_dollar(self):
        fees = [
            input_fee(
                "hidden",
                "Land Disturbing Plan Review Fees [Hidden]",
                [custom_input("DisturbedAcres")],
                rate=250,
                rounding_type=4,
                rounding_value=1,
            ),
            input_fee(
                "visible",
                "Land Disturbing Plan Review Fees",
                [fee_input("wrapper", "hidden")],
                rate=1,
            ),
        ]

        actual = MODULE.evaluate_fee_graph(
            fees,
            ["visible"],
            user_inputs={"DisturbedAcres": 4.59},
        )

        self.assertEqual(actual["visible"]["status"], "calculated")
        self.assertEqual(actual["visible"]["amount"], 1148.0)
        self.assertEqual(actual["visible"]["dependencies"], ["hidden"])

    def test_depth_three_dependency_chain_evaluates_recursively(self):
        fees = [
            input_fee("a", "A", [custom_input("Value")], rate=2),
            input_fee("b", "B", [fee_input("b-a", "a")], rate=3),
            input_fee("c", "C", [fee_input("c-b", "b")], rate=4),
            input_fee("d", "D", [fee_input("d-c", "c")], rate=5),
        ]

        actual = MODULE.evaluate_fee_graph(
            fees,
            ["d"],
            user_inputs={"Value": 2},
        )

        self.assertEqual(actual["d"]["status"], "calculated")
        self.assertEqual(actual["d"]["amount"], 240.0)

    def test_technology_surcharge_sums_distinct_fee_inputs_and_ceilings(self):
        fees = [
            fixed_fee("base-a", "Base A", 100),
            fixed_fee("base-b", "Base B", 23),
            input_fee(
                "tech",
                "MISC - Technology Surcharge",
                [
                    fee_input("tech-a", "base-a"),
                    fee_input("tech-b", "base-b"),
                ],
                rate=4.0,
                rounding_type=2,
                rounding_value=1,
            ),
        ]

        actual = MODULE.evaluate_fee_graph(fees, ["tech"])

        self.assertEqual(actual["tech"]["status"], "calculated")
        self.assertEqual(actual["tech"]["baseValue"], 123.0)
        self.assertEqual(actual["tech"]["configuredRate"], 4.0)
        self.assertEqual(actual["tech"]["rate"], 0.04)
        self.assertEqual(actual["tech"]["amount"], 5.0)

    def test_validated_total_based_surcharge_uses_exact_component_assignments(self):
        plumbing_id = "f09bbc1f-cd45-49f2-81a9-38e4434e08cf"
        electrical_id = "7210288d-2877-477f-9872-3af531dfe12b"
        surcharge_id = "dbeb60d1-9a93-4dee-b002-4fa9f96a6fa6"

        plumbing = fixed_fee(plumbing_id, "DSINSP - Change Out [PLMNR]", 124)
        electrical = fixed_fee(electrical_id, "DSINSP - Change Out [ELNR]", 124)
        electrical["section"] = "conditional"
        surcharge = input_fee(
            surcharge_id,
            "MISC - Technology Surcharge",
            [total_input()],
            rate=4.0,
            rounding_type=2,
            rounding_value=1,
        )
        fees = [plumbing, electrical, surcharge]

        unresolved = MODULE.evaluate_fee_graph(fees, [surcharge_id])
        self.assertEqual(unresolved[surcharge_id]["status"], "blocked")
        self.assertIn(
            MODULE.APPLICABILITY_CODE,
            unresolved[surcharge_id]["validationCodes"],
        )

        excluded = MODULE.evaluate_fee_graph(
            fees,
            [surcharge_id],
            applicability={electrical_id: False},
        )
        self.assertEqual(excluded[surcharge_id]["status"], "calculated")
        self.assertEqual(excluded[surcharge_id]["baseValue"], 124.0)
        self.assertEqual(excluded[surcharge_id]["amount"], 5.0)
        self.assertEqual(
            excluded[surcharge_id]["dependencies"],
            [plumbing_id, electrical_id],
        )

        included = MODULE.evaluate_fee_graph(
            fees,
            [surcharge_id],
            applicability={electrical_id: True},
        )
        self.assertEqual(included[surcharge_id]["baseValue"], 248.0)
        self.assertEqual(included[surcharge_id]["amount"], 10.0)

    def test_validated_fee_based_surcharge_uses_xref_component_whitelist(self):
        elnr_id = "2348ff00-df4b-481e-94cb-310537f91cbf"
        menr_id = "794bbf57-c1a6-41da-90fc-6d0d7bfed3cb"
        surcharge_id = "0f671a9d-554d-433c-a0fe-010732422b56"

        elnr = fixed_fee(elnr_id, "DSINSP - Change Out [ELNR]", 124)
        elnr["section"] = "conditional"
        menr = fixed_fee(menr_id, "DSINSP - Change Out [MENR]", 248)
        unrelated = {
            "feeTemplateFeeId": "manual-unrelated",
            "name": "Manual unrelated fee",
            "section": "manual-review",
            "calculation": {"mode": "manual"},
        }
        surcharge = input_fee(
            surcharge_id,
            "MISC - Technology Surcharge",
            [
                fee_input("tech-elnr", elnr_id),
                fee_input("tech-menr", menr_id),
                fee_input("tech-unrelated", "manual-unrelated"),
            ],
            rate=4.0,
            rounding_type=2,
            rounding_value=1,
        )
        fees = [elnr, menr, unrelated, surcharge]

        menr_only = MODULE.evaluate_fee_graph(
            fees,
            [surcharge_id],
            applicability={elnr_id: False},
        )
        self.assertEqual(menr_only[surcharge_id]["status"], "calculated")
        self.assertEqual(menr_only[surcharge_id]["baseValue"], 248.0)
        self.assertEqual(menr_only[surcharge_id]["amount"], 10.0)
        self.assertEqual(
            menr_only[surcharge_id]["dependencies"],
            [elnr_id, menr_id],
        )

        both = MODULE.evaluate_fee_graph(
            fees,
            [surcharge_id],
            applicability={elnr_id: True},
        )
        self.assertEqual(both[surcharge_id]["baseValue"], 372.0)
        self.assertEqual(both[surcharge_id]["amount"], 15.0)

    def test_third_slice_surcharge_uses_only_validated_electrical_fee(self):
        electrical_id = "f2ebbd08-bb1a-47ca-9193-eba43c39d17d"
        surcharge_id = "d92a2242-5b24-4624-8950-6a030a150951"

        electrical = fixed_fee(
            electrical_id,
            "DSINSP - Non-Residential Electrical Permit Fee",
            399,
        )
        unrelated = fixed_fee("other", "Other configured fee", 1000)
        surcharge = input_fee(
            surcharge_id,
            "MISC - Technology Surcharge",
            [
                fee_input("tech-electrical", electrical_id),
                fee_input("tech-other", "other"),
            ],
            rate=4.0,
            rounding_type=2,
            rounding_value=1,
        )

        actual = MODULE.evaluate_fee_graph(
            [electrical, unrelated, surcharge],
            [surcharge_id],
        )

        self.assertEqual(actual[surcharge_id]["status"], "calculated")
        self.assertEqual(actual[surcharge_id]["baseValue"], 399.0)
        self.assertEqual(actual[surcharge_id]["amount"], 16.0)
        self.assertEqual(
            actual[surcharge_id]["dependencies"],
            [electrical_id],
        )

    def test_third_slice_wrapper_rounds_hidden_dependency_to_cents_first(self):
        baseline_id = "7728caad-06b5-438f-88b3-e637cdec2b97"
        electrical_id = "f2ebbd08-bb1a-47ca-9193-eba43c39d17d"

        baseline = fixed_fee(
            baseline_id,
            "DSINSP - Non-Residential Baseline Permit Fee",
            124.495,
        )
        electrical = input_fee(
            electrical_id,
            "DSINSP - Non-Residential Electrical Permit Fee",
            [fee_input("electrical-baseline", baseline_id)],
            rate=1.0,
            rounding_type=4,
            rounding_value=1,
        )

        actual = MODULE.evaluate_fee_graph(
            [baseline, electrical],
            [electrical_id],
        )

        self.assertEqual(actual[electrical_id]["status"], "calculated")
        self.assertEqual(actual[electrical_id]["baseValue"], 124.5)
        self.assertEqual(actual[electrical_id]["amount"], 125.0)

    def test_unknown_total_based_assignment_fails_closed(self):
        fees = [
            fixed_fee("base", "Base", 100),
            input_fee(
                "unknown-total",
                "MISC - Technology Surcharge",
                [total_input()],
                rate=4.0,
                rounding_type=2,
                rounding_value=1,
            ),
        ]

        actual = MODULE.evaluate_fee_graph(fees, ["unknown-total"])

        self.assertEqual(actual["unknown-total"]["status"], "blocked")
        self.assertIn(
            MODULE.UNSUPPORTED_INPUT_CODE,
            actual["unknown-total"]["validationCodes"],
        )

    def test_validated_zero_input_utility_fee_uses_configured_fixed_amount(self):
        fee_id = "0d9e4154-3d96-4fb3-9954-177657a7b293"
        validated = input_fee(
            fee_id,
            'PU - 5/8" Sewer Capital Facility Fee',
            [],
            rate=2223,
        )
        unknown = input_fee(
            "unknown-zero-input",
            "Unknown zero-input fee",
            [],
            rate=2223,
        )

        supported = MODULE.evaluate_fee_graph(
            [validated],
            [fee_id],
        )
        self.assertEqual(supported[fee_id]["status"], "calculated")
        self.assertEqual(supported[fee_id]["amount"], 2223.0)
        self.assertTrue(supported[fee_id]["validatedFixedBehavior"])

        blocked = MODULE.evaluate_fee_graph(
            [unknown],
            ["unknown-zero-input"],
        )
        self.assertEqual(blocked["unknown-zero-input"]["status"], "blocked")
        self.assertIn(
            MODULE.MISSING_INPUT_CODE,
            blocked["unknown-zero-input"]["validationCodes"],
        )

    def test_non_surcharge_input_rate_four_remains_multiplier_four(self):
        fees = [
            input_fee(
                "ordinary",
                "Ordinary per-unit fee",
                [custom_input("Units")],
                rate=4.0,
            )
        ]

        actual = MODULE.evaluate_fee_graph(
            fees,
            ["ordinary"],
            user_inputs={"Units": 10},
        )

        self.assertEqual(actual["ordinary"]["status"], "calculated")
        self.assertEqual(actual["ordinary"]["configuredRate"], 4.0)
        self.assertEqual(actual["ordinary"]["rate"], 4.0)
        self.assertEqual(actual["ordinary"]["amount"], 40.0)

    def test_custom_input_key_whitespace_is_ignored(self):
        fees = [
            input_fee(
                "fee",
                "Quantity Fee",
                [custom_input("Quantity ")],
                rate=10,
            )
        ]

        actual = MODULE.evaluate_fee_graph(
            fees,
            ["fee"],
            user_inputs={"Quantity": 0},
        )

        self.assertEqual(actual["fee"]["status"], "calculated")
        self.assertEqual(actual["fee"]["amount"], 0.0)

    def test_duplicate_fee_input_semantics_fail_closed(self):
        fees = [
            fixed_fee("base", "Base", 100),
            input_fee(
                "tech",
                "MISC - Technology Surcharge",
                [
                    fee_input("input-1", "base"),
                    fee_input("input-2", "base"),
                ],
                rate=4.0,
                rounding_type=2,
                rounding_value=1,
            ),
        ]

        actual = MODULE.evaluate_fee_graph(fees, ["tech"])

        self.assertEqual(actual["tech"]["status"], "blocked")
        self.assertIn(
            MODULE.DUPLICATE_FEE_INPUT_CODE,
            actual["tech"]["validationCodes"],
        )

    def test_conditional_dependency_requires_external_applicability(self):
        fees = [
            input_fee(
                "conditional",
                "Working without a Permit",
                [custom_input("Value")],
                rate=2,
                section="conditional",
            ),
            input_fee(
                "wrapper",
                "Wrapper",
                [fee_input("wrapper-conditional", "conditional")],
                rate=1,
            ),
        ]

        unresolved = MODULE.evaluate_fee_graph(
            fees,
            ["wrapper"],
            user_inputs={"Value": 10},
        )
        self.assertEqual(unresolved["wrapper"]["status"], "blocked")
        self.assertIn(
            MODULE.APPLICABILITY_CODE,
            unresolved["wrapper"]["validationCodes"],
        )

        excluded = MODULE.evaluate_fee_graph(
            fees,
            ["wrapper"],
            user_inputs={"Value": 10},
            applicability={"conditional": False},
        )
        self.assertEqual(excluded["wrapper"]["status"], "calculated")
        self.assertEqual(excluded["wrapper"]["amount"], 0.0)

        included = MODULE.evaluate_fee_graph(
            fees,
            ["wrapper"],
            user_inputs={"Value": 10},
            applicability={"conditional": True},
        )
        self.assertEqual(included["wrapper"]["amount"], 20.0)

    def test_unvalidated_multi_input_non_surcharge_fails_closed(self):
        fees = [
            fixed_fee("a", "A", 10),
            fixed_fee("b", "B", 20),
            input_fee(
                "combined",
                "Some Other Combined Fee",
                [
                    fee_input("combined-a", "a"),
                    fee_input("combined-b", "b")
                ],
                rate=1,
            ),
        ]

        actual = MODULE.evaluate_fee_graph(fees, ["combined"])

        self.assertEqual(actual["combined"]["status"], "blocked")
        self.assertIn(MODULE.MULTI_INPUT_CODE, actual["combined"]["validationCodes"])

    def test_cycle_detection_fails_closed(self):
        fees = [
            input_fee("a", "A", [fee_input("a-b", "b")]),
            input_fee("b", "B", [fee_input("b-a", "a")]),
        ]

        with self.assertRaises(MODULE.EvaluationBlocked) as context:
            MODULE.evaluate_fee_graph(fees, ["a"])

        self.assertEqual(context.exception.code, MODULE.CYCLE_CODE)

    def test_validated_tree_conservation_formula_uses_hidden_acreage_inputs(self):
        nsa_id = "c64859af-2ace-4e44-9945-35a850bc4f16"
        tca_id = "67708793-e39f-4507-92ae-037db95f2b1b"
        parent_id = "17133e4e-ef4e-4ed9-af59-6c4810bfcba1"

        nsa = input_fee(
            nsa_id,
            "PRCR - HIDDEN FEE NSA 10% [All Other Zones]",
            [custom_input("NetSiteAcres")],
            rate=0.10,
        )
        tca = input_fee(
            tca_id,
            "PRCR - HIDDEN FEE TCA",
            [custom_input("TreeConservationAcres")],
            rate=1.0,
        )
        parent = {
            "feeTemplateFeeId": parent_id,
            "name": "UF - Tree Conservation 10% Fee (All Zones)",
            "section": "configured",
            "calculation": {
                "mode": "formula",
                "rule": {
                    "amount": 1296,
                    "minimum": 306,
                    "maximum": 7014.13,
                    "roundingType": 1,
                    "roundingValue": 0,
                    "inputs": [
                        fee_input("tree-nsa", nsa_id),
                        fee_input("tree-tca", tca_id),
                    ],
                    "expression": {"expressionType": 5},
                },
            },
        }

        actual = MODULE.evaluate_fee_graph(
            [nsa, tca, parent],
            [parent_id],
            user_inputs={
                "NetSiteAcres": 37.19,
                "TreeConservationAcres": 3.79,
            },
        )

        self.assertEqual(actual[parent_id]["status"], "calculated")
        self.assertEqual(actual[parent_id]["baseValue"], 3.72)
        self.assertAlmostEqual(actual[parent_id]["amount"], 4821.12)
        self.assertEqual(
            actual[parent_id]["operation"],
            "minimum-cent-rounded-times-rate",
        )

    def test_validated_pump_station_formula_uses_configured_fixed_amount(self):
        fee_id = "1484297c-4e7f-0dab-46c4-f72ef0863990"
        fee = {
            "feeTemplateFeeId": fee_id,
            "name": "PU - Pump Station Inspection Fee",
            "section": "configured",
            "calculation": {
                "mode": "formula",
                "rule": {
                    "amount": 3000,
                    "minimum": 0,
                    "maximum": 0,
                    "roundingType": 1,
                    "roundingValue": 0,
                    "inputs": [],
                    "expression": {"expressionType": 7},
                },
            },
        }

        actual = MODULE.evaluate_fee_graph(
            [fee],
            [fee_id],
        )

        self.assertEqual(actual[fee_id]["status"], "calculated")
        self.assertEqual(actual[fee_id]["amount"], 3000.0)
        self.assertEqual(
            actual[fee_id]["operation"],
            "validated-fixed-formula",
        )
        self.assertTrue(actual[fee_id]["validatedFixedBehavior"])

    def test_validated_tiered_dependency_can_feed_input_fee(self):
        fees = [
            tiered_fee(
                "stormwater",
                "ENG - Stormwater Conveyance Fee",
                "FinalDisturbedAcres",
                [
                    {
                        "lowerLimit": 0,
                        "upperLimit": 1,
                        "amount": 250,
                        "additionalAmount": 0,
                        "additionalUnit": 0,
                        "over": 0,
                    },
                    {
                        "lowerLimit": 1.0001,
                        "upperLimit": 0,
                        "amount": 250,
                        "additionalAmount": 50,
                        "additionalUnit": 1,
                        "over": 1,
                        "unlimited": True,
                    },
                ],
            ),
            input_fee(
                "wrapper",
                "Wrapper",
                [fee_input("wrapper-tier", "stormwater")],
                rate=1,
            ),
        ]

        actual = MODULE.evaluate_fee_graph(
            fees,
            ["wrapper"],
            user_inputs={"FinalDisturbedAcres": 1.74},
        )

        self.assertEqual(actual["wrapper"]["status"], "calculated")
        self.assertEqual(actual["wrapper"]["amount"], 285.0)

    def test_unvalidated_tier_input_remains_blocked(self):
        fees = [
            tiered_fee(
                "unknown-tier",
                "Unvalidated hidden rate",
                "UnvalidatedTierInput",
                [
                    {
                        "lowerLimit": 0,
                        "upperLimit": 150,
                        "amount": 65,
                        "additionalAmount": 0,
                        "additionalUnit": 1,
                        "over": 0,
                    },
                    {
                        "lowerLimit": 150.0001,
                        "upperLimit": 0,
                        "amount": 65,
                        "additionalAmount": 0.33,
                        "additionalUnit": 1,
                        "over": 150,
                        "unlimited": True,
                    },
                ],
            )
        ]

        actual = MODULE.evaluate_fee_graph(
            fees,
            ["unknown-tier"],
            user_inputs={"UnvalidatedTierInput": 200},
        )

        self.assertEqual(actual["unknown-tier"]["status"], "blocked")
        self.assertIn(
            MODULE.TIERED_CALCULATION_CODE,
            actual["unknown-tier"]["validationCodes"],
        )


if __name__ == "__main__":
    unittest.main()