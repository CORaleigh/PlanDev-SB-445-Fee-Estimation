import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_fixed_fee_fixtures.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_fixed_fee_fixtures", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildFixedFeeFixturesTests(unittest.TestCase):
    def test_amount_class_separates_base_match_from_adjusted_total(self):
        result = MODULE.amount_class(
            Decimal("100"), Decimal("100"), Decimal("90")
        )
        self.assertEqual(
            result,
            ("base-matches-computed-adjusted", True, False),
        )

    def test_validation_window_is_narrower_than_broad_setup_dates(self):
        entry = {
            "setupStart": datetime(2022, 7, 1),
            "setupEnd": datetime(2027, 6, 30),
        }
        self.assertFalse(
            MODULE.within_validation_period(
                datetime(2025, 7, 1),
                entry,
                date(2026, 7, 1),
                date(2026, 9, 15),
            )
        )
        self.assertTrue(
            MODULE.within_validation_period(
                datetime(2026, 8, 1),
                entry,
                date(2026, 7, 1),
                date(2026, 9, 15),
            )
        )

    def test_ready_fixed_entries_use_direct_fees_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "options").mkdir()
            manifest = {
                "catalogVersion": "2026-09-15",
                "configurationAsOfDate": "2026-09-15",
                "options": [
                    {
                        "path": "options/test.json",
                    }
                ],
            }
            fee = {
                "feeKey": "permit-template:F1",
                "feeTemplateFeeId": "F1",
                "name": "Fixed fee",
                "section": "configured",
                "applicability": {"conditions": []},
                "calculation": {
                    "mode": "fixed",
                    "amountStatus": "calculable",
                    "fixedAmount": 100,
                    "rule": {
                        "startDate": "2022-07-01T00:00:00",
                        "endDate": "2027-06-30T00:00:00",
                    },
                },
            }
            option_document = {
                "option": {
                    "optionKey": "TYPE|CLASS",
                    "permitType": "Type",
                    "workClass": "Class",
                },
                "fees": [fee],
                "relatedPermits": [{"fees": [fee]}],
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (root / "options" / "test.json").write_text(
                json.dumps(option_document), encoding="utf-8"
            )

            _, entries = MODULE.ready_fixed_entries(root)

            self.assertEqual(len(entries), 1)
            entry = next(iter(entries.values()))
            self.assertEqual(entry["configuredAmount"], Decimal("100"))
            self.assertEqual(
                entry["setupStart"].date().isoformat(), "2022-07-01"
            )


if __name__ == "__main__":
    unittest.main()
