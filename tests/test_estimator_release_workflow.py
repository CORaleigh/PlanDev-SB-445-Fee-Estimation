import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_module(name):
    spec = importlib.util.spec_from_file_location(
        name,
        SCRIPTS / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REFRESH = load_module("refresh_estimator")
PROMOTE = load_module("promote_estimator")
STAGE = load_module("stage_static_release_candidate")


class EstimatorReleaseWorkflowTests(unittest.TestCase):
    def test_promotion_detects_ignored_files_in_a_real_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            PROMOTE.subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
            (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
            data = root / "docs" / "data"
            data.mkdir(parents=True)
            sentinel = data / "private.log"
            sentinel.write_text("local work", encoding="utf-8")
            with patch.object(PROMOTE, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "Untracked or ignored"):
                    PROMOTE.check_destination()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "local work")

    def test_promotion_refuses_untracked_and_ignored_data_before_writing(self):
        for local_file in ("docs/data/notes.txt", "docs/data/private.log"):
            with self.subTest(local_file=local_file), \
                 patch.object(PROMOTE, "parse_args"), \
                 patch.object(PROMOTE, "tracked_worktree_clean", return_value=True), \
                 patch.object(PROMOTE, "git", return_value=local_file) as git, \
                 patch.object(PROMOTE, "publish_catalog") as publish:
                with self.assertRaisesRegex(RuntimeError, "Untracked or ignored"):
                    PROMOTE.main()
                git.assert_called_once_with("ls-files", "--others", "--", "docs/data")
                publish.assert_not_called()

    def test_promotion_preserves_an_existing_temporary_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            temporary = root / "docs" / "data.tmp"
            temporary.mkdir(parents=True)
            sentinel = temporary / "keep.txt"
            sentinel.write_text("local work", encoding="utf-8")
            with patch.object(PROMOTE, "ROOT", root):
                with self.assertRaises(FileExistsError):
                    PROMOTE.check_destination()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "local work")

    def saved_review(self, root):
        paths = {name: root / name for name in (
            "summary.json", "note.txt", "baseline.json", "clean.json", "report.json",
            "permit_fee_templates.json", "permit_fee_errors.json",
        )}
        manifest = {"catalogVersion": "published", "configurationAsOfDate": "2026-09-24"}
        summary = {
            "status": "review-required", "baselineCatalogVersion": "published",
            "baselineConfigurationAsOfDate": "2026-09-24",
            "configurationAsOfDate": "2026-10-14", "normalizedSnapshot": str(paths["clean.json"]),
            "baselineNormalizedSnapshot": str(paths["baseline.json"]),
            "changeReport": str(paths["report.json"]), "changeSummary": {"actionRequired": True},
        }
        payloads = {
            "summary.json": summary, "baseline.json": {"asOfDate": "2026-09-24"},
            "clean.json": {"asOfDate": "2026-10-14"},
            "report.json": {"summary": summary["changeSummary"]},
            "permit_fee_templates.json": {"saved": True}, "permit_fee_errors.json": [],
        }
        for name, payload in payloads.items():
            paths[name].write_text(json.dumps(payload), encoding="utf-8")
        paths["note.txt"].write_text("Approved for internal preview; keep review restrictions.", encoding="utf-8")
        return paths, manifest, summary

    def test_resume_requires_note_and_explicit_version(self):
        for arguments in (["--resume", "summary.json"], ["--review-note", "note.txt"],
                          ["--resume", "summary.json", "--review-note", "note.txt"],
                          ["--resume", "summary.json", "--review-note", "note.txt",
                           "--catalog-version", "reviewed", "--baseline-snapshot", "other.json"]):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", ["refresh", *arguments]), \
                 patch.object(sys, "stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    REFRESH.parse_args()

    def test_resume_records_saved_source_and_approval_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, manifest, _ = self.saved_review(Path(tmp))
            before = {name: path.read_bytes() for name, path in paths.items()}
            as_of, baseline, raw, errors, review = REFRESH.reviewed_refresh(
                paths["summary.json"], paths["note.txt"], manifest,
            )
            self.assertEqual(as_of, "2026-10-14")
            self.assertEqual((baseline, raw, errors), tuple(paths[name] for name in (
                "baseline.json", "permit_fee_templates.json", "permit_fee_errors.json",
            )))
            self.assertEqual(review["rawSnapshotSha256"], REFRESH.file_digest(raw))
            self.assertEqual(review["noteSha256"], REFRESH.file_digest(paths["note.txt"]))
            self.assertEqual(before, {name: path.read_bytes() for name, path in paths.items()})

    def test_resume_rejects_stale_incomplete_or_mismatched_review(self):
        for problem in ("status", "published", "errors", "date", "baseline", "report", "note", "raw"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as tmp:
                paths, manifest, summary = self.saved_review(Path(tmp))
                if problem == "status":
                    summary["status"] = "candidate-ready"
                    paths["summary.json"].write_text(json.dumps(summary), encoding="utf-8")
                elif problem == "published":
                    manifest["catalogVersion"] = "different"
                elif problem == "raw":
                    paths["permit_fee_templates.json"].unlink()
                else:
                    name, content = {
                        "errors": ("permit_fee_errors.json", "[{}]"),
                        "date": ("clean.json", "{}"), "baseline": ("baseline.json", "{}"),
                        "report": ("report.json", "{}"), "note": ("note.txt", " "),
                    }[problem]
                    paths[name].write_text(content, encoding="utf-8")
                with self.assertRaises((ValueError, FileNotFoundError)):
                    REFRESH.reviewed_refresh(paths["summary.json"], paths["note.txt"], manifest)

    def test_resumed_pipeline_never_extracts_and_preserves_review_required_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, manifest, _ = self.saved_review(root)
            original = paths["summary.json"].read_bytes()
            published = root / "published.json"
            published.write_text(json.dumps(manifest), encoding="utf-8")
            arguments = ["refresh", "--resume", str(paths["summary.json"]),
                         "--review-note", str(paths["note.txt"]), "--catalog-version", "reviewed"]
            def run(label, command, capture=False):
                if "scripts/permit_fee_extract.py" in command:
                    self.fail("Resume called the live extractor.")
                if "scripts/compare_fee_template_snapshots.py" in command:
                    Path(command[command.index("--output") + 1]).write_text(
                        json.dumps({"summary": {"actionRequired": True}}), encoding="utf-8",
                    )
                return ""
            inputs = {"historicalUsage": paths["permit_fee_templates.json"],
                      "inspectionFeeMap": "map.csv", "inspectionTemplates": "inspection.json",
                      "inspectionValidation": "validation.json", "fixedFeeBehavior": "fixed.csv",
                      "choiceLabels": "choices.csv", "inputPrevalence": [], "assessmentEvidence": []}
            audit = dict(options=1, files=1, jsonFiles=1, bytes=1,
                         externalRuntimeUrls=[], emailLikeValues=[], findings=[])
            with patch.object(REFRESH, "ROOT", root), patch.object(REFRESH, "OUT", root / "output-prod"), \
                 patch.object(REFRESH, "PUBLISHED", published), patch.object(sys, "argv", arguments), \
                 patch.object(REFRESH, "load_refresh_inputs", return_value=inputs), \
                 patch.object(REFRESH, "required_refresh_inputs", return_value=[]), \
                 patch.object(REFRESH, "require_files"), patch.object(REFRESH, "history_is_compatible", return_value=True), \
                 patch.object(REFRESH, "git_state", return_value=("test", "current-head")), \
                 patch.object(REFRESH, "run", side_effect=run), \
                 patch.object(REFRESH, "stage_release_candidate", return_value=dict(sha256="digest", files=1, bytes=1)), \
                 patch.object(REFRESH, "audit_public_site", return_value=audit), \
                 patch.object(sys, "stdout", new_callable=io.StringIO):
                self.assertEqual(REFRESH.main(), 0)
            result = REFRESH.load_json(root / "output-prod/refresh-reviewed/estimator-refresh-summary.json")
            self.assertEqual(result["status"], "candidate-ready")
            self.assertTrue(result["changeSummary"]["actionRequired"])
            self.assertEqual(result["sourceGitHead"], "current-head")
            self.assertEqual(result["configurationAsOfDate"], "2026-10-14")
            self.assertIn("review", result)
            self.assertEqual(paths["summary.json"].read_bytes(), original)

    def test_live_refresh_still_stops_for_unapproved_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths, manifest, _ = self.saved_review(root)
            published = root / "published.json"
            published.write_text(json.dumps(manifest), encoding="utf-8")
            inputs = {"baselineSnapshot": paths["baseline.json"]}
            def run(label, command, capture=False):
                if "scripts/permit_fee_extract.py" in command:
                    directory = Path(command[command.index("--output-dir") + 1])
                    (directory / "permit_fee_errors.json").write_text("[]", encoding="utf-8")
                if "scripts/compare_fee_template_snapshots.py" in command:
                    Path(command[command.index("--output") + 1]).write_text(
                        json.dumps({"summary": {"actionRequired": True}}), encoding="utf-8",
                    )
                return ""
            with patch.object(REFRESH, "ROOT", root), patch.object(REFRESH, "OUT", root / "output-prod"), \
                 patch.object(REFRESH, "PUBLISHED", published), \
                 patch.object(sys, "argv", ["refresh", "--as-of", "2026-10-14"]), \
                 patch.object(REFRESH, "load_refresh_inputs", return_value=inputs), \
                 patch.object(REFRESH, "required_refresh_inputs", return_value=[]), \
                 patch.object(REFRESH, "git_state", return_value=("test", "head")), \
                 patch.object(REFRESH, "run", side_effect=run), \
                 patch.object(REFRESH, "stage_release_candidate") as stage, \
                 patch.object(sys, "stdout", new_callable=io.StringIO):
                self.assertEqual(REFRESH.main(), 2)
                stage.assert_not_called()
            result = REFRESH.load_json(root / "output-prod/refresh-2026-10-14/estimator-refresh-summary.json")
            self.assertEqual(result["status"], "review-required")
            self.assertNotIn("review", result)

    def test_refresh_finds_dated_published_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output-prod"
            snapshot = (
                output
                / "refresh-2026-09-24"
                / "permit_fee_templates_clean.json"
            )
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(
                json.dumps({"asOfDate": "2026-09-24"}),
                encoding="utf-8",
            )

            old_root, old_out = REFRESH.ROOT, REFRESH.OUT
            REFRESH.ROOT, REFRESH.OUT = root, output
            try:
                found = REFRESH.baseline_snapshot(
                    {"configurationAsOfDate": "2026-09-24"}
                )
            finally:
                REFRESH.ROOT, REFRESH.OUT = old_root, old_out

            self.assertEqual(found, snapshot)

    def test_refresh_prefers_matching_prior_summary_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output-prod"
            refresh = output / "refresh-2026-10-02"
            refresh.mkdir(parents=True)
            snapshot = refresh / "permit_fee_templates_clean.json"
            snapshot.write_text(
                json.dumps({"asOfDate": "2026-10-02"}),
                encoding="utf-8",
            )
            (
                refresh / "estimator-refresh-summary.json"
            ).write_text(
                json.dumps(
                    {
                        "configurationAsOfDate": "2026-10-02",
                        "normalizedSnapshot": (
                            "output-prod/refresh-2026-10-02/"
                            "permit_fee_templates_clean.json"
                        ),
                    }
                ),
                encoding="utf-8",
            )

            old_root, old_out = REFRESH.ROOT, REFRESH.OUT
            REFRESH.ROOT, REFRESH.OUT = root, output
            try:
                found = REFRESH.baseline_snapshot(
                    {"configurationAsOfDate": "2026-10-02"}
                )
            finally:
                REFRESH.ROOT, REFRESH.OUT = old_root, old_out

            self.assertEqual(found, snapshot)

    def test_newer_review_required_refresh_blocks_older_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            ready_dir = output / "refresh-2026-10-01"
            review_dir = output / "refresh-2026-10-02"
            ready_dir.mkdir()
            review_dir.mkdir()

            ready = ready_dir / "estimator-refresh-summary.json"
            review = review_dir / "estimator-refresh-summary.json"
            ready.write_text(
                json.dumps({"status": "candidate-ready"}),
                encoding="utf-8",
            )
            review.write_text(
                json.dumps({"status": "review-required"}),
                encoding="utf-8",
            )
            os.utime(review, (200, 200))
            os.utime(ready, (100, 100))

            with self.assertRaisesRegex(RuntimeError, "newest"):
                PROMOTE.latest_ready_summary(output)

    def test_history_compatibility_requires_schema_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "old.json"
            current = root / "current.json"
            old.write_text(
                json.dumps({"schemaVersion": "1.2"}),
                encoding="utf-8",
            )
            current.write_text(
                json.dumps({"schemaVersion": "2.0"}),
                encoding="utf-8",
            )

            self.assertFalse(
                REFRESH.history_is_compatible(old)
            )
            self.assertTrue(
                REFRESH.history_is_compatible(current)
            )

    def test_refresh_input_config_resolves_repository_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            payload = {
                "baselineSnapshot": "local-data/baseline.json",
                "inspectionTemplates": "local-data/inspection.json",
                "inspectionValidation": "local-data/validation.json",
                "inspectionFeeMap": "local-data/inspection.csv",
                "historicalUsage": "local-data/history.json",
                "historicalFeeDetail": "local-data/detail.csv",
                "historicalCohort": "local-data/cohort.csv",
                "fixedFeeBehavior": "local-data/fixed.csv",
                "choiceLabels": "local-data/choices.csv",
                "inputPrevalence": ["local-data/prevalence.csv"],
                "assessmentEvidence": ["local-data/assessment.csv"],
            }
            config.write_text(json.dumps(payload), encoding="utf-8")

            old_root = REFRESH.ROOT
            REFRESH.ROOT = root
            try:
                actual = REFRESH.load_refresh_inputs(config)
            finally:
                REFRESH.ROOT = old_root

            self.assertEqual(
                actual["inspectionTemplates"],
                root / "local-data" / "inspection.json",
            )
            self.assertEqual(
                actual["inputPrevalence"],
                [root / "local-data" / "prevalence.csv"],
            )

    def test_refresh_input_preflight_selects_history_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "history.json"
            history.write_text(
                json.dumps({"schemaVersion": "2.1"}),
                encoding="utf-8",
            )
            inputs = {
                "inspectionFeeMap": root / "inspection.csv",
                "inspectionTemplates": root / "inspection.json",
                "inspectionValidation": root / "validation.json",
                "fixedFeeBehavior": root / "fixed.csv",
                "choiceLabels": root / "choices.csv",
                "inputPrevalence": [root / "prevalence.csv"],
                "assessmentEvidence": [root / "assessment.csv"],
                "historicalUsage": history,
                "historicalFeeDetail": root / "detail.csv",
                "historicalCohort": root / "cohort.csv",
            }

            reused = REFRESH.required_refresh_inputs(inputs)
            rebuilt = REFRESH.required_refresh_inputs(
                inputs,
                refresh_history=True,
            )

            self.assertIn(history, reused)
            self.assertNotIn(inputs["historicalFeeDetail"], reused)
            self.assertNotIn(history, rebuilt)
            self.assertIn(inputs["historicalFeeDetail"], rebuilt)
            self.assertIn(inputs["historicalCohort"], rebuilt)

    def test_missing_files_reports_every_missing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "existing.csv"
            existing.write_text("value\n", encoding="utf-8")

            actual = REFRESH.missing_files(
                [existing, root / "first.csv", root / "second.json"]
            )

            self.assertEqual(len(actual), 2)
            self.assertTrue(actual[0].endswith("first.csv"))
            self.assertTrue(actual[1].endswith("second.json"))

    def test_release_only_requires_docs_data(self):
        self.assertTrue(
            PROMOTE.release_only(
                [
                    "docs/data/manifest.json",
                    "docs/data/options/a.json",
                ]
            )
        )
        self.assertFalse(PROMOTE.release_only([]))
        self.assertFalse(
            PROMOTE.release_only(
                [
                    "docs/app.js",
                    "docs/data/manifest.json",
                ]
            )
        )

    def test_candidate_digest_must_match_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            (candidate / "data" / "options").mkdir(parents=True)

            for name, body in {
                ".nojekyll": "",
                "index.html": "<main></main>",
                "app.js": "export {};",
                "runtime.js": "export {};",
                "site-config.js": "export {};",
                "styles.css": "body {}",
            }.items():
                (candidate / name).write_text(
                    body,
                    encoding="utf-8",
                )

            images = candidate / "images"
            images.mkdir()
            for name in STAGE.REQUIRED_IMAGE_FILES:
                (images / name).write_bytes(b"test-image")

            manifest = {
                "schemaVersion": "public-1.0",
                "catalogVersion": "test-r1",
                "configurationAsOfDate": "2026-10-02",
                "options": [{"path": "options/a.json"}],
            }
            (candidate / "data" / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            (
                candidate / "data" / "options" / "a.json"
            ).write_text(
                "{}",
                encoding="utf-8",
            )

            _, files = PROMOTE._validate_candidate(candidate)
            digest, _ = PROMOTE._candidate_digest(
                candidate,
                files,
            )
            summary = {
                "candidateDir": "candidate",
                "candidateSha256": digest,
                "catalogVersion": "test-r1",
                "configurationAsOfDate": "2026-10-02",
            }

            old_root = PROMOTE.ROOT
            PROMOTE.ROOT = root
            try:
                verified, _, actual = PROMOTE.verify_candidate(
                    summary
                )
                self.assertEqual(verified, candidate)
                self.assertEqual(actual, digest)

                summary["candidateSha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "digest"):
                    PROMOTE.verify_candidate(summary)
            finally:
                PROMOTE.ROOT = old_root


if __name__ == "__main__":
    unittest.main()
