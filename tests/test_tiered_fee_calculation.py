import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "tiered_fee_calculation.py"
)
SPEC = importlib.util.spec_from_file_location("tiered_fee_calculation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def tier(
    lower,
    upper,
    amount,
    additional_amount=0,
    additional_unit=0,
    over=0,
    unlimited=False,
):
    return {
        "lowerLimit": lower,
        "upperLimit": upper,
        "amount": amount,
        "additionalAmount": additional_amount,
        "additionalUnit": additional_unit,
        "over": over,
        "unlimited": unlimited,
    }


class TieredFeeCalculationTests(unittest.TestCase):
    def test_stormwater_acreage_rounds_extra_units_to_tenth(self):
        tiers = [
            tier(0, 1, 250),
            tier(1.0001, 0, 250, 50, 1, 1, True),
        ]
        cases = {
            1.71: 285,
            1.74: 285,
            1.80: 290,
            4.84: 440,
            5.26: 465,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                actual = MODULE.calculate_tiered_amount(
                    "FinalDisturbedAcres", value, tiers
                )
                self.assertEqual(actual["amount"], expected)

    def test_private_fire_line_rounds_partial_hundred_feet_up(self):
        tiers = [tier(0, 0, 248, 27, 100, 500, True)]
        self.assertEqual(
            MODULE.calculate_tiered_amount(
                "LengthFireSprinkler", 580, tiers
            )["amount"],
            275,
        )
        self.assertEqual(
            MODULE.calculate_tiered_amount(
                "LengthFireSprinkler", 1078, tiers
            )["amount"],
            410,
        )

    def test_integer_standpipe_count_uses_exact_extra_count(self):
        tiers = [tier(0, 0, 401, 164, 1, 1, True)]
        self.assertEqual(
            MODULE.calculate_tiered_amount(
                "NumberofStandpipes", 10, tiers
            )["amount"],
            1877,
        )

    def test_fixed_appliance_tier_uses_selected_band_amount(self):
        tiers = [
            tier(0, 3, 248),
            tier(4, 6, 496),
            tier(7, 9, 744),
        ]
        self.assertEqual(
            MODULE.calculate_tiered_amount(
                "NumberAppliances", 6, tiers
            )["amount"],
            496,
        )

    def test_valuation_tiers_apply_rate_to_full_permit_value(self):
        tiers = [
            tier(0, 500000, 0, 0.0021, 1, 0),
            tier(500001, 10000000, 1050, 0.0006, 1, 0),
            tier(10000001, 0, 7250, 0.0001, 1, 0, True),
        ]
        cases = {
            984184.29: 1640.510574,
            10421623.60: 8292.16236,
            87286815.60: 15978.68156,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                actual = MODULE.calculate_tiered_amount(
                    "ValuationValue", value, tiers
                )
                self.assertAlmostEqual(actual["amount"], expected, places=6)

    def test_valuation_tier_rejects_nonzero_over_value(self):
        tiers = [tier(0, 0, 1050, 0.0006, 1, 500000, True)]
        with self.assertRaises(MODULE.TieredCalculationUnsupported):
            MODULE.calculate_tiered_amount("ValuationValue", 984184.29, tiers)

    def test_row_linear_foot_tier_uses_raw_feet_above_150(self):
        tiers = [
            tier(0, 150, 39),
            tier(150.0001, 0, 39, 0.20, 1, 150, True),
        ]
        cases = {
            100: 39,
            150: 39,
            175: 44,
            240: 57,
            365: 82,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                actual = MODULE.calculate_tiered_amount(
                    "TotalMajorLFStrLN", value, tiers
                )
                self.assertAlmostEqual(actual["amount"], expected)

    def test_row_linear_foot_tier_rejects_unvalidated_shape(self):
        tiers = [
            tier(0, 150, 39),
            tier(150.0001, 0, 39, 0.20, 10, 150, True),
        ]
        with self.assertRaises(MODULE.TieredCalculationUnsupported):
            MODULE.calculate_tiered_amount(
                "TotalMajorLFStrLN", 240, tiers
            )


if __name__ == "__main__":
    unittest.main()
