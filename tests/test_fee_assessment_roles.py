import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SCRIPT = SCRIPTS_DIR / "fee_assessment_roles.py"
SPEC = importlib.util.spec_from_file_location(
    "fee_assessment_roles",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fee(
    fee_id,
    name,
    *,
    dependency_ids=None,
    occurrence=None,
):
    inputs = [
        {
            "source": "fee",
            "inputKey": dependency_id,
        }
        for dependency_id in (dependency_ids or [])
    ]
    return {
        "feeTemplateFeeId": fee_id,
        "name": name,
        "calculation": {
            "rule": {
                "inputs": inputs,
            }
        },
        "occurrence": occurrence or {"status": "unavailable"},
    }


class FeeAssessmentRoleTests(unittest.TestCase):
    def test_hidden_unobserved_dependency_is_dependency_only(self):
        fees = [
            fee("hidden", "Rate (Hidden)"),
            fee(
                "visible",
                "Visible Fee",
                dependency_ids=["hidden"],
                occurrence={
                    "status": "available",
                    "numerator": 10,
                    "denominator": 100,
                },
            ),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=100,
        )

        self.assertEqual(
            roles["hidden"]["role"],
            "dependency-only",
        )
        self.assertEqual(
            roles["hidden"]["directAssessmentEvidence"],
            "no-direct-assessment-observed",
        )
        self.assertEqual(
            roles["hidden"]["referencedByFeeIds"],
            ["visible"],
        )

    def test_hidden_dependency_with_direct_assessment_remains_assessed(self):
        fees = [
            fee(
                "hidden",
                "Rate (Hidden)",
                occurrence={
                    "status": "available",
                    "numerator": 3,
                    "denominator": 100,
                },
            ),
            fee("visible", "Visible Fee", dependency_ids=["hidden"]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=100,
        )

        self.assertEqual(
            roles["hidden"]["role"],
            "dependency-and-assessed",
        )

    def test_unobserved_nonhidden_dependency_stays_unresolved(self):
        fees = [
            fee("base", "Base Fee"),
            fee("wrapper", "Wrapper Fee", dependency_ids=["base"]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=100,
        )

        self.assertEqual(
            roles["base"]["role"],
            "dependency-unresolved",
        )

    def test_hidden_dependency_without_history_stays_unresolved(self):
        fees = [
            fee("hidden", "Rate [Hidden]"),
            fee("visible", "Visible Fee", dependency_ids=["hidden"]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=0,
        )

        self.assertEqual(
            roles["hidden"]["role"],
            "dependency-unresolved",
        )

    def test_directly_assessed_dependency_is_not_hidden_from_subtotal(self):
        fees = [
            fee(
                "base",
                "Base Fee",
                occurrence={
                    "status": "available",
                    "numerator": 25,
                    "denominator": 100,
                },
            ),
            fee("wrapper", "Wrapper Fee", dependency_ids=["base"]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=100,
        )

        self.assertEqual(
            roles["base"]["role"],
            "dependency-and-assessed",
        )

    def test_validated_legacy_dependency_moves_into_catalog_role(self):
        legacy_id = "7728caad-06b5-438f-88b3-e637cdec2b97"
        fees = [
            fee(legacy_id, "Baseline Permit Fee"),
            fee("wrapper", "Visible Fee", dependency_ids=[legacy_id]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=100,
            assessment_evidence={
                legacy_id: {
                    "validatedDependencyOnlyBasis":
                        "validated historical direct-assessment audit",
                }
            },
        )

        self.assertEqual(
            roles[legacy_id]["role"],
            "dependency-only",
        )
        self.assertIn(
            "validated historical",
            roles[legacy_id]["basis"],
        )


    def test_hidden_dependency_can_use_imported_direct_assessment_evidence(self):
        fees = [
            fee("hidden", "Rate (Hidden)"),
            fee("visible", "Visible Fee", dependency_ids=["hidden"]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=0,
            assessment_evidence={
                "hidden": {
                    "historicalDenominator": 2737,
                    "activeDirectFeeRows": 0,
                }
            },
        )

        self.assertEqual(roles["hidden"]["role"], "dependency-only")
        self.assertEqual(
            roles["hidden"]["directAssessmentEvidence"],
            "no-direct-assessment-observed",
        )
        self.assertEqual(
            roles["hidden"]["directAssessmentEvidenceSource"],
            "direct-assessment-import",
        )
        self.assertEqual(
            roles["hidden"]["historicalDenominator"],
            2737,
        )

    def test_imported_positive_assessment_keeps_hidden_dependency_assessed(self):
        fees = [
            fee("hidden", "Rate (Hidden)"),
            fee("visible", "Visible Fee", dependency_ids=["hidden"]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=0,
            assessment_evidence={
                "hidden": {
                    "historicalDenominator": 2737,
                    "activeDirectFeeRows": 4,
                }
            },
        )

        self.assertEqual(
            roles["hidden"]["role"],
            "dependency-and-assessed",
        )

    def test_imported_zero_does_not_override_positive_historical_evidence(self):
        fees = [
            fee(
                "hidden",
                "Rate (Hidden)",
                occurrence={
                    "status": "available",
                    "numerator": 2,
                    "denominator": 100,
                },
            ),
            fee("visible", "Visible Fee", dependency_ids=["hidden"]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=100,
            assessment_evidence={
                "hidden": {
                    "historicalDenominator": 2737,
                    "activeDirectFeeRows": 0,
                }
            },
        )

        self.assertEqual(
            roles["hidden"]["role"],
            "dependency-and-assessed",
        )
        self.assertEqual(
            roles["hidden"]["directAssessmentEvidenceSource"],
            "conflicting-direct-assessment-evidence",
        )


    def test_positive_import_overrides_validated_legacy_dependency(self):
        legacy_id = "7728caad-06b5-438f-88b3-e637cdec2b97"
        fees = [
            fee(legacy_id, "Baseline Permit Fee"),
            fee("wrapper", "Visible Fee", dependency_ids=[legacy_id]),
        ]

        roles = MODULE.classify_assessment_roles(
            fees,
            historical_denominator=0,
            assessment_evidence={
                legacy_id: {
                    "historicalDenominator": 100,
                    "activeDirectFeeRows": 1,
                    "validatedDependencyOnlyBasis":
                        "validated historical direct-assessment audit",
                }
            },
        )

        self.assertEqual(
            roles[legacy_id]["role"],
            "dependency-and-assessed",
        )


if __name__ == "__main__":
    unittest.main()
