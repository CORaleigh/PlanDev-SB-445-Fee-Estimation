import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "compare_fee_template_snapshots.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location(
    "compare_fee_template_snapshots",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fee(
    assignment_id,
    *,
    fee_id="fee-id",
    name="Example Fee",
    source_template_id="template-id",
    amount=124,
    rounding_type=1,
    rounding_value=0,
    inputs=None,
    applicability=None,
):
    return {
        "feeTemplateFeeId": assignment_id,
        "feeId": fee_id,
        "name": name,
        "classification": "Input / per-unit" if inputs else "Fixed-dollar",
        "estimateMode": "input-rule-review" if inputs else "fixed",
        "selectable": True,
        "canAutoEstimate": not bool(inputs),
        "requiresInput": bool(inputs),
        "sourceTemplateId": source_template_id,
        "templateRelationship": "Direct",
        "flags": {
            "active": True,
            "manual": False,
            "hidden": False,
            "disabled": False,
            "online": True,
            "tax": False,
            "accountsReceivable": False,
            "timeTracking": False,
            "cpi": False,
            "prorated": False,
            "prorationByDate": False,
            "reinspection": False,
        },
        "applicability": applicability or {
            "hasCondition": False,
        },
        "calculation": {
            "pricingStatus": "Effective dated setup",
            "feeSetupId": "setup-a",
            "startDate": "2026-07-01",
            "amount": amount,
            "minimum": 0,
            "maximum": 0,
            "roundingType": rounding_type,
            "roundingValue": rounding_value,
            "effectiveSetupCount": 1,
            "inputs": list(inputs or []),
            "tiers": [],
            "advancedRules": {},
        },
    }


def fee_input(key, source="fee"):
    return {
        "inputKey": key,
        "source": source,
        "inputTypeId": 3,
        "calculationInputType": 1,
        "variable": True,
    }


def document(fees, *, as_of="2026-09-15"):
    return {
        "schemaVersion": "1.0",
        "asOfDate": as_of,
        "permitOptions": [
            {
                "key": "permit-type|work-class",
                "permitType": "Permit Type",
                "workClass": "Work Class",
                "fees": list(fees),
            }
        ],
    }


class FeeTemplateSnapshotComparisonTests(unittest.TestCase):
    def test_identical_snapshot_has_no_material_changes(self):
        old = document([fee("assignment-a")])
        new = deepcopy(old)
        new["asOfDate"] = "2026-09-18"

        report = MODULE.compare_documents(old, new)

        self.assertEqual(report["summary"]["totalChanges"], 0)
        self.assertFalse(report["summary"]["actionRequired"])

    def test_pricing_only_change_is_safe_configuration_change(self):
        old = document([fee("assignment-a", amount=124)])
        new = document(
            [fee("assignment-a", amount=130)],
            as_of="2026-09-18",
        )

        report = MODULE.compare_documents(old, new)
        change = report["changes"][0]

        self.assertEqual(change["classification"], MODULE.SAFE)
        self.assertFalse(report["summary"]["actionRequired"])
        self.assertEqual(
            [item["path"] for item in change["changes"]],
            ["calculation.amount"],
        )

    def test_same_as_of_pricing_change_requires_revalidation(self):
        old = document([fee("assignment-a", amount=124)])
        new = document([fee("assignment-a", amount=130)])

        report = MODULE.compare_documents(old, new)
        change = report["changes"][0]

        self.assertEqual(change["classification"], MODULE.REVALIDATE)
        self.assertTrue(report["summary"]["actionRequired"])
        self.assertIn("in-place source-configuration mutation", change["reason"])

    def test_rounding_change_requires_revalidation(self):
        old = document([fee("assignment-a", rounding_type=1)])
        new = document(
            [fee("assignment-a", rounding_type=4)],
            as_of="2026-09-18",
        )

        report = MODULE.compare_documents(old, new)

        self.assertEqual(
            report["changes"][0]["classification"],
            MODULE.REVALIDATE,
        )
        self.assertTrue(report["summary"]["actionRequired"])

    def test_unvalidated_input_change_requires_compatibility_review(self):
        old = document(
            [
                fee(
                    "assignment-a",
                    inputs=[fee_input("dependency-a")],
                )
            ]
        )
        new = document(
            [
                fee(
                    "assignment-a",
                    inputs=[fee_input("dependency-b")],
                )
            ],
            as_of="2026-09-18",
        )

        report = MODULE.compare_documents(old, new)

        self.assertEqual(
            report["changes"][0]["classification"],
            MODULE.COMPATIBILITY,
        )

    def test_validated_surcharge_dependency_change_requires_revalidation(self):
        surcharge_id = "d92a2242-5b24-4624-8950-6a030a150951"
        electrical_id = "f2ebbd08-bb1a-47ca-9193-eba43c39d17d"
        old = document(
            [
                fee(
                    surcharge_id,
                    inputs=[fee_input(electrical_id)],
                )
            ]
        )
        new = document(
            [
                fee(
                    surcharge_id,
                    inputs=[fee_input("different-component")],
                )
            ],
            as_of="2026-09-18",
        )

        report = MODULE.compare_documents(old, new)

        self.assertEqual(
            report["changes"][0]["classification"],
            MODULE.REVALIDATE,
        )
        self.assertIn(
            "historically validated",
            report["changes"][0]["reason"],
        )

    def test_validated_percentage_fee_name_change_requires_revalidation(self):
        old = document(
            [
                fee(
                    "wwp-assignment",
                    name="Working without a Permit (Electrical)",
                    inputs=[fee_input("base-fee")],
                )
            ]
        )
        new = document(
            [
                fee(
                    "wwp-assignment",
                    name="Working without Permit - Electrical",
                    inputs=[fee_input("base-fee")],
                )
            ],
            as_of="2026-09-18",
        )

        report = MODULE.compare_documents(old, new)

        self.assertEqual(
            report["changes"][0]["classification"],
            MODULE.REVALIDATE,
        )

    def test_assignment_id_replacement_is_paired_and_revalidated(self):
        old = document(
            [
                fee(
                    "old-assignment",
                    fee_id="stable-fee",
                    name="Stable Fee",
                )
            ]
        )
        new = document(
            [
                fee(
                    "new-assignment",
                    fee_id="stable-fee",
                    name="Stable Fee",
                )
            ],
            as_of="2026-09-18",
        )

        report = MODULE.compare_documents(old, new)

        self.assertEqual(len(report["changes"]), 1)
        change = report["changes"][0]
        self.assertEqual(change["classification"], MODULE.REVALIDATE)
        self.assertEqual(
            change["oldFeeTemplateFeeId"],
            "old-assignment",
        )
        self.assertEqual(
            change["newFeeTemplateFeeId"],
            "new-assignment",
        )

    def test_new_fee_is_new_unknown(self):
        old = document([fee("assignment-a")])
        new = document(
            [
                fee("assignment-a"),
                fee(
                    "assignment-b",
                    fee_id="fee-b",
                    name="New Fee",
                ),
            ],
            as_of="2026-09-18",
        )

        report = MODULE.compare_documents(old, new)

        self.assertEqual(len(report["changes"]), 1)
        self.assertEqual(
            report["changes"][0]["classification"],
            MODULE.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
