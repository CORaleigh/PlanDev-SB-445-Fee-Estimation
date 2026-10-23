import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "publish_static_site_catalog.py"
)
SPEC = importlib.util.spec_from_file_location(
    "publish_static_site_catalog",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublishStaticSiteCatalogTests(unittest.TestCase):
    def test_rejects_option_paths_outside_the_flat_options_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "private.json").write_text("{}", encoding="utf-8")
            for name in ("options/../private.json", "../private.json", "options/sub/a.json"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    MODULE._option_sources(root, {"options": [{"path": name}]})
            (root / "options").mkdir()
            (root / "options/a.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                MODULE._option_sources(root, {"options": [{"path": "options/a.json"}] * 2})

    def test_existing_destination_requires_explicit_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            (catalog / "options").mkdir(parents=True)
            (catalog / "options/a.json").write_text("{}", encoding="utf-8")
            manifest = {"options": [{"path": "options/a.json"}]}
            (catalog / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            destination = root / "data"
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("local work", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                MODULE._copy_public_catalog(catalog, destination, manifest)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "local work")
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                MODULE.publish_catalog(catalog, root)

    def test_publishes_manifest_and_option_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            options = catalog / "options"
            options.mkdir(parents=True)

            manifest = {
                "schemaVersion": "public-1.0",
                "catalogVersion": "test-r1",
                "configurationAsOfDate": "2026-09-15",
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

            destination = root / "site-data"
            result = MODULE.publish_catalog(
                catalog,
                destination,
            )

            self.assertEqual(result["options"], 1)
            self.assertEqual(result["catalogVersion"], "test-r1")
            self.assertTrue(
                (destination / "manifest.json").exists()
            )
            self.assertTrue(
                (destination / "options" / "a.json").exists()
            )
            self.assertFalse(
                (destination / "disclosure-codes.json").exists()
            )

    def test_missing_option_fails_before_replacing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog"
            catalog.mkdir()
            (catalog / "manifest.json").write_text(
                json.dumps(
                    {
                        "options": [
                            {
                                "path": "options/missing.json",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            destination = root / "site-data"
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                MODULE.publish_catalog(
                    catalog,
                    destination,
                )

            self.assertEqual(
                sentinel.read_text(encoding="utf-8"),
                "keep",
            )


if __name__ == "__main__":
    unittest.main()
