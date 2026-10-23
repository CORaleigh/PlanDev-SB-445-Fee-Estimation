from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".txt"}

HIGH_RISK_PATTERNS = (
    (
        "private key material",
        re.compile(
            r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
            re.IGNORECASE,
        ),
    ),
    (
        "database connection string",
        re.compile(
            r"\b(?:server|data source)\s*=\s*[^;\r\n]+;",
            re.IGNORECASE,
        ),
    ),
    (
        "credential assignment",
        re.compile(
            r"\b(?:password|pwd|client[_-]?secret|api[_-]?key)"
            r"\s*[:=]\s*['\"]?[^\s,'\";}]{4,}",
            re.IGNORECASE,
        ),
    ),
    (
        "bearer token",
        re.compile(r"\bauthorization\s*:\s*bearer\s+\S+", re.IGNORECASE),
    ),
    (
        "Windows absolute path",
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\+[^\r\n]+"),
    ),
    (
        "UNC path",
        re.compile(r"(?<!\\)\\\\[A-Za-z0-9_.-]+\\[^\r\n]+"),
    ),
    (
        "file URL",
        re.compile(r"\bfile://", re.IGNORECASE),
    ),
)

LOCAL_RUNTIME_PATTERN = re.compile(
    r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?",
    re.IGNORECASE,
)
EXTERNAL_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
NON_NETWORK_NAMESPACE_URLS = {
    "http://www.w3.org/2000/svg",
}
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
GUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
PUBLIC_SCHEMA_VERSION = "public-1.0"
FORBIDDEN_PUBLIC_JSON_KEYS = {
    "advancedRules",
    "assessmentEvidence",
    "className",
    "configuredOptionKey",
    "configuredOptionPath",
    "denominator",
    "feeId",
    "feeSetupId",
    "numerator",
    "permitTypeId",
    "sourceColumn",
    "sourceFile",
    "sourceTemplate",
    "sourceTemplateId",
    "sources",
    "validation",
    "workClassId",
}

RUNTIME_SUFFIXES = {".css", ".html", ".js"}


def iter_files(root: Path):
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _json_key_findings(value, relative, path="$"):
    findings = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_PUBLIC_JSON_KEYS:
                findings.append(
                    f"{relative}: forbidden public field {child_path}"
                )
            findings.extend(
                _json_key_findings(child, relative, child_path)
            )
    elif isinstance(value, list):
        for child in value:
            findings.extend(_json_key_findings(child, relative, path + "[]"))
    return findings


def audit_public_site(site_dir):
    root = Path(site_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Static site directory was not found: {root}")

    manifest_path = root / "data" / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Public site is missing data/manifest.json.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != PUBLIC_SCHEMA_VERSION:
        raise ValueError(
            "Public manifest is not a reduced public-data package."
        )
    options = list(manifest.get("options") or [])
    if not options:
        raise ValueError("Public manifest contains no estimator options.")

    expected_options = {
        (Path("data") / str(item.get("path") or "")).as_posix()
        for item in options
    }
    actual_options = {
        path.relative_to(root).as_posix()
        for path in (root / "data" / "options").glob("*.json")
    }
    if expected_options != actual_options:
        missing = sorted(expected_options - actual_options)
        extras = sorted(actual_options - expected_options)
        raise ValueError(
            "Public option files do not match the manifest. "
            f"missing={missing}, extras={extras}"
        )

    files = iter_files(root)
    findings = []
    external_runtime_urls = set()
    email_values = set()
    json_files = 0
    total_bytes = 0

    for path in files:
        total_bytes += path.stat().st_size
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()
        if suffix not in TEXT_SUFFIXES:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            findings.append(
                f"{relative}: UTF-8 decode failed: {error}"
            )
            continue

        if suffix == ".json":
            json_files += 1
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as error:
                findings.append(
                    f"{relative}: invalid JSON: {error}"
                )
            else:
                findings.extend(
                    _json_key_findings(payload, relative)
                )

        if GUID_PATTERN.search(text):
            findings.append(f"{relative}: internal-style identifier")

        for label, pattern in HIGH_RISK_PATTERNS:
            if pattern.search(text):
                findings.append(f"{relative}: {label}")

        if LOCAL_RUNTIME_PATTERN.search(text):
            findings.append(f"{relative}: localhost runtime reference")

        if suffix in RUNTIME_SUFFIXES:
            for url in EXTERNAL_URL_PATTERN.findall(text):
                if url in NON_NETWORK_NAMESPACE_URLS:
                    continue
                external_runtime_urls.add(url)

        for email in EMAIL_PATTERN.findall(text):
            email_values.add(email)

    if external_runtime_urls:
        findings.append(
            "runtime files contain external URL(s): "
            + ", ".join(sorted(external_runtime_urls))
        )

    if email_values:
        findings.append(
            "public tree contains email-like value(s) requiring manual review: "
            + ", ".join(sorted(email_values))
        )

    result = {
        "catalogVersion": manifest.get("catalogVersion"),
        "configurationAsOfDate": manifest.get("configurationAsOfDate"),
        "options": len(options),
        "files": len(files),
        "jsonFiles": json_files,
        "bytes": total_bytes,
        "externalRuntimeUrls": len(external_runtime_urls),
        "emailLikeValues": len(email_values),
        "findings": findings,
    }

    if findings:
        raise ValueError(
            "Public static site audit failed:\n- "
            + "\n- ".join(findings)
        )

    return result


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Audit the deployable static estimator tree for public-exposure "
            "and self-contained-runtime risks."
        )
    )
    parser.add_argument(
        "site_dir",
        nargs="?",
        default="docs",
        help="Static site directory to audit (default: docs)",
    )
    args = parser.parse_args()

    result = audit_public_site(args.site_dir)
    print("PUBLIC STATIC SITE AUDIT PASS")
    print(f"Site: {args.site_dir}")
    print(f"Catalog version: {result['catalogVersion']}")
    print(
        "Configuration snapshot: "
        f"{result['configurationAsOfDate']}"
    )
    print(f"Options: {result['options']}")
    print(f"Files: {result['files']}")
    print(f"JSON files: {result['jsonFiles']}")
    print(f"Bytes: {result['bytes']}")
    print(
        "External runtime URLs: "
        f"{result['externalRuntimeUrls']}"
    )
    print(
        "Email-like values requiring review: "
        f"{result['emailLikeValues']}"
    )
    print("Sensitive/local-path findings: 0")


if __name__ == "__main__":
    main()
