import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_fee_usage_patterns.py"
)
SPEC = importlib.util.spec_from_file_location("build_fee_usage_patterns", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildFeeUsagePatternsTests(unittest.TestCase):
    def test_default_inputs_are_repo_root_data_paths(self):
        self.assertEqual(
            MODULE.DEFAULT_CSV,
            "data/permit_family_fee_detail.csv",
        )
        self.assertEqual(
            MODULE.DEFAULT_COHORT_CSV,
            "data/permit_family_cohort.csv",
        )

    def write_csv(self, path, fieldnames, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_all_parent_cohort_supplies_denominators(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fee_path = root / "fees.csv"
            cohort_path = root / "cohort.csv"
            template_path = root / "templates.json"

            fee_row = {
                "ParentPermitID": "P1",
                "ParentPermitType": "Building",
                "ParentWorkClass": "New",
                "ParentAppliedDate": "2026-01-10",
                "FeeOwnerPermitID": "P1",
                "FeeOwnerRole": "Parent",
                "RelationshipFamily": "Parent Permit",
                "FeeOwnerPermitType": "Building",
                "FeeOwnerWorkClass": "New",
                "ComputedFeeID": "CF1",
                "FeeTemplateFeeID": "TF1",
                "FeeName": "Building fee",
                "ComputationType": "Fixed",
                "ComputedAmount": "100.00",
            }
            self.write_csv(
                fee_path,
                sorted(MODULE.REQUIRED_COLUMNS),
                [fee_row],
            )

            cohort_rows = [
                {
                    "ParentPermitID": "P1",
                    "ParentPermitType": "Building",
                    "ParentWorkClass": "New",
                    "ParentAppliedDate": "2026-01-10",
                    "FeeOwnerPermitID": "P1",
                    "FeeOwnerRole": "Parent",
                    "RelationshipFamily": "Parent Permit",
                    "FeeOwnerPermitType": "Building",
                    "FeeOwnerWorkClass": "New",
                    "FeeOwnerHasActiveFee": "True",
                    "ActiveFeeInstances": "1",
                    "FamilyMemberCount": "2",
                    "RelatedPermitCount": "1",
                    "FamilyActiveFeeInstances": "1",
                    "FamilyHasActiveFee": "True",
                },
                {
                    "ParentPermitID": "P1",
                    "ParentPermitType": "Building",
                    "ParentWorkClass": "New",
                    "ParentAppliedDate": "2026-01-10",
                    "FeeOwnerPermitID": "C1",
                    "FeeOwnerRole": "Child",
                    "RelationshipFamily": "MEP",
                    "FeeOwnerPermitType": "Electrical",
                    "FeeOwnerWorkClass": "New",
                    "FeeOwnerHasActiveFee": "False",
                    "ActiveFeeInstances": "0",
                    "FamilyMemberCount": "2",
                    "RelatedPermitCount": "1",
                    "FamilyActiveFeeInstances": "1",
                    "FamilyHasActiveFee": "True",
                },
                {
                    "ParentPermitID": "P2",
                    "ParentPermitType": "Building",
                    "ParentWorkClass": "New",
                    "ParentAppliedDate": "2026-02-10",
                    "FeeOwnerPermitID": "P2",
                    "FeeOwnerRole": "Parent",
                    "RelationshipFamily": "Parent Permit",
                    "FeeOwnerPermitType": "Building",
                    "FeeOwnerWorkClass": "New",
                    "FeeOwnerHasActiveFee": "False",
                    "ActiveFeeInstances": "0",
                    "FamilyMemberCount": "1",
                    "RelatedPermitCount": "0",
                    "FamilyActiveFeeInstances": "0",
                    "FamilyHasActiveFee": "False",
                },
            ]
            self.write_csv(
                cohort_path,
                sorted(MODULE.COHORT_REQUIRED_COLUMNS),
                cohort_rows,
            )

            template_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.0",
                        "asOfDate": "2026-09-16",
                        "permitOptions": [
                            {
                                "key": "building-new",
                                "permitType": "Building",
                                "workClass": "New",
                                "permitTypeId": "PT1",
                                "workClassId": "WC1",
                                "feeTemplateId": "FT1",
                                "feeTemplate": "Building fees",
                                "fees": [
                                    {
                                        "feeTemplateFeeId": "TF1",
                                        "name": "Building fee",
                                        "classification": "Fixed",
                                        "estimateMode": "fixed",
                                        "selectable": True,
                                        "canAutoEstimate": True,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            document = MODULE.build_document(
                fee_path,
                cohort_path,
                template_path,
                "both",
            )

            self.assertEqual(document["schemaVersion"], "2.0")
            self.assertEqual(
                document["summary"]["eligibleParentFamilies"],
                2,
            )
            self.assertEqual(
                document["summary"]["zeroFeeParentFamilies"],
                1,
            )
            self.assertEqual(
                document["summary"]["relatedPermitsWithoutActiveFees"],
                1,
            )
            pattern = document["parentPermitPatterns"][0]
            self.assertEqual(pattern["observed"]["parentFamilies"], 2)
            self.assertEqual(
                pattern["parentFees"][0]["overall"][
                    "shareOfEligibleParentFamilies"
                ],
                0.5,
            )
            relation = pattern["relatedPermitPatterns"][0]
            self.assertEqual(
                relation["overall"]["shareOfEligibleParentFamilies"],
                0.5,
            )
            self.assertEqual(relation["fees"], [])

            second_fee = {
                **fee_row,
                "ComputedFeeID": "CF2",
                "FeeTemplateFeeID": "TF2",
                "FeeName": "Second fee",
            }
            self.write_csv(
                fee_path,
                sorted(MODULE.REQUIRED_COLUMNS),
                [fee_row, second_fee],
            )
            with self.assertRaisesRegex(
                ValueError,
                "snapshots are not aligned",
            ):
                MODULE.build_document(
                    fee_path,
                    cohort_path,
                    template_path,
                    "both",
                )


    def test_related_permit_tracks_active_fee_family_occurrence(self):
        parent_row = {
            "ParentPermitID": "P1",
            "ParentPermitType": "Building",
            "ParentWorkClass": "New",
            "ParentAppliedDate": "2026-01-10",
            "FeeOwnerPermitID": "P1",
            "FeeOwnerRole": "Parent",
            "RelationshipFamily": "Parent Permit",
            "FeeOwnerPermitType": "Building",
            "FeeOwnerWorkClass": "New",
            "FeeOwnerHasActiveFee": "False",
            "ActiveFeeInstances": "0",
            "FamilyMemberCount": "2",
            "RelatedPermitCount": "1",
            "FamilyActiveFeeInstances": "2",
            "FamilyHasActiveFee": "True",
        }
        child_row = {
            **parent_row,
            "FeeOwnerPermitID": "C1",
            "FeeOwnerRole": "Child",
            "RelationshipFamily": "MEP",
            "FeeOwnerPermitType": "Electrical",
            "FeeOwnerWorkClass": "Sub Permit",
            "FeeOwnerHasActiveFee": "True",
            "ActiveFeeInstances": "2",
        }

        family = MODULE.new_cohort_family(parent_row, "both")
        MODULE.add_cohort_row(family, parent_row, 2)
        MODULE.add_cohort_row(family, child_row, 3)
        parents = {}
        diagnostics = {
            "parentFamilies": 0,
            "relatedPermits": 0,
            "relatedPermitsWithoutActiveFees": 0,
            "activeFeeInstances": 0,
            "activeFeeParentFamilies": 0,
            "zeroFeeParentFamilies": 0,
            "missingParentAppliedDateFamilies": 0,
        }
        MODULE.flush_cohort_family(
            family,
            parents,
            diagnostics,
            {"value": 0},
        )

        parent = next(iter(parents.values()))
        relation = next(iter(parent["relatedPermits"].values()))
        self.assertEqual(relation["families"], 1)
        self.assertEqual(relation["familiesWithActiveFee"], 1)
        self.assertEqual(relation["permitsWithActiveFee"], 1)

        finalized = MODULE.finalize_related(
            relation,
            {},
            parent,
            "both",
        )
        overall = finalized["overall"]
        self.assertEqual(
            overall["parentFamiliesWithRelatedPermitAndActiveFee"],
            1,
        )
        self.assertEqual(
            overall[
                "shareOfEligibleParentFamiliesWithRelatedPermitAndActiveFee"
            ],
            1.0,
        )
        self.assertEqual(
            overall["shareOfRelatedPermitFamiliesWithActiveFee"],
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
