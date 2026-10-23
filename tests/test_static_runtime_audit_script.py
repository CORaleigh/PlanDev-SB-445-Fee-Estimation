import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_static_runtime_coverage.mjs"


class StaticRuntimeAuditScriptTests(unittest.TestCase):
    def test_calculation_blocker_filter_and_limit_are_supported(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('"--calculation-blocker"', source)
        self.assertIn('"--limit"', source)
        self.assertIn("args.calculationBlocker", source)
        self.assertIn("calculationRanked.slice(0, args.limit)", source)


if __name__ == "__main__":
    unittest.main()
