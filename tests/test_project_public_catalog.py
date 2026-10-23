import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "project_public_catalog.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("project_public_catalog", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


PERMIT_TYPE_ID = "11111111-1111-1111-1111-111111111111"
WORK_CLASS_ID = "22222222-2222-2222-2222-222222222222"
FEE_ID = "33333333-3333-3333-3333-333333333333"


def internal_document():
    return {
        "catalogVersion": "test-r1",
        "configuration": {
            "asOfDate": "2026-09-24",
            "historicalPeriod": {
                "startDate": "2024-01-01",
                "endDate": "2025-12-31",
            },
        },
        "option": {
            "optionKey": "internal-option-key",
            "permitTypeId": PERMIT_TYPE_ID,
            "permitType": "Test Permit",
            "workClassId": WORK_CLASS_ID,
            "workClass": "Test Work",
        },
        "fees": [
            {
                "feeTemplateFeeId": FEE_ID,
                "feeSetupId": "44444444-4444-4444-4444-444444444444",
                "name": "Quantity Fee",
                "section": "input-required",
                "assessmentRole": "standalone-or-assessed",
                "assessmentEvidence": {
                    "status": "available",
                    "denominator": 100,
                },
                "applicability": {
                    "status": "configured",
                    "conditions": [],
                },
                "calculation": {
                    "mode": "input",
                    "fixedAmount": None,
                    "rule": {
                        "amount": 10,
                        "inputs": [
                            {
                                "inputKey": "Quantity",
                                "label": "Quantity",
                                "source": "custom-field",
                            }
                        ],
                    },
                },
            }
        ],
        "requiredInputs": [],
        "inputPrevalence": [
            {
                "inputKey": "Quantity",
                "status": "available",
                "denominator": 100,
                "activeRows": 42,
                "activeRate": 0.42,
                "basis": "direct-input-prevalence-query",
            }
        ],
        "relatedPermits": [
            {
                "permitType": "Related Permit",
                "workClass": "Related Work",
                "relationshipFamily": "same-address",
                "activeFeeOccurrence": {
                    "status": "available",
                    "rate": 0.4,
                    "numerator": 2,
                    "denominator": 5,
                },
                "fees": [
                    {
                        "name": "Related Fee",
                        "section": "configured",
                        "assessmentRole": "standalone-or-assessed",
                        "occurrence": {
                            "status": "available",
                            "rate": 0.12,
                            "numerator": 12,
                            "denominator": 100,
                        },
                    }
                ],
            }
        ],
        "disclosures": [{"code": "ESTIMATE_NOT_INVOICE", "text": "raw"}],
        "sourceFile": "internal-extract.json",
    }


def write_catalog(root, document=None):
    catalog = root / "catalog"
    options = catalog / "options"
    options.mkdir(parents=True)
    manifest = {
        "catalogVersion": "test-r1",
        "generatedAtUtc": "2026-09-24T12:00:00Z",
        "configurationAsOfDate": "2026-09-24",
        "options": [
            {
                "optionKey": "internal-option-key",
                "permitType": "Test Permit",
                "workClass": "Test Work",
                "path": "options/internal.json",
            }
        ],
    }
    (catalog / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (options / "internal.json").write_text(
        json.dumps(document or internal_document()),
        encoding="utf-8",
    )
    return catalog


def all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_keys(child)


class ProjectPublicCatalogTests(unittest.TestCase):
    def test_projects_opaque_identifiers_and_reduced_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "public"
            result = MODULE.project_catalog(
                write_catalog(root),
                destination,
                minimum_sample_size=10,
            )

            manifest = json.loads(
                (destination / "manifest.json").read_text(encoding="utf-8")
            )
            option = json.loads(
                (destination / "options" / "o001.json").read_text(
                    encoding="utf-8"
                )
            )
            serialized = json.dumps([manifest, option])

            self.assertEqual(result["options"], 1)
            self.assertEqual(manifest["schemaVersion"], "public-1.0")
            self.assertEqual(manifest["options"][0]["path"], "options/o001.json")
            self.assertEqual(option["option"]["optionKey"], "o001")
            self.assertEqual(option["option"]["permitTypeKey"], "t001")
            self.assertEqual(option["option"]["workClassKey"], "w001")
            self.assertNotIn(PERMIT_TYPE_ID, serialized)
            self.assertNotIn(WORK_CLASS_ID, serialized)
            self.assertNotIn(FEE_ID, serialized)

            control = option["ui"]["controls"][0]
            self.assertEqual(
                control["historicalPrevalence"],
                {"status": "available", "percent": 42},
            )
            relation = option["relatedPermits"][0]
            self.assertEqual(
                relation["history"],
                {"status": "limited-history", "hasOccurrences": True},
            )
            self.assertEqual(
                relation["fees"][0]["history"],
                {"status": "available", "percent": 12},
            )

            forbidden = {
                "assessmentEvidence",
                "denominator",
                "feeSetupId",
                "numerator",
                "permitTypeId",
                "sourceFile",
                "workClassId",
            }
            self.assertTrue(forbidden.isdisjoint(set(all_keys(option))))

    def test_duplicate_visible_choice_labels_remain_fail_closed(self):
        document = internal_document()
        for index, value in enumerate(("ZONE-A", "ZONE-B"), start=1):
            document["fees"].append(
                {
                    "feeTemplateFeeId": f"zone-fee-{index}",
                    "name": f"Zone Fee {index}",
                    "section": "conditional",
                    "assessmentRole": "standalone-or-assessed",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "Zone",
                                            "operandFriendlyName": "Zone",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": value,
                                                "label": "Zone 1",
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": index * 10,
                        "rule": {},
                    },
                }
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "public"
            MODULE.project_catalog(
                write_catalog(root, document),
                destination,
                minimum_sample_size=10,
            )
            option = json.loads(
                (destination / "options" / "o001.json").read_text(
                    encoding="utf-8"
                )
            )

        control = next(
            item for item in option["ui"]["controls"]
            if item["label"] == "Zone"
        )
        self.assertFalse(option["ui"]["displayReady"])
        self.assertFalse(control["displayReady"])
        self.assertEqual(
            [choice["label"] for choice in control["choices"]],
            ["Zone 1", "Zone 1"],
        )
        self.assertEqual(
            option["ui"]["uiBlockers"][0]["code"],
            "DUPLICATE_CHOICE_LABELS_NOT_DISPLAY_READY",
        )

    def test_rejects_nonpositive_history_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "must be positive"):
                MODULE.project_catalog(
                    write_catalog(root),
                    root / "public",
                    minimum_sample_size=0,
                )

    def test_preserves_existing_output_and_temporary_directories(self):
        for name in ("public", "public.tmp"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                catalog = write_catalog(root)
                existing = root / name
                existing.mkdir()
                sentinel = existing / "keep.txt"
                sentinel.write_text("local work", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    MODULE.project_catalog(catalog, root / "public")
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "local work")


if __name__ == "__main__":
    unittest.main()
