import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "build_option_input_prevalence_query.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "build_option_input_prevalence_query",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture_document():
    return {
        "option": {
            "optionKey": "TEST",
            "permitType": "Permit Type A",
            "workClass": "Work Class A",
        },
        "requiredInputs": [
            {
                "key": "ValuationValue",
                "label": "Permit Value",
                "source": "custom-field",
            },
            {
                "key": "Quantity",
                "label": "Quantity",
                "source": "custom-field",
            },
        ],
        "fees": [
            {
                "feeTemplateFeeId": "VALUE",
                "name": "Value Fee",
                "section": "input-required",
                "applicability": {
                    "status": "configured",
                    "conditions": [],
                },
                "calculation": {
                    "mode": "input",
                    "rule": {
                        "amount": 1,
                        "inputs": [
                            {
                                "inputKey": "ValuationValue",
                                "label": "Permit Value",
                                "source": "custom-field",
                            }
                        ],
                    },
                },
            },
            {
                "feeTemplateFeeId": "QTY",
                "name": "Quantity Fee",
                "section": "input-required",
                "applicability": {
                    "status": "configured",
                    "conditions": [],
                },
                "calculation": {
                    "mode": "input",
                    "rule": {
                        "amount": 1,
                        "inputs": [
                            {
                                "inputKey": "Quantity",
                                "label": "Quantity",
                                "source": "custom-field",
                            }
                        ],
                    },
                },
            },
        ],
    }


class BuildOptionInputPrevalenceQueryTests(unittest.TestCase):
    def test_field_rows_map_permit_value_and_custom_field(self):
        rows = MODULE.field_rows(fixture_document())
        by_key = {row["key"]: row for row in rows}

        self.assertEqual(
            by_key["ValuationValue"]["source_kind"],
            "permit",
        )
        self.assertEqual(
            by_key["ValuationValue"]["source_column"],
            "VALUE",
        )
        self.assertEqual(
            by_key["Quantity"]["source_kind"],
            "custom-saver",
        )
        self.assertEqual(
            by_key["Quantity"]["source_column"],
            "Quantity",
        )

    def test_rendered_sql_has_import_ready_direct_prevalence_contract(self):
        document = fixture_document()
        sql = MODULE.render_sql(
            document,
            MODULE.field_rows(document),
            "2024-07-01",
        )

        self.assertIn(
            "MATURE_ISSUED_OR_FINALIZED",
            sql,
        )
        self.assertIn(
            "LEFT JOIN dbo.CUSTOMSAVERPERMITMANAGEMENT",
            sql,
        )
        self.assertIn(
            "PhysicalColumnFound",
            sql,
        )
        self.assertIn(
            "ActivePct",
            sql,
        )
        self.assertIn(
            "direct input prevalence",
            sql.lower(),
        )
        self.assertIn(
            "Permit Type A",
            sql,
        )
        self.assertIn(
            "Work Class A",
            sql,
        )


if __name__ == "__main__":
    unittest.main()
