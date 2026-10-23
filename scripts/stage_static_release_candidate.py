from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from publish_static_site_catalog import _option_sources, publish_catalog
from project_public_catalog import PUBLIC_SCHEMA_VERSION


DEFAULT_DOCS = "docs"
DEFAULT_OUTPUT = "output-prod/static-release-candidate"

REQUIRED_ROOT_FILES = {
    ".nojekyll",
    "index.html",
    "app.js",
    "runtime.js",
    "site-config.js",
    "styles.css",
}

REQUIRED_IMAGE_FILES = {
    "android-chrome-192x192.png",
    "android-chrome-512x512.png",
    "apple-touch-icon.png",
    "favicon-16x16.png",
    "favicon-32x32.png",
    "favicon.ico",
    "raleigh_horizontal.png",
    "site.webmanifest",
}

FORBIDDEN_SUFFIXES = {
    ".csv",
    ".db",
    ".env",
    ".key",
    ".log",
    ".parquet",
    ".pbix",
    ".pem",
    ".pkl",
    ".p12",
    ".pfx",
    ".sql",
    ".sqlite",
    ".xlsx",
    ".xls",
    ".zip",
}

FORBIDDEN_NAMES = {
    ".env",
    "env.pkl",
    "swagger_probe_output.txt",
}

SECRET_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)

RUNTIME_FORBIDDEN_MARKERS = (
    "/api/evaluate",
    "http://127.0.0.1",
    "https://127.0.0.1",
    "http://localhost",
    "https://localhost",
)


def _iter_files(root):
    return sorted(
        (path for path in Path(root).rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _copy_docs_shell(docs_dir, staging):
    docs_dir = Path(docs_dir)
    if not docs_dir.exists():
        raise FileNotFoundError(f"Static docs directory was not found: {docs_dir}")

    staging.mkdir(parents=True)
    for name in sorted(REQUIRED_ROOT_FILES):
        source = docs_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"Static site file was not found: {source}")
        shutil.copy2(source, staging / name)

    images = staging / "images"
    images.mkdir()
    for name in sorted(REQUIRED_IMAGE_FILES):
        source = docs_dir / "images" / name
        if not source.is_file():
            raise FileNotFoundError(f"Static site image was not found: {source}")
        shutil.copy2(source, images / name)


def _validate_candidate(candidate):
    candidate = Path(candidate)
    missing = sorted(
        name
        for name in REQUIRED_ROOT_FILES
        if not (candidate / name).is_file()
    )
    if missing:
        raise ValueError(
            "Static candidate is missing required root files: "
            + ", ".join(missing)
        )

    missing_images = sorted(
        name
        for name in REQUIRED_IMAGE_FILES
        if not (candidate / "images" / name).is_file()
    )
    if missing_images:
        raise ValueError(
            "Static candidate is missing required images: "
            + ", ".join(missing_images)
        )

    manifest_path = candidate / "data" / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Static candidate is missing data/manifest.json.")

    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    if manifest.get("schemaVersion") != PUBLIC_SCHEMA_VERSION:
        raise ValueError(
            "Static candidate data is not the reduced public package."
        )

    options = list(manifest.get("options") or [])
    if not options:
        raise ValueError("Static candidate manifest contains no options.")

    expected_option_paths = set()
    for relative, _ in _option_sources(candidate / "data", manifest):
        expected_option_paths.add(
            (Path("data") / relative).as_posix()
        )

    files = _iter_files(candidate)
    for path in files:
        relative = path.relative_to(candidate).as_posix()
        name = path.name.casefold()
        suffix = path.suffix.casefold()

        if name in FORBIDDEN_NAMES or suffix in FORBIDDEN_SUFFIXES:
            raise ValueError(
                f"Forbidden release-candidate file: {relative}"
            )

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if any(marker in text for marker in SECRET_MARKERS):
            raise ValueError(
                f"Potential private-key material found in: {relative}"
            )

    for runtime_name in ("index.html", "app.js", "runtime.js"):
        text = (candidate / runtime_name).read_text(encoding="utf-8")
        for marker in RUNTIME_FORBIDDEN_MARKERS:
            if marker in text:
                raise ValueError(
                    f"Runtime backend dependency marker {marker!r} "
                    f"found in {runtime_name}."
                )

    actual_option_paths = {
        path.relative_to(candidate).as_posix()
        for path in (candidate / "data" / "options").glob("*.json")
    }
    extras = sorted(actual_option_paths - expected_option_paths)
    missing_options = sorted(expected_option_paths - actual_option_paths)
    if extras or missing_options:
        raise ValueError(
            "Static candidate option set does not match the manifest. "
            f"extras={extras}, missing={missing_options}"
        )

    return manifest, files


def _candidate_digest(candidate, files):
    root = Path(candidate)
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        total_bytes += len(payload)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest(), total_bytes


def stage_release_candidate(catalog_dir, docs_dir, output_dir):
    catalog_dir = Path(catalog_dir)
    docs_dir = Path(docs_dir)
    output_dir = Path(output_dir)
    staging = output_dir.with_name(output_dir.name + ".tmp")

    for target in (output_dir, staging):
        if target.exists():
            raise FileExistsError(f"Choose a new candidate directory; path already exists: {target}")

    try:
        _copy_docs_shell(docs_dir, staging)
        publish_catalog(catalog_dir, staging / "data")
        manifest, files = _validate_candidate(staging)
        digest, total_bytes = _candidate_digest(staging, files)

        staging.replace(output_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "catalogVersion": manifest.get("catalogVersion"),
        "configurationAsOfDate": manifest.get("configurationAsOfDate"),
        "options": len(manifest.get("options") or []),
        "files": len(files),
        "bytes": total_bytes,
        "sha256": digest,
        "output": str(output_dir),
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Stage and audit a reproducible static estimator release candidate "
            "without modifying tracked docs/data."
        )
    )
    parser.add_argument("catalog_dir")
    parser.add_argument("--docs-dir", default=DEFAULT_DOCS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result = stage_release_candidate(
        args.catalog_dir,
        args.docs_dir,
        args.output,
    )

    print("STATIC RELEASE CANDIDATE READY")
    print(f"Catalog version: {result['catalogVersion']}")
    print(
        "Configuration snapshot: "
        f"{result['configurationAsOfDate']}"
    )
    print(f"Options: {result['options']}")
    print(f"Files: {result['files']}")
    print(f"Bytes: {result['bytes']}")
    print(f"Candidate SHA-256: {result['sha256']}")
    print(f"Output: {result['output']}")


if __name__ == "__main__":
    main()
