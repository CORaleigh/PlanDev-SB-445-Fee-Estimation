import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class StakeholderPreviewTests(unittest.TestCase):
    def test_preview_banner_is_configured_and_rendered(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        config = (DOCS / "site-config.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="stakeholder-preview"', index)
        self.assertIn('role="note"', index)
        self.assertIn("STAKEHOLDER_PREVIEW", config)
        self.assertIn("enabled: true", config)
        self.assertIn("Preview: For Review Only", config)
        self.assertNotIn("Internal preview", config)
        self.assertIn(
            'import { OPTION_LABEL_MODE, STAKEHOLDER_PREVIEW }',
            app,
        )
        self.assertIn("function renderStakeholderPreview()", app)
        self.assertIn("showSourceDates: false", config)
        self.assertIn("function renderSourceNote()", app)
        self.assertIn("STAKEHOLDER_PREVIEW?.enabled && STAKEHOLDER_PREVIEW?.showSourceDates", app)
        self.assertNotIn("Catalog: ${version}", app)
        self.assertIn("renderStakeholderPreview();", app)
        self.assertIn(".stakeholder-preview", styles)

    def test_interface_copy_avoids_em_dashes_and_internal_terms(self):
        interface = "\n".join(
            (DOCS / name).read_text(encoding="utf-8")
            for name in ("index.html", "app.js", "site-config.js")
        )

        self.assertNotIn(chr(0x2014), interface)
        self.assertNotIn("process clock", interface.lower())
        self.assertNotIn("currently validated catalog", interface.lower())
        self.assertNotIn("mature, non-terminal permits", interface.lower())
        self.assertNotIn("historical parent permit families", interface.lower())

    def test_page_and_document_titles_use_title_case(self):
        for name, title in (("README.md", "Raleigh Development Fee Estimate"),
                            ("UPDATE.md", "Updating the Fee Data")):
            self.assertTrue((ROOT / name).read_text(encoding="utf-8").startswith("# " + title + "\n"))
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        for element in ("<title>Permit Fee Estimate</title>", "<h1>Permit Fee Estimate</h1>",
                        "<h2>Project Details</h2>", "<h3>Related Permits</h3>"):
            self.assertIn(element, index)

    def test_owned_source_and_documentation_do_not_contain_em_dashes(self):
        files = list(ROOT.glob("*.md"))
        for directory in ("scripts", "queries", "schema", "tests"):
            files.extend(path for path in (ROOT / directory).glob("*")
                         if path.suffix in {".py", ".sql", ".mjs", ".json"})
        files.extend(path for path in DOCS.glob("*")
                     if path.suffix in {".html", ".js", ".css"})
        for path in files:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
