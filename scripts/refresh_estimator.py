from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from audit_public_static_site import audit_public_site
from build_estimator_catalog import ESTIMATION_REVIEW_PATH, load_estimation_review
from stage_static_release_candidate import stage_release_candidate


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output-prod"
PUBLISHED = ROOT / "docs" / "data" / "manifest.json"
DEFAULT_INPUTS_CONFIG = ROOT / "config" / "refresh-inputs.json"

REQUIRED_INPUT_KEYS = {
    "inspectionTemplates",
    "inspectionValidation",
    "inspectionFeeMap",
    "historicalUsage",
    "historicalFeeDetail",
    "historicalCohort",
    "fixedFeeBehavior",
    "choiceLabels",
    "inputPrevalence",
    "assessmentEvidence",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def configured_path(value):
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def load_refresh_inputs(path):
    path = configured_path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Refresh input configuration was not found: {path}. "
            "Copy config/refresh-inputs.example.json to "
            "config/refresh-inputs.json and set the local paths."
        )

    payload = load_json(path)
    missing = sorted(REQUIRED_INPUT_KEYS - set(payload))
    if missing:
        raise ValueError(
            "Refresh input configuration is missing: "
            + ", ".join(missing)
        )

    result = {
        key: configured_path(payload[key])
        for key in REQUIRED_INPUT_KEYS
        if key not in {"inputPrevalence", "assessmentEvidence"}
    }
    for key in ("inputPrevalence", "assessmentEvidence"):
        values = payload[key]
        if not isinstance(values, list) or not values:
            raise ValueError(f"Refresh input configuration {key} must be a list.")
        result[key] = [configured_path(value) for value in values]

    baseline = payload.get("baselineSnapshot")
    result["baselineSnapshot"] = (
        configured_path(baseline) if baseline else None
    )
    return result


def rel(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def git_state():
    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise RuntimeError(
            "Tracked changes are present. Commit, restore, or stash them first."
        )
    return git("branch", "--show-current"), git("rev-parse", "HEAD")


def baseline_snapshot(manifest, explicit=None):
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    as_of = str(manifest.get("configurationAsOfDate") or "")
    for summary_path in sorted(
        OUT.glob("refresh-*/estimator-refresh-summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        try:
            summary = load_json(summary_path)
        except (OSError, json.JSONDecodeError):
            continue
        if summary.get("configurationAsOfDate") != as_of:
            continue
        candidate = ROOT / str(summary.get("normalizedSnapshot") or "")
        if candidate.is_file():
            return candidate

    candidates = [
        OUT / f"refresh-{as_of}/permit_fee_templates_clean.json",
        OUT / "permit_fee_templates_clean.json",
    ]
    for path in candidates:
        if path.is_file() and load_json(path).get("asOfDate") == as_of:
            return path

    raise FileNotFoundError(
        f"No normalized source snapshot was found for published date {as_of}. "
        "Use --baseline-snapshot."
    )


def run(label, command, capture=False):
    print(f"\n== {label} ==")
    result = subprocess.run(
        [str(value) for value in command],
        cwd=ROOT,
        text=True,
        capture_output=capture,
    )
    if result.returncode:
        if capture and result.stdout:
            print(result.stdout.rstrip())
        if capture and result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}."
        )
    return result.stdout if capture else ""


def missing_files(paths):
    return [rel(path) for path in paths if not Path(path).is_file()]


def require_files(paths):
    missing = missing_files(paths)
    if missing:
        raise FileNotFoundError(
            "Missing required evidence:\n- " + "\n- ".join(missing)
        )


def runtime_summary(text):
    prefixes = (
        "Catalog:",
        "Options:",
        "UI display-ready options:",
        "Options with at least one structurally supported automatic fee:",
        "Fully structurally supported options:",
        "Structurally supported automatic fees:",
        "Structurally supported assessable automatic fees:",
        "Dependency-only automatic fee assignments:",
        "Blocked automatic fee rows with applicability diagnostics:",
    )
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(prefixes)
    ]


def write_summary(path, payload):
    path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def history_is_compatible(path):
    path = Path(path)
    if not path.is_file():
        return False
    try:
        version = str(load_json(path).get("schemaVersion") or "")
    except (OSError, json.JSONDecodeError):
        return False
    return version.startswith("2.")


