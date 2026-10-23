import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "audit_public_static_site.py"
)
SPEC = importlib.util.spec_from_file_location(
    "audit_public_static_site",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_site(root):
    site = root / "docs"
    options = site / "data" / "options"
    options.mkdir(parents=True)

    (site / "index.html").write_text(
        '<script type="module" src="./app.js"></script>',
        encoding="utf-8",
    )
    (site / "app.js").write_text(
        'fetch("./data/manifest.json");',
        encoding="utf-8",
    )
    (site / "runtime.js").write_text(
        "export function evaluateOption() {}",
        encoding="utf-8",
    )
    (site / "styles.css").write_text(
        "body { font-family: sans-serif; }",
        encoding="utf-8",
    )

    manifest = {
        "schemaVersion": "public-1.0",
        "catalogVersion": "test-r1",
        "configurationAsOfDate": "2026-09-24",
        "options": [
            {
                "optionKey": "A",
                "permitType": "Permit",
                "workClass": "Work",
                "path": "options/a.json",
            }
        ],
    }
    (site / "data" / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (options / "a.json").write_text(
        '{"schemaVersion":"public-1.0","option":{"optionKey":"A"}}',
        encoding="utf-8",
    )
    return site


class PublicStaticSiteAuditTests(unittest.TestCase):
    def test_clean_static_site_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            result = MODULE.audit_public_site(site)

            self.assertEqual(result["catalogVersion"], "test-r1")
            self.assertEqual(result["options"], 1)
            self.assertEqual(result["jsonFiles"], 2)
            self.assertEqual(result["externalRuntimeUrls"], 0)
            self.assertEqual(result["emailLikeValues"], 0)
            self.assertEqual(result["findings"], [])

    def test_rejects_sensitive_or_local_path_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            (site / "app.js").write_text(
                'const password = "secret123";\n'
                'const source = "C:\\\\temp\\\\extract.csv";\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "Public static site audit failed",
            ):
                MODULE.audit_public_site(site)

    def test_allows_embedded_svg_namespace_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            (site / "styles.css").write_text(
                "select { background-image: url(\"data:image/svg+xml,"
                "%3Csvg xmlns='http://www.w3.org/2000/svg'%3E"
                "%3C/svg%3E\"); }",
                encoding="utf-8",
            )

            result = MODULE.audit_public_site(site)

            self.assertEqual(result["externalRuntimeUrls"], 0)
            self.assertEqual(result["findings"], [])

    def test_rejects_external_runtime_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            (site / "index.html").write_text(
                '<script src="https://example.com/runtime.js"></script>',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "external URL",
            ):
                MODULE.audit_public_site(site)

    def test_rejects_internal_identifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            option = site / "data" / "options" / "a.json"
            option.write_text(
                '{"value":"11111111-2222-3333-4444-555555555555"}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "internal-style identifier",
            ):
                MODULE.audit_public_site(site)

    def test_rejects_internal_catalog_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            option = site / "data" / "options" / "a.json"
            option.write_text(
                '{"assessmentEvidence":{"status":"available"}}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "forbidden public field",
            ):
                MODULE.audit_public_site(site)

    def test_rejects_non_public_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = write_site(Path(tmp))
            manifest = site / "data" / "manifest.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload.pop("schemaVersion")
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "reduced public-data package",
            ):
                MODULE.audit_public_site(site)


if __name__ == "__main__":
    unittest.main()
