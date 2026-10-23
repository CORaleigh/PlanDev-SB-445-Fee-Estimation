import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPTS_DIR / "stage_static_release_candidate.py"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location(
    "stage_static_release_candidate",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_docs_shell(root):
    docs = root / "docs"
    docs.mkdir()
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    (docs / "index.html").write_text(
        '<script type="module" src="./app.js"></script>',
        encoding="utf-8",
    )
    (docs / "app.js").write_text(
        'fetch("./data/manifest.json");',
        encoding="utf-8",
    )
    (docs / "runtime.js").write_text(
        "export function evaluateOption() {}",
        encoding="utf-8",
    )
    (docs / "site-config.js").write_text(
        "export const STAKEHOLDER_PREVIEW = { enabled: true };",
        encoding="utf-8",
    )
    (docs / "styles.css").write_text(
        "body { font-family: sans-serif; }",
        encoding="utf-8",
    )
    images = docs / "images"
    images.mkdir()
    for name in MODULE.REQUIRED_IMAGE_FILES:
        (images / name).write_bytes(b"test-image")
    (docs / "NOTES.md").write_text(
        "Static release notes.",
        encoding="utf-8",
    )
    return docs


def write_catalog(root):
    catalog = root / "catalog"
    options = catalog / "options"
    options.mkdir(parents=True)

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
    (catalog / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (options / "a.json").write_text(
        '{"schemaVersion":"public-1.0","option":{"optionKey":"A"}}',
        encoding="utf-8",
    )
    return catalog


class StageStaticReleaseCandidateTests(unittest.TestCase):
    def test_existing_candidate_and_temporary_files_are_preserved(self):
        for name in ("candidate", "candidate.tmp"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                docs = write_docs_shell(root)
                catalog = write_catalog(root)
                existing = root / name
                existing.mkdir()
                sentinel = existing / "keep.txt"
                sentinel.write_text("local work", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    MODULE.stage_release_candidate(catalog, docs, root / "candidate")
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "local work")

    def test_stages_catalog_without_reusing_existing_docs_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = write_docs_shell(root)
            stale = docs / "data"
            stale.mkdir()
            (stale / "stale.json").write_text(
                '{"stale":true}',
                encoding="utf-8",
            )
            catalog = write_catalog(root)
            output = root / "candidate"

            result = MODULE.stage_release_candidate(
                catalog,
                docs,
                output,
            )

            self.assertEqual(result["catalogVersion"], "test-r1")
            self.assertEqual(result["configurationAsOfDate"], "2026-09-24")
            self.assertEqual(result["options"], 1)
            self.assertEqual(len(result["sha256"]), 64)
            self.assertTrue(
                (output / "data" / "manifest.json").is_file()
            )
            self.assertTrue(
                (output / "data" / "options" / "a.json").is_file()
            )
            self.assertFalse(
                (output / "data" / "stale.json").exists()
            )
            self.assertFalse((output / "NOTES.md").exists())

    def test_candidate_digest_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = write_docs_shell(root)
            catalog = write_catalog(root)

            first = MODULE.stage_release_candidate(
                catalog,
                docs,
                root / "candidate-a",
            )
            second = MODULE.stage_release_candidate(
                catalog,
                docs,
                root / "candidate-b",
            )

            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(first["files"], second["files"])
            self.assertEqual(first["bytes"], second["bytes"])

    def test_excludes_files_outside_the_site_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = write_docs_shell(root)
            (docs / "extract.csv").write_text(
                "permit,fee\nA,100\n",
                encoding="utf-8",
            )
            catalog = write_catalog(root)

            output = root / "candidate"
            MODULE.stage_release_candidate(catalog, docs, output)

            self.assertFalse((output / "extract.csv").exists())
            self.assertFalse((output / "NOTES.md").exists())


if __name__ == "__main__":
    unittest.main()
