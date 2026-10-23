from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from audit_public_static_site import audit_public_site
from publish_static_site_catalog import publish_catalog
from stage_static_release_candidate import (
    _candidate_digest,
    _validate_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output-prod"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rel(path):
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def git(*args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
    ).stdout.strip()


def tracked_worktree_clean():
    return not git(
        "status",
        "--porcelain",
        "--untracked-files=no",
    )


def check_destination():
    temporary = ROOT / "docs" / "data.tmp"
    if temporary.exists():
        raise FileExistsError(f"Temporary data directory already exists: {temporary}")
    local_files = git("ls-files", "--others", "--", "docs/data")
    if local_files:
        raise RuntimeError(
            "Untracked or ignored files are present under docs/data. "
            "Move them outside that directory before promotion."
        )


def latest_ready_summary(output_root=OUT):
    candidates = sorted(
        Path(output_root).glob(
            "refresh-*/estimator-refresh-summary.json"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        status = payload.get("status")
        if status != "candidate-ready":
            raise RuntimeError(
                "The newest estimator refresh is not candidate-ready "
                f"(status={status!r}). Resolve that refresh before promotion."
            )
        return path, payload
    raise FileNotFoundError(
        "No estimator refresh summary was found."
    )


def verify_candidate(summary):
    candidate = ROOT / str(summary.get("candidateDir") or "")
    if not candidate.is_dir():
        raise FileNotFoundError(
            f"Release candidate was not found: {candidate}"
        )

    manifest, files = _validate_candidate(candidate)
    digest, _ = _candidate_digest(candidate, files)
    expected = str(summary.get("candidateSha256") or "")
    if not expected or digest != expected:
        raise ValueError(
            "Release candidate digest does not match the reviewed "
            "refresh summary."
        )
    if manifest.get("catalogVersion") != summary.get("catalogVersion"):
        raise ValueError(
            "Release candidate catalog version does not match "
            "the refresh summary."
        )
    if manifest.get("configurationAsOfDate") != summary.get(
        "configurationAsOfDate"
    ):
        raise ValueError(
            "Release candidate configuration date does not match "
            "the refresh summary."
        )
    return candidate, manifest, digest


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


def release_only(paths):
    return bool(paths) and all(
        path == "docs/data" or path.startswith("docs/data/")
        for path in paths
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Promote the latest reviewed estimator release candidate "
            "into tracked docs/data, rerun release gates, and stage "
            "only docs/data."
        )
    )
    parser.add_argument(
        "--summary",
        help=(
            "Optional estimator-refresh-summary.json to promote. "
            "Defaults to the newest candidate-ready summary."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not tracked_worktree_clean():
        raise RuntimeError(
            "Tracked working-tree changes are present. "
            "Clean them before promotion."
        )

    check_destination()

    if args.summary:
        summary_path = Path(args.summary)
        if not summary_path.is_absolute():
            summary_path = ROOT / summary_path
        summary = load_json(summary_path)
        if summary.get("status") != "candidate-ready":
            raise ValueError(
                "Selected refresh summary is not candidate-ready."
            )
    else:
        summary_path, summary = latest_ready_summary()

    current_head = git("rev-parse", "HEAD")
    if current_head != summary.get("sourceGitHead"):
        raise RuntimeError(
            "Repository HEAD changed after this candidate was built. "
            "Run a new refresh so the candidate is validated against "
            "the current application shell."
        )

    candidate, manifest, digest = verify_candidate(summary)

    print("PROMOTION TARGET")
    print(f"Catalog: {manifest.get('catalogVersion')}")
    print(
        "Configuration: "
        f"{manifest.get('configurationAsOfDate')}"
    )
    print(f"Candidate SHA-256: {digest}")

    try:
        publish_catalog(
            candidate / "data",
            ROOT / "docs" / "data",
            replace=True,
        )

        python = sys.executable
        run(
            "1/3 Repository tests",
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
        run(
            "2/3 Browser runtime coverage",
            [
                "node",
                "scripts/audit_static_runtime_coverage.mjs",
                "--catalog-dir",
                "docs/data",
            ],
            capture=True,
        )

        audit = audit_public_site(ROOT / "docs")
        print("\n== 3/3 Public static-site audit ==")
        print(f"Options: {audit['options']}")
        print(f"Files: {audit['files']}")
        print("Findings: 0")

        changed = [
            line
            for line in git("diff", "--name-only").splitlines()
            if line
        ]
        if not release_only(changed):
            raise RuntimeError(
                "Promotion changed tracked files outside docs/data. "
                "Nothing was staged."
            )

        subprocess.run(
            ["git", "add", "docs/data"],
            cwd=ROOT,
            check=True,
        )
        staged = [
            line
            for line in git(
                "diff",
                "--cached",
                "--name-only",
            ).splitlines()
            if line
        ]
        if not release_only(staged):
            raise RuntimeError(
                "Staged changes are not confined to docs/data."
            )

    except Exception:
        subprocess.run(
            [
                "git",
                "restore",
                "--staged",
                "--worktree",
                "--",
                "docs/data",
            ],
            cwd=ROOT,
        )
        raise

    branch = git("branch", "--show-current")
    version = str(
        manifest.get("catalogVersion") or "estimator catalog"
    )

    print("\nPROMOTION VALIDATED AND STAGED")
    print(f"Staged files: {len(staged)}")
    print(f"Summary: {rel(summary_path)}")
    print("Next:")
    print(
        f'  git commit -m "Publish validated {version}"'
    )
    print(f"  git push origin {branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
