import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SCRIPTS = {
    "analyze_fixed_fee_behavior.py",
    "build_fee_assessment_evidence_query.py",
    "build_fee_usage_patterns.py",
    "build_option_input_prevalence_query.py",
    "clean_inspection_fee_templates.py",
    "inspection_fee_extract.py",
    "normalize_input_prevalence_results.py",
    "refresh_estimator.py",
    "run_query.py",
    "summarize_refresh_changes.py",
    "validate_inspection_fee_rules.py",
}

REQUIRED_QUERIES = {
    "12_inspection_fee_condition_statuses.sql",
    "13_technology_surcharge_formula_validation.sql",
    "15_permit_family_cohort.sql",
    "116_authoritative_condition_choice_labels.sql",
    "117_condition_choice_label_gaps.sql",
    "122_building_accessory_input_prevalence.sql",
    "123_row_occupancy_scenario_prevalence.sql",
    "permit_family_fee_detail.sql",
}


class UpdateWorkflowAssetTests(unittest.TestCase):
    def test_refresh_requires_all_option_field_history(self):
        config = json.loads((ROOT / "config/refresh-inputs.example.json").read_text(encoding="utf-8"))
        self.assertEqual(config["inputPrevalence"], ["local-data/input-prevalence/result-02.csv"])
        refresh = (ROOT / "scripts/refresh_estimator.py").read_text(encoding="utf-8")
        self.assertIn('"--require-input-prevalence"', refresh)
        self.assertIn("--all", (ROOT / "UPDATE.md").read_text(encoding="utf-8"))

    def test_required_update_sources_are_retained(self):
        for name in REQUIRED_SCRIPTS:
            self.assertTrue((ROOT / "scripts" / name).is_file(), name)
        for name in REQUIRED_QUERIES:
            self.assertTrue((ROOT / "queries" / name).is_file(), name)

    def test_example_inputs_are_local_and_ignored(self):
        config = json.loads(
            (ROOT / "config" / "refresh-inputs.example.json").read_text(
                encoding="utf-8"
            )
        )
        paths = [
            value
            for key, value in config.items()
            if key != "schemaVersion"
            for value in (value if isinstance(value, list) else [value])
            if value
        ]
        self.assertTrue(paths)
        self.assertTrue(all(str(path).startswith("local-data/") for path in paths))

        result = subprocess.run(
            ["git", "check-ignore", "local-data/test.csv"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_query_dependencies_are_declared(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pandas==", requirements)
        self.assertIn("pyodbc==", requirements)
        self.assertIn("python-dotenv==", requirements)


if __name__ == "__main__":
    unittest.main()
