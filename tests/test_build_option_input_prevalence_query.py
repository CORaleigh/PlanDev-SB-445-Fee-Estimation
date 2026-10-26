import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


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


def catalog_fixture(root):
    (root / "options").mkdir()
    document = fixture_document()
    document.update(schemaVersion="1.0", catalogVersion="test", configuration={
        "asOfDate": "2026-10-14", "historicalPeriod": {"startDate": "2019-07-01", "endDate": "2026-09-15"},
    })
    document["option"].update(permitTypeId="11111111-1111-1111-1111-111111111111",
                              workClassId="22222222-2222-2222-2222-222222222222")
    option = document["option"]
    option["optionKey"] = option["permitTypeId"] + "|" + option["workClassId"]
    empty = copy.deepcopy(document)
    empty["option"].update(workClassId="33333333-3333-3333-3333-333333333333", workClass="No Fields")
    empty["option"]["optionKey"] = option["permitTypeId"] + "|" + empty["option"]["workClassId"]
    empty.update(fees=[], requiredInputs=[])
    entries = []
    for index, doc in enumerate((document, empty)):
        path = f"options/{index}.json"
        (root / path).write_text(json.dumps(doc), encoding="utf-8")
        entries.append({**doc["option"], "path": path})
    (root / "manifest.json").write_text(json.dumps({"schemaVersion": "1.0", "catalogVersion": "test",
        "configurationAsOfDate": "2026-10-14", "options": entries}), encoding="utf-8")
    return document


class BuildOptionInputPrevalenceQueryTests(unittest.TestCase):
    def test_batch_reuses_sql_and_binds_exact_source_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            document = catalog_fixture(Path(tmp))
            sql = MODULE.render_batch_sql([(document, MODULE.field_rows(document))], "2019-07-01", "2026-09-15")
        self.assertIn("pm.PMPERMITTYPEID = @PermitTypeId", sql)
        self.assertIn("pm.PMPERMITWORKCLASSID = @WorkClassId", sql)
        self.assertIn("FROM #BatchFields WHERE OptionKey = @OptionKey", sql)
        self.assertIn(document["option"]["optionKey"], sql)
        self.assertEqual(sql.count("DECLARE field_cursor"), 1)
        self.assertEqual(sql.count("SELECT * FROM #BatchPrevalence"), 1)
        self.assertIn("INSERT INTO #BatchContext", sql)
        self.assertIn("INSERT INTO #BatchPrevalence", sql)
        self.assertIn("HAVING COUNT_BIG(*) > 1", sql)
        self.assertIn("@ValueKind,0,COUNT_BIG(*),NULL,NULL,NULL,NULL", sql)
        self.assertIn("NULLIF(COUNT_BIG(*),0)", sql)

    def test_batch_rejects_duplicate_or_mismatched_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = catalog_fixture(Path(tmp))
            rows = MODULE.field_rows(doc)
            with self.assertRaises(ValueError):
                MODULE.render_batch_sql([(doc, rows), (doc, rows)], "2024-07-01", "2026-09-15")
            doc["option"]["optionKey"] = "wrong"
            with self.assertRaises(ValueError):
                MODULE.render_batch_sql([(doc, rows)], "2024-07-01", "2026-09-15")

    def test_insert_values_are_escaped_and_split_at_sql_row_limit(self):
        sql = MODULE.insert_rows("#Test", "Value", [("O'Brien",)] * 1001)
        self.assertEqual(sql.count("INSERT INTO #Test"), 2)
        self.assertEqual(sql.count("N'O''Brien'"), 1001)

    def test_query_period_uses_catalog_dates_and_validates_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = catalog_fixture(Path(tmp))
            self.assertEqual(MODULE.query_period([doc]), ("2019-07-01", "2026-09-15"))
            with self.assertRaises(ValueError):
                MODULE.query_period([doc], "2027-01-01", "2026-01-01")
            other = copy.deepcopy(doc)
            other["configuration"]["historicalPeriod"]["endDate"] = "2026-09-16"
            with self.assertRaises(ValueError):
                MODULE.query_period([doc, other])
            self.assertEqual(MODULE.query_period([doc, other], "2024-01-01", "2024-02-01"),
                             ("2024-01-01", "2024-02-01"))

    def test_catalog_loader_rejects_public_paths_and_identity_changes(self):
        for change in ("public", "path", "identity", "version", "date"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                catalog_fixture(root)
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                if change == "public":
                    manifest["schemaVersion"] = "public-1.0"
                elif change == "path":
                    manifest["options"][0]["path"] = "../private.json"
                elif change == "identity":
                    manifest["options"][0]["permitType"] = "Wrong"
                elif change == "version":
                    manifest["catalogVersion"] = "wrong"
                else:
                    manifest["configurationAsOfDate"] = "2026-09-24"
                (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(ValueError):
                    MODULE.load_options(root)

    def test_all_cli_skips_options_without_fields_and_preserves_existing_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_fixture(root)
            output = root / "query.sql"
            arguments = ["generate", str(root), "--all", "--output", str(output)]
            with patch.object(sys, "argv", arguments), redirect_stdout(io.StringIO()) as printed:
                MODULE.main()
            self.assertIn("1 with project fields; 1 without", printed.getvalue())
            sql = output.read_text(encoding="utf-8")
            self.assertIn("2019-07-01", sql)
            self.assertIn("2026-09-15", sql)
            with patch.object(sys, "argv", arguments):
                with self.assertRaises(FileExistsError):
                    MODULE.main()
            self.assertEqual(output.read_text(encoding="utf-8"), sql)

    def test_cli_requires_one_selection_mode(self):
        for flags in ([], ["--permit-type", "Permit"], ["--all", "--work-class", "Work"]):
            with self.subTest(flags=flags), patch.object(sys, "argv", ["generate", "--output", "unused.sql", *flags]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    MODULE.parse_args()

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
