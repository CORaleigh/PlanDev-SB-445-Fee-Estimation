import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SCRIPT = SCRIPTS / "normalize_input_prevalence_results.py"
SPEC = importlib.util.spec_from_file_location(
    "normalize_input_prevalence_results",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def document():
    return {
        "option": {
            "optionKey": "TEST",
            "permitType": "Permit A",
            "workClass": "Work A",
        },
        "requiredInputs": [],
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
                                "source": "system",
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


class NormalizeInputPrevalenceResultsTests(unittest.TestCase):
    def test_normalizes_query_122_shape_and_filters_mature(self):
        rows = [
            {
                "DenominatorName": "ALL_RECENT",
                "PermitRows": "200",
                "FieldOrder": "1",
                "InputKey": "ValuationValue",
                "InputLabel": "Permit Value",
                "SourceKind": "permit",
                "SourceColumn": "VALUE",
                "ValueKind": "numeric",
                "PhysicalColumnFound": "1",
                "PopulatedRows": "200",
                "PopulatedPct": "100.0000",
                "ActiveRows": "100",
                "ActivePct": "50.0000",
                "ActiveDefinition": "numeric <> 0",
            },
            {
                "DenominatorName": "MATURE_ISSUED_OR_FINALIZED",
                "PermitRows": "100",
                "FieldOrder": "1",
                "InputKey": "ValuationValue",
                "InputLabel": "Permit Value",
                "SourceKind": "permit",
                "SourceColumn": "VALUE",
                "ValueKind": "numeric",
                "PhysicalColumnFound": "1",
                "PopulatedRows": "100",
                "PopulatedPct": "100.0000",
                "ActiveRows": "68",
                "ActivePct": "68.0000",
                "ActiveDefinition": "numeric <> 0",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            selected = MODULE.read_source_rows(
                path,
                MODULE.MATURE_DENOMINATOR,
            )
            normalized = MODULE.normalize_rows(
                selected,
                document(),
                "Permit A",
                "Work A",
            )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["PermitRows"], "100")
        self.assertEqual(normalized[0]["ActivePct"], "68.0000")
        self.assertEqual(normalized[0]["SourceKind"], "permit")
        self.assertEqual(normalized[0]["SourceColumn"], "VALUE")

    def test_normalizes_query_123_mature_only_shape(self):
        rows = [
            {
                "FieldOrder": "2",
                "ScenarioOrder": "1",
                "ScenarioName": "Scenario",
                "FieldRole": "primary",
                "InputKey": "Quantity",
                "ValueKind": "numeric",
                "PhysicalColumnFound": "True",
                "PermitRows": "2733",
                "PopulatedRows": "2733",
                "PopulatedPct": "100.0000",
                "ActiveRows": "258",
                "ActivePct": "9.4402",
                "NumericMinNonzero": "1",
                "NumericMaxNonzero": "1000",
            }
        ]
        normalized = MODULE.normalize_rows(
            rows,
            document(),
            "Permit A",
            "Work A",
        )

        self.assertEqual(len(normalized), 1)
        row = normalized[0]
        self.assertEqual(row["InputLabel"], "Quantity")
        self.assertEqual(row["SourceKind"], "custom-saver")
        self.assertEqual(row["SourceColumn"], "Quantity")
        self.assertEqual(row["ActiveDefinition"], "numeric <> 0")

    def test_write_rows_uses_catalog_import_schema(self):
        rows = MODULE.normalize_rows(
            [
                {
                    "FieldOrder": "1",
                    "InputKey": "Quantity",
                    "ValueKind": "numeric",
                    "PhysicalColumnFound": "1",
                    "PermitRows": "10",
                    "PopulatedRows": "10",
                    "PopulatedPct": "100",
                    "ActiveRows": "2",
                    "ActivePct": "20",
                }
            ],
            document(),
            "Permit A",
            "Work A",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "normalized.csv"
            MODULE.write_rows(path, rows)
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                written = list(reader)

        self.assertEqual(reader.fieldnames, MODULE.OUTPUT_COLUMNS)
        self.assertEqual(written[0]["DenominatorName"], MODULE.MATURE_DENOMINATOR)
        self.assertEqual(written[0]["InputKey"], "Quantity")


if __name__ == "__main__":
    unittest.main()