def required_refresh_inputs(inputs, refresh_history=False):
    paths = [
        inputs["inspectionFeeMap"],
        inputs["inspectionTemplates"],
        inputs["inspectionValidation"],
        inputs["fixedFeeBehavior"],
        inputs["choiceLabels"],
        *inputs["inputPrevalence"],
        *inputs["assessmentEvidence"],
    ]
    history = inputs["historicalUsage"]
    if refresh_history or not history_is_compatible(history):
        paths.extend(
            [inputs["historicalFeeDetail"], inputs["historicalCohort"]]
        )
    else:
        paths.append(history)
    return paths


def file_digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def reviewed_refresh(summary_path, note_path, published):
    summary_path = configured_path(summary_path)
    note_path = configured_path(note_path)
    summary = load_json(summary_path)
    if summary.get("status") != "review-required":
        raise ValueError("Resume requires a review-required refresh summary.")
    for key in ("CatalogVersion", "ConfigurationAsOfDate"):
        if summary.get("baseline" + key) != published.get(key[0].lower() + key[1:]):
            raise ValueError("The published baseline changed; start a new review.")
    as_of = summary["configurationAsOfDate"]
    date.fromisoformat(as_of)
    clean = configured_path(summary["normalizedSnapshot"])
    baseline = configured_path(summary["baselineNormalizedSnapshot"])
    raw = configured_path(summary.get("rawSnapshot") or clean.with_name("permit_fee_templates.json"))
    errors = configured_path(summary.get("extractionErrors") or raw.with_name("permit_fee_errors.json"))
    report = configured_path(summary["changeReport"])
    require_files([raw, clean, errors, baseline, report, note_path])
    if load_json(errors):
        raise ValueError("The saved extraction contains errors; it cannot be resumed.")
    if load_json(clean).get("asOfDate") != as_of:
        raise ValueError("The saved snapshot date does not match its summary.")
    if load_json(baseline).get("asOfDate") != published.get("configurationAsOfDate"):
        raise ValueError("The saved baseline date does not match the published data.")
    if load_json(report).get("summary") != summary.get("changeSummary"):
        raise ValueError("The saved change report does not match its summary.")
    if not note_path.read_text(encoding="utf-8").strip():
        raise ValueError("The review note must document the approval and restrictions.")
    review = {
        "resumedFrom": rel(summary_path),
        "summarySha256": file_digest(summary_path),
        "changeReport": rel(report),
        "changeReportSha256": file_digest(report),
        "note": rel(note_path),
        "noteSha256": file_digest(note_path),
        "rawSnapshotSha256": file_digest(raw),
        "baselineSha256": file_digest(baseline),
    }
    return as_of, baseline, raw, errors, review


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Refresh, validate, and stage a new estimator catalog without "
            "publishing it."
        )
    )
    parser.add_argument("--as-of")
    parser.add_argument("--baseline-snapshot")
    parser.add_argument("--catalog-version")
    parser.add_argument("--refresh-history", action="store_true")
    parser.add_argument("--resume", help="Saved review-required refresh summary; makes no API requests.")
    parser.add_argument("--review-note", help="Local note recording approval of the saved changes.")
    parser.add_argument(
        "--check-inputs",
        action="store_true",
        help="Check the configured local inputs without calling EnerGov.",
    )
    parser.add_argument(
        "--inputs-config",
        default=str(DEFAULT_INPUTS_CONFIG),
        help=(
            "Local refresh input configuration. Relative paths inside the "
            "file are resolved from the repository root."
        ),
    )
    args = parser.parse_args()
    if bool(args.resume) != bool(args.review_note):
        parser.error("--resume and --review-note must be supplied together.")
    if args.resume and (not args.catalog_version or args.baseline_snapshot):
        parser.error("Resume requires a new --catalog-version and uses its saved baseline.")
    return args


