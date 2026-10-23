import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analyze_fixed_fee_behavior.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("analyze_fixed_fee_behavior", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnalyzeFixedFeeBehaviorTests(unittest.TestCase):
    def test_flat_total(self):
        self.assertEqual(
            MODULE.computed_behavior(Decimal("124"), Decimal("124")),
            ("flat-total", Decimal("1")),
        )

    def test_integer_multiplier(self):
        self.assertEqual(
            MODULE.computed_behavior(Decimal("124"), Decimal("1116")),
            ("integer-multiplier", Decimal("9")),
        )

    def test_zero_computed(self):
        self.assertEqual(
            MODULE.computed_behavior(Decimal("248"), Decimal("0")),
            ("zero-computed", Decimal("0")),
        )


if __name__ == "__main__":
    unittest.main()
