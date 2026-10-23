import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / "build_fee_assessment_evidence_query.py"
SPEC = importlib.util.spec_from_file_location(
    "build_fee_assessment_evidence_query",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildFeeAssessmentEvidenceQueryTests(unittest.TestCase):
    def test_dependency_rows_are_catalog_driven(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            option = {
                "option": {
                    "permitType": "Permit A",
                    "workClass": "Work A",
                },
                "fees": [
                    {
                        "feeTemplateFeeId": "HIDDEN",
                        "name": "Rate (Hidden)",
                        "calculation": {
                            "rule": {
                                "inputs": [],
                            }
                        },
                    },
                    {
                        "feeTemplateFeeId": "VISIBLE",
                        "name": "Visible Fee",
                        "calculation": {
                            "rule": {
                                "inputs": [
                                    {
                                        "source": "fee",
                                        "inputKey": "HIDDEN",
                                    }
                                ],
                            }
                        },
                    },
                ],
            }
            (root / "option.json").write_text(
                json.dumps(option),
                encoding="utf-8",
            )
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "catalogVersion": "test",
                        "options": [{"path": "option.json"}],
                    }
                ),
                encoding="utf-8",
            )

            manifest, rows = MODULE.dependency_rows(root)

        self.assertEqual(manifest["catalogVersion"], "test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["feeTemplateFeeId"], "HIDDEN")
        self.assertTrue(rows[0]["explicitHidden"])
        self.assertEqual(rows[0]["referencedByCount"], 1)

    def test_render_sql_is_import_ready_and_not_row_specific(self):
        rows = [
            {
                "candidateOrder": 1,
                "permitType": "Permit A",
                "workClass": "Work A",
                "feeTemplateFeeId": "FEE-1",
                "expectedName": "Rate (Hidden)",
                "explicitHidden": True,
                "referencedByCount": 2,
            }
        ]

        sql = MODULE.render_sql(rows, "2024-07-01")

        self.assertIn("MATURE_ISSUED_OR_FINALIZED", sql)
        self.assertIn("MaturePermitRows", sql)
        self.assertIn("ActiveDirectFeeRows", sql)
        self.assertIn("FeeTemplateFeeID", sql)
        self.assertIn("Permit A", sql)
        self.assertIn("Work A", sql)
        self.assertNotIn("DSENG - Right of Way", sql)

    def test_candidate_inserts_batch_below_sql_server_values_limit(self):
        rows = [
            {
                "candidateOrder": index + 1,
                "permitType": "Permit",
                "workClass": "Work",
                "feeTemplateFeeId": f"FEE-{index}",
                "expectedName": f"Fee {index}",
                "explicitHidden": False,
                "referencedByCount": 1,
            }
            for index in range(1001)
        ]

        sql = MODULE.render_candidate_inserts(
            rows,
            batch_size=900,
        )

        self.assertEqual(
            sql.count("INSERT INTO #Candidates"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