def main():
    args = parse_args()
    load_estimation_review(ESTIMATION_REVIEW_PATH)
    inputs = load_refresh_inputs(args.inputs_config)
    published = load_json(PUBLISHED)
    review = None
    if args.resume:
        as_of, baseline, saved_raw, saved_errors, review = reviewed_refresh(
            args.resume, args.review_note, published,
        )
        if args.as_of and args.as_of != as_of:
            raise ValueError("Resume must retain the saved configuration date.")
        args.as_of = as_of
    else:
        args.as_of = args.as_of or date.today().isoformat()
        explicit = args.baseline_snapshot or inputs["baselineSnapshot"]
        baseline = (
            configured_path(explicit) if args.check_inputs and explicit
            else baseline_snapshot(published, explicit)
        )
    date.fromisoformat(args.as_of)
    version = args.catalog_version or f"{args.as_of}-r7-refresh"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
        raise ValueError("Catalog version must be a simple filename without path separators.")
    supporting_inputs = required_refresh_inputs(
        inputs,
        refresh_history=args.refresh_history,
    )
    if args.check_inputs:
        missing = missing_files([baseline, *supporting_inputs])
        if missing:
            print("REFRESH INPUT CHECK FAILED", file=sys.stderr)
            for path in missing:
                print(f"- {path}", file=sys.stderr)
            return 2
        print("REFRESH INPUT CHECK PASS")
        print(f"Baseline: {rel(baseline)}")
        print(f"Supporting files: {len(supporting_inputs)}")
        return 0

    require_files(supporting_inputs)
    branch, head = git_state()

    refresh_dir = OUT / f"refresh-{version if review else args.as_of}"
    catalog = OUT / f"catalog-{version}"
    candidate = OUT / f"static-release-candidate-{version}"
    for path in (refresh_dir, catalog, candidate, candidate.with_name(candidate.name + ".tmp")):
        if path.exists():
            raise FileExistsError(f"Output already exists: {rel(path)}")
    refresh_dir.mkdir(parents=True)

    raw = refresh_dir / "permit_fee_templates.json"
    clean = refresh_dir / "permit_fee_templates_clean.json"
    errors_path = refresh_dir / "permit_fee_errors.json"
    report_path = refresh_dir / "permit_template_change_report.json"
    summary_path = refresh_dir / "estimator-refresh-summary.json"
    python = sys.executable

    if review:
        raw, errors_path = saved_raw, saved_errors
        print(f"\n== 1/8 Reuse reviewed extract: {rel(raw)} ==")
    else:
        run(
            "1/8 Extract production permit templates",
            [
                python,
                "scripts/permit_fee_extract.py",
                "--environment",
                "prod",
                "--output-dir",
                refresh_dir,
            ],
        )
    errors = load_json(errors_path)
    if errors:
        raise RuntimeError(
            f"Production extraction reported {len(errors)} error(s)."
        )

    run(
        "2/8 Normalize templates",
        [
            python,
            "scripts/clean_permit_fee_templates.py",
            raw,
            "--output",
            clean,
            "--as-of",
            args.as_of,
            "--compact",
        ],
    )
    output = run(
        "3/8 Compare with published source",
        [
            python,
            "scripts/compare_fee_template_snapshots.py",
            baseline,
            clean,
            "--output",
            report_path,
            "--report-only",
        ],
        capture=True,
    )
    print(output.rstrip())
    report = load_json(report_path)

    base = {
        "schemaVersion": "1.0",
        "sourceGitBranch": branch,
        "sourceGitHead": head,
        "baselineCatalogVersion": published.get("catalogVersion"),
        "baselineConfigurationAsOfDate": published.get(
            "configurationAsOfDate"
        ),
        "baselineNormalizedSnapshot": rel(baseline),
        "configurationAsOfDate": args.as_of,
        "normalizedSnapshot": rel(clean),
        "rawSnapshot": rel(raw),
        "extractionErrors": rel(errors_path),
        "changeReport": rel(report_path),
        "changeSummary": report.get("summary") or {},
    }
    if review:
        base["review"] = review
    if (report.get("summary") or {}).get("actionRequired") and not review:
        write_summary(
            summary_path,
            {**base, "status": "review-required"},
        )
        print("\nREFRESH STOPPED FOR REVIEW")
        print(f"Report: {rel(report_path)}")
        return 2

    write_summary(summary_path, {**base, "status": "building"})
    history = inputs["historicalUsage"]
    if args.refresh_history or not history_is_compatible(history):
        history = refresh_dir / "permit_fee_usage_patterns.json"
        require_files(
            [inputs["historicalFeeDetail"], inputs["historicalCohort"]]
        )
        run(
            "4/8 Rebuild historical usage",
            [
                python,
                "scripts/build_fee_usage_patterns.py",
                inputs["historicalFeeDetail"],
                "--cohort-csv",
                inputs["historicalCohort"],
                "--templates-json",
                clean,
                "--output",
                history,
                "--period-grain",
                "both",
                "--compact",
            ],
        )
    else:
        print("\n== 4/8 Historical usage: reuse existing evidence ==")

    require_files(
        [
            history,
            inputs["inspectionFeeMap"],
            inputs["inspectionTemplates"],
            inputs["inspectionValidation"],
            inputs["fixedFeeBehavior"],
            inputs["choiceLabels"],
            *inputs["inputPrevalence"],
            *inputs["assessmentEvidence"],
        ]
    )
    remapped = (
        refresh_dir / "permit_fee_usage_patterns_cohort_remapped.json"
    )
    run(
        "5/8 Remap historical evidence",
        [
            python,
            "scripts/remap_fee_usage_patterns.py",
            history,
            "--templates-json",
            clean,
            "--inspection-fee-map",
            inputs["inspectionFeeMap"],
            "--output",
            remapped,
            "--audit-csv",
            refresh_dir / "permit_fee_configuration_match_audit.csv",
        ],
        capture=True,
    )
    print("Historical evidence remap: PASS")

    command = [
        python,
        "scripts/build_estimator_catalog.py",
        "--permit-templates",
        clean,
        "--inspection-templates",
        inputs["inspectionTemplates"],
        "--rule-validation",
        inputs["inspectionValidation"],
        "--usage-json",
        remapped,
        "--fixed-fee-behavior",
        inputs["fixedFeeBehavior"],
        "--disclosures",
        "schema/disclosure-codes.json",
        "--choice-labels",
        inputs["choiceLabels"],
        "--require-input-prevalence",
    ]
    for path in inputs["inputPrevalence"]:
        command += ["--input-prevalence", path]
    for path in inputs["assessmentEvidence"]:
        command += ["--assessment-evidence", path]
    command += [
        "--output-dir",
        catalog,
        "--catalog-version",
        version,
    ]
    run("6/8 Build catalog", command, capture=True)
    print(f"Catalog: {rel(catalog)}")

    run(
        "7/8 Repository tests",
        [
            python,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
            "-q",
        ],
        capture=True,
    )
    staged = stage_release_candidate(
        catalog,
        ROOT / "docs",
        candidate,
    )

    coverage = run(
        "8/8 Public runtime and release audit",
        [
            "node",
            "scripts/audit_static_runtime_coverage.mjs",
            "--catalog-dir",
            candidate / "data",
        ],
        capture=True,
    )
    coverage_lines = runtime_summary(coverage)
    for line in coverage_lines:
        print(line)

    audit = audit_public_site(candidate)

    payload = {
        **base,
        "status": "candidate-ready",
        "catalogVersion": version,
        "catalogDir": rel(catalog),
        "historicalUsageSource": rel(history),
        "remappedUsage": rel(remapped),
        "candidateDir": rel(candidate),
        "candidateSha256": staged["sha256"],
        "candidateFiles": staged["files"],
        "candidateBytes": staged["bytes"],
        "runtimeCoverageSummary": coverage_lines,
        "publicAudit": {
            "options": audit["options"],
            "files": audit["files"],
            "jsonFiles": audit["jsonFiles"],
            "bytes": audit["bytes"],
            "externalRuntimeUrls": audit["externalRuntimeUrls"],
            "emailLikeValues": audit["emailLikeValues"],
            "findings": audit["findings"],
        },
    }
    write_summary(summary_path, payload)

    print("\n== Release candidate ready ==")
    print("REFRESH CANDIDATE READY")
    print(f"Catalog: {version}")
    print(f"Candidate SHA-256: {staged['sha256']}")
    print(f"Review: {rel(report_path)}")
    print(
        r"Next: env\Scripts\python.exe scripts\promote_estimator.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
