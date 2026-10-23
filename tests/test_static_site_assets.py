import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def run_node_module(source):
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".mjs",
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write(source)
        temp_path = Path(handle.name)
    try:
        return subprocess.run(
            ["node", str(temp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def row_like_fixture():
    def fixed(fee_id, name, amount):
        return {
            "feeTemplateFeeId": fee_id,
            "name": name,
            "section": "configured",
            "applicability": {
                "status": "configured",
                "conditions": [],
            },
            "calculation": {
                "mode": "fixed",
                "fixedAmount": amount,
                "rule": {},
            },
        }

    def custom_input(key, label):
        return {
            "inputKey": key,
            "label": label,
            "source": "custom-field",
            "variable": True,
        }

    def fee_input(key, label):
        return {
            "inputKey": key,
            "label": label,
            "source": "fee",
            "variable": True,
        }

    def expression_condition(*conditions):
        return {
            "kind": "expression",
            "sourceRule": {
                "isOrGroup": False,
                "childConditions": [
                    {
                        "operandValue": field,
                        "operandFriendlyName": label,
                        "operatorID": 1,
                        "compareExpression": {"value": value},
                    }
                    for field, label, value in conditions
                ],
            },
        }

    site = fixed("SITE", "ROW - Site Final", 124)
    driveway = {
        "feeTemplateFeeId": "NEW",
        "name": "ROW - New Driveway Fee",
        "section": "input-required",
        "applicability": {
            "status": "configured",
            "conditions": [],
        },
        "calculation": {
            "mode": "input",
            "rule": {
                "amount": 141,
                "minimum": 0,
                "maximum": 0,
                "roundingType": 1,
                "roundingValue": 0,
                "inputs": [
                    custom_input(
                        "NumberofNewDriveways",
                        "# of New Driveways",
                    )
                ],
            },
        },
    }
    sidewalk = {
        "feeTemplateFeeId": "SIDEWALK",
        "name": "ROW - Sidewalk Installation",
        "section": "conditional",
        "applicability": {
            "status": "conditional",
            "conditions": [
                expression_condition(
                    (
                        "SidewalkInstallation",
                        "Sidewalk Installation",
                        True,
                    ),
                    (
                        "SiteFinalOnly",
                        "Site Final Only",
                        False,
                    ),
                )
            ],
        },
        "calculation": {
            "mode": "input",
            "rule": {
                "amount": 0.4,
                "minimum": 141,
                "maximum": 0,
                "roundingType": 1,
                "roundingValue": 0,
                "inputs": [
                    custom_input(
                        "SidewalkLinearFeet",
                        "Sidewalk Linear Feet",
                    )
                ],
            },
        },
    }
    tech = {
        "feeTemplateFeeId": "TECH",
        "name": "MISC - Technology Surcharge",
        "section": "configured",
        "applicability": {
            "status": "configured",
            "conditions": [],
        },
        "calculation": {
            "mode": "input",
            "rule": {
                "amount": 4,
                "minimum": 0,
                "maximum": 0,
                "roundingType": 2,
                "roundingValue": 1,
                "inputs": [
                    fee_input("SITE", "Site"),
                    fee_input("NEW", "Driveway"),
                    fee_input("SIDEWALK", "Sidewalk"),
                ],
                "publicBehavior": {
                    "aggregate": "sum",
                    "rateScale": 0.01,
                },
            },
        },
    }
    return {
        "catalogVersion": "test-r1",
        "configuration": {"asOfDate": "2026-09-15"},
        "option": {
            "optionKey": "ROW-LIKE",
            "permitType": "Generic Complex Permit",
            "workClass": "Generic Complex Work Class",
        },
        "fees": [site, driveway, sidewalk, tech],
        "requiredInputs": [],
        "disclosures": [],
    }


class StaticSiteAssetTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for browser logic tests")
    def test_option_loading_ignores_stale_responses_and_errors(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        source = app[app.index("async function loadOption()"):app.index("function optionHash(")]
        script = """
import assert from 'node:assert/strict';
const state = {loadSequence: 0};
const elements = Object.fromEntries(['results','calculate','projectInputs','runtimeNotice'].map(k => [k, {}]));
const DATA_ROOT = '';
let selected;
const pending = new Map();
function fetch(url) { return new Promise((resolve, reject) => pending.set(url, {resolve, reject})); }
function selectedManifestOption() { return {path: selected}; }
function hideError() {}
function renderSourceNote() {}
function renderRuntimeNotice() {}
function renderControls() {}
function buildOptionUiSchema(document) { return document; }
""" + source + """
for (const kind of ['response', 'body', 'error']) {
  selected = 'old';
  const old = loadOption();
  assert.equal(elements.calculate.disabled, true);
  assert.equal(state.optionDocument, null);
  let resolveBody;
  if (kind === 'body') {
    pending.get('old').resolve({ok: true, json: () => new Promise(r => resolveBody = r)});
    await Promise.resolve();
  }
  selected = 'new';
  const current = loadOption();
  pending.get('new').resolve({ok: true, json: async () => ({key: 'new'})});
  await current;
  if (kind === 'body') resolveBody({key: 'old'});
  else if (kind === 'error') pending.get('old').reject(new Error('stale request failed'));
  else pending.get('old').resolve({ok: true, json: async () => ({key: 'old'})});
  await old;
  assert.equal(state.optionDocument.key, 'new');
  assert.equal(elements.calculate.disabled, false);
}
selected = 'failed';
const failed = loadOption();
pending.get('failed').resolve({ok: false});
await assert.rejects(failed, /Could not load/);
assert.equal(state.optionDocument, null);
assert.equal(elements.calculate.disabled, true);
"""
        result = run_node_module(script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_docs_data_is_not_gitignored(self):
        result = subprocess.run(
            ["git", "check-ignore", "docs/data/manifest.json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)

    def test_static_shell_has_no_backend_endpoint(self):
        index = (DOCS / "index.html").read_text(
            encoding="utf-8"
        )
        app = (DOCS / "app.js").read_text(encoding="utf-8")

        self.assertIn('src="./app.js"', index)
        self.assertIn('href="./styles.css"', index)
        self.assertIn('DATA_ROOT + "manifest.json"', app)
        self.assertIn("DATA_ROOT + selected.path", app)
        self.assertGreaterEqual(app.count('cache: "no-store"'), 2)
        self.assertNotIn("/api/evaluate", app)
        self.assertNotIn("127.0.0.1:8765", app)

    def test_browser_runtime_has_no_permit_specific_branches(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("Tree Impact Permit", runtime)
        self.assertNotIn("Stormwater Control 2", runtime)
        self.assertNotIn("Utility Connection Tap", runtime)
        self.assertNotIn("Right-of-way [Residential]", runtime)

    def test_static_shell_hides_empty_project_inputs_and_draws_select_carets(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="project-inputs"', index)
        self.assertIn(
            "elements.projectInputs.hidden = controls.length === 0;",
            app,
        )
        self.assertIn("select {", styles)
        self.assertIn("appearance: none;", styles)
        self.assertIn("-moz-appearance: none;", styles)
        self.assertIn("background-image: url(", styles)
        self.assertIn("padding-right: 2.25rem;", styles)
        self.assertIn(".controls .field > .field-label {", styles)
        self.assertIn("align-items: stretch;", styles)
        self.assertIn("flex-direction: column;", styles)
        self.assertIn("height: 100%;", styles)
        self.assertIn("overflow-wrap: anywhere;", styles)
        self.assertIn(".controls .field > input,", styles)
        self.assertIn("margin-top: auto;", styles)
        self.assertNotIn("grid-template-rows: subgrid;", styles)
        self.assertIn("height: 2.75rem;", styles)
        self.assertIn("box-sizing: border-box;", styles)

    def test_static_ui_surfaces_possible_additional_fees(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        self.assertIn("renderPossibleAdditionalFees", app)
        self.assertIn("report.possibleAdditionalFees", app)
        self.assertIn("Other Fees That May Apply", app)

    def test_static_ui_surfaces_estimate_traceability_and_limitations(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")

        for element_id in (
            "source-note",
            "estimate-limitations",
            "estimate-inputs",
        ):
            self.assertIn(f'id="{element_id}"', index)

        self.assertIn("PROMINENT_DISCLOSURE_CODES", app)
        self.assertIn('"ESTIMATE_NOT_INVOICE"', app)
        self.assertNotIn('"CONFIGURATION_AS_OF_DATE"', app)
        self.assertIn('"ENERGOV_SOURCE_OF_TRUTH"', app)
        self.assertIn("Important Information", app)
        self.assertIn("Project Information Used", app)
        self.assertIn("renderSourceNote()", app)
        self.assertIn("No project details were entered.", app)
        self.assertIn("renderEstimateInputs(values)", app)
        self.assertIn("renderEstimateLimitations()", app)

    def test_catalog_fetches_bypass_browser_cache(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")

        self.assertGreaterEqual(app.count('cache: "no-store"'), 2)
        self.assertIn('DATA_ROOT + "manifest.json"', app)
        self.assertIn("DATA_ROOT + selected.path", app)

    def test_permit_options_have_hash_deep_links(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")

        self.assertIn("function optionHash(permitType, workClass)", app)
        self.assertIn("function preferredFromHash()", app)
        self.assertIn("encodeURIComponent", app)
        self.assertIn("decodeURIComponent", app)
        self.assertIn('raw.split("#")', app)
        self.assertIn('url.searchParams.delete("permitType")', app)
        self.assertIn('url.searchParams.delete("workClass")', app)
        self.assertIn("window.history.replaceState", app)
        self.assertGreaterEqual(app.count("updateOptionUrl();"), 3)

    def test_grouped_forms_have_expand_and_collapse_all_actions(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="group-actions"', index)
        self.assertIn('id="expand-all-groups"', index)
        self.assertIn('id="collapse-all-groups"', index)
        self.assertIn(">Expand all</button>", index)
        self.assertIn(">Collapse all</button>", index)
        self.assertIn("function setAllControlGroupsOpen(open)", app)
        self.assertIn('"details.control-group"', app)
        self.assertIn("details.open = Boolean(open);", app)
        self.assertIn(
            'elements.expandAllGroups?.addEventListener("click"',
            app,
        )
        self.assertIn(
            'elements.collapseAllGroups?.addEventListener("click"',
            app,
        )
        self.assertIn(
            "elements.groupActions.hidden = presentationGroups.length === 0;",
            app,
        )
        self.assertIn(".project-input-actions", styles)
        self.assertIn(".group-actions", styles)
        self.assertIn(".link-button", styles)

        helper = app[
            app.index("function setAllControlGroupsOpen(open) {"):
            app.index("function updateFrequencyVisibility() {")
        ]
        self.assertNotIn("renderControls()", helper)
        self.assertNotIn("evaluateOption(", helper)
        self.assertNotIn(".value =", helper)

    def test_project_numeric_inputs_reject_negative_values(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")

        self.assertIn('input.min = "0";', app)
        self.assertIn("input.checkValidity()", app)
        self.assertIn("input.reportValidity()", app)

    def test_staff_frequency_uses_direct_input_prevalence_only(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")

        self.assertIn('id="show-frequency"', index)
        self.assertIn("Show percentages from past permits", index)
        self.assertIn(
            "These percentages do not show what is required for this project.",
            index,
        )
        self.assertIn("historicalPrevalenceBadge", app)
        self.assertIn("formatPrevalencePercent", app)
        self.assertIn("Math.round(percent)", app)
        self.assertIn("Past permits with this detail", app)
        self.assertIn("|| percent < 0", app)
        self.assertNotIn("|| percent <= 0", app)
        self.assertIn('status === "limited-history"', app)
        self.assertIn("evidence.percent", app)
        self.assertIn("control.historicalPrevalence", app)
        self.assertNotIn("control.historicalOccurrence", app)
        self.assertIn("document?.ui", runtime)
        self.assertNotIn("directInputPrevalence(document, field)", app)
        self.assertIn(".frequency-badge", styles)
        self.assertIn(".source-badge", styles)


    def test_public_form_hides_internal_inheritance_and_conditional_jargon(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('textContent = "Inherited"', app)
        self.assertNotIn("inherited or conditional fee details", app)
        self.assertNotIn("“Inherited” means", index)
        self.assertNotIn(
            "Fee percentages are conditional on families",
            index,
        )
        self.assertIn(
            "Open this section if additional project details apply.",
            app,
        )
        self.assertIn(
            "Related permit fees are not included in the estimate.",
            index,
        )

    def test_related_permit_history_is_opt_in_and_non_calculating(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="show-related-permits"', index)
        self.assertIn("Show related permits", index)
        self.assertIn('id="related-permits-panel"', index)
        self.assertIn("function renderRelatedPermits()", app)
        self.assertIn("relation?.history", app)
        self.assertIn("fee?.history", app)
        self.assertIn("relation?.historyBasis", app)
        self.assertNotIn("numerator", app)
        self.assertNotIn("denominator", app)
        self.assertIn("updateRelatedPermitVisibility", app)
        self.assertIn(".related-permit", styles)
        self.assertIn(".occurrence-badge", styles)

        evaluator = runtime[
            runtime.index("export function evaluateOption("):
            runtime.index("\n}", runtime.index("export function evaluateOption(")) + 2
        ]
        self.assertIn("const fees = Array.from(document.fees ?? []);", runtime)
        self.assertNotIn("document.relatedPermits", evaluator)


    def test_dense_project_inputs_use_generic_progressive_disclosure(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")

        self.assertIn('document.createElement("details")', app)
        self.assertIn("control.presentationGroup?.key", app)
        self.assertIn("presentation?.groups", app)
        self.assertIn(".control-group", styles)
        self.assertIn(".control-group-fields", styles)
        self.assertIn('.control-group > summary::before', styles)
        self.assertIn('content: "+";', styles)
        self.assertIn('.control-group[open] > summary::before', styles)
        self.assertIn('content: "−";', styles)
        self.assertIn("DENSE_OPTION_CONTROL_THRESHOLD", runtime)
        self.assertIn("applyControlPresentation", runtime)
        self.assertIn("Additional Fee Details", runtime)
        self.assertNotIn("Right-of-Way Occupancy", runtime)
        self.assertNotIn("Accessory Structure", runtime)

    def test_browser_runtime_uses_curated_display_label_aliases(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")

        self.assertIn("DISPLAY_LABEL_ALIASES", runtime)
        self.assertIn('["workingwithouta", "Working without a"]', runtime)
        self.assertIn(
            '["bothmechandele", "Both Mechanical and Electrical"]',
            runtime,
        )
        self.assertIn("export function curatedDisplayLabel", runtime)
        self.assertIn(
            "return cleanStaffFacingLabel(",
            runtime,
        )
        self.assertIn(
            "curatedDisplayLabel(friendly) || friendly",
            runtime,
        )

    def test_browser_runtime_aliases_choice_labels_without_changing_values(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")

        self.assertIn("function displayChoice(choice)", runtime)
        self.assertIn(
            'label: cleanStaffFacingLabel(label, { surface: "choice" })',
            runtime,
        )
        self.assertIn("dedupeBrowserChoices(", runtime)
        self.assertIn("(field.choices ?? []).map(displayChoice)", runtime)
        self.assertIn("choices: displayChoices", runtime)

        app = (DOCS / "app.js").read_text(encoding="utf-8")
        self.assertIn("cleanStaffFacingLabel,", app)
        self.assertIn("displayOptionLabel,", app)
        self.assertIn(
            '? displayOptionLabel(label, OPTION_LABEL_MODE)',
            app,
        )
        self.assertIn(
            'cleanStaffFacingLabel(name, { surface: "fee" })',
            app,
        )

    def test_public_shell_uses_branding_and_plain_language_data_freshness(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")

        self.assertIn("./images/raleigh_horizontal.png", index)
        self.assertIn("./images/favicon.ico", index)
        self.assertIn("./images/site.webmanifest", index)
        self.assertIn("function dataFreshnessText(document)", app)
        self.assertIn("Fee configuration as of ", app)
        self.assertIn("Past projects reviewed: ", app)
        self.assertIn('id="source-note" class="source-note" hidden><em>', index)
        self.assertNotIn('id="option-meta"', index)
        self.assertNotIn('id="estimate-context"', index)
        self.assertNotIn("Catalog: ${state.optionDocument.catalogVersion", app)
        self.assertNotIn("Configuration snapshot:", app)
        self.assertIn(".site-logo", styles)

    def test_option_label_mode_defaults_external_but_keeps_internal_values(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        config = (DOCS / "site-config.js").read_text(encoding="utf-8")

        self.assertIn(
            'export const OPTION_LABEL_MODE = "external";',
            config,
        )
        self.assertIn(
            'import { OPTION_LABEL_MODE, STAKEHOLDER_PREVIEW } '
            'from "./site-config.js";',
            app,
        )
        self.assertIn("export function displayOptionLabel", runtime)
        self.assertIn('if (mode === "internal") return raw;', runtime)
        self.assertIn(
            'option.value = value;',
            app,
        )
        self.assertIn(
            'optionElement(value, value, { optionName: true })',
            app,
        )

    def test_staff_facing_app_hides_internal_rule_jargon_by_default(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")

        self.assertIn("Some fees need review.", app)
        self.assertNotIn("Some Fees Still Need Rule Validation.", app)
        self.assertIn("displayFeeName", app)
        self.assertIn("REINSPECTION FEES BELOW THIS LINE", app)
        self.assertIn("Boolean(displayFeeName(item.name))", app)
        self.assertNotIn("DEBUG", app)

    def test_zoom_reflow_uses_controlled_minimum_width(self):
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")

        self.assertIn("min-width: 320px;", styles)
        self.assertIn("overflow-x: auto;", styles)
        self.assertGreaterEqual(
            styles.count(
                "grid-template-columns: repeat(auto-fit, "
                "minmax(min(240px, 100%), 1fr));"
            ),
            2,
        )

    def test_static_ui_has_accessibility_semantics_and_focus_support(self):
        index = (DOCS / "index.html").read_text(encoding="utf-8")
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        styles = (DOCS / "styles.css").read_text(encoding="utf-8")

        self.assertIn('role="alert"', index)
        self.assertIn('id="results-heading"', index)
        self.assertIn('aria-labelledby="results-heading"', index)
        self.assertIn('tabindex="-1"', index)
        self.assertGreaterEqual(index.count('aria-live="polite"'), 3)
        self.assertIn("elements.results.focus();", app)
        self.assertIn("addTableCaption(table, \"Calculated Fees\")", app)
        self.assertIn("addTableCaption(table, \"Project Inputs Used\")", app)
        self.assertIn(
            'addTableCaption(table, "Items That Still Need Review")',
            app,
        )
        self.assertIn("input:focus-visible", styles)
        self.assertIn("select:focus-visible", styles)
        self.assertIn("button:focus-visible", styles)
        self.assertIn(".control-group > summary:focus-visible", styles)
        self.assertIn("@media (forced-colors: active)", styles)
        self.assertIn("background: hsl(var(--frequency-hue), 70%, 28%);", styles)
        self.assertIn(".sr-only", styles)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser runtime syntax test skipped.",
    )
    def test_browser_javascript_parses_as_modules(self):
        for filename in ("runtime.js", "app.js", "site-config.js"):
            source = (DOCS / filename).read_text(
                encoding="utf-8"
            )
            with tempfile.NamedTemporaryFile(
                "w",
                suffix=".mjs",
                encoding="utf-8",
                delete=False,
            ) as handle:
                handle.write(source)
                temp_path = Path(handle.name)
            try:
                completed = subprocess.run(
                    ["node", "--check", str(temp_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr,
                )
            finally:
                temp_path.unlink(missing_ok=True)


    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; dense presentation parity test skipped.",
    )
    def test_browser_runtime_groups_dense_scenario_review_control(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "DENSE-PRESENTATION",
                "permitType": "Generic Dense Permit",
                "workClass": "Generic Dense Work",
            },
            "fees": [],
            "requiredInputs": [],
            "disclosures": [],
        }
        labels = [
            "Primary(FULL STREETS - MAJOR CLOSURES-PM - [X] Dense)",
            "Secondary(FULL STREETS - MAJOR CLOSURES-PM - [X] Dense)",
            "Duration(FULL STREETS - MAJOR CLOSURES-PM - [X] Dense)",
            "Review (PM - [X] Dense)",
        ]
        for index in range(12):
            key = f"Field{index}"
            fixture["fees"].append(
                {
                    "feeTemplateFeeId": f"FEE-{index}",
                    "name": (
                        "ROW - Full Streets - Major - Review"
                        if index == 3
                        else f"Fee {index}"
                    ),
                    "section": "input-required",
                    "templateRelationship": (
                        "Direct" if index < 4 or index == 11 else "Child"
                    ),
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 1,
                            "inputs": [
                                {
                                    "inputKey": key,
                                    "label": (
                                        labels[index]
                                        if index < len(labels)
                                        else f"Field {index}"
                                    ),
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                }
            )

        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "console.log(JSON.stringify(ui.presentation));\n"
        )
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            presentation = json.loads(completed.stdout.strip())
            self.assertEqual(presentation["mode"], "progressive")
            self.assertEqual(presentation["initialControlCount"], 1)
            groups = {
                group["label"]: group
                for group in presentation["groups"]
            }
            self.assertEqual(
                len(groups["Full Streets - Major Closures"]["controlKeys"]),
                4,
            )
            self.assertEqual(
                len(groups["Additional Fee Details"]["controlKeys"]),
                7,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; dependency-only role test skipped.",
    )
    def test_browser_runtime_excludes_dependency_only_role_from_subtotal(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "DEPENDENCY-ROLE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "HIDDEN",
                    "name": "Intermediate (Hidden)",
                    "assessmentRole": "dependency-only",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 25,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "VISIBLE",
                    "name": "Visible Fee",
                    "assessmentRole": "standalone-or-assessed",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 2,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": "HIDDEN",
                                    "label": "Hidden dependency",
                                    "source": "fee",
                                }
                            ],
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const result = evaluateOption(fixture, {});\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "console.log(JSON.stringify({"
            "total: result.report.estimatedTotal,"
            "regular: result.report.regularPermitFees.map("
            "(item) => item.feeTemplateFeeId),"
            "dependencyOnly: result.report.dependencyOnlyFees.map("
            "(item) => item.feeTemplateFeeId),"
            "calculated: result.report.calculatedFees.map("
            "(item) => item.feeTemplateFeeId),"
            "dependencyOnlyAutomaticFees: "
            "capability.dependencyOnlyAutomaticFees,"
            "assessableAutomaticFees: capability.assessableAutomaticFees,"
            "supportedAssessable: "
            "capability.structurallySupportedAssessableAutomaticFees"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["total"], 50)
            self.assertEqual(actual["regular"], ["VISIBLE"])
            self.assertEqual(actual["dependencyOnly"], ["HIDDEN"])
            self.assertEqual(
                actual["calculated"],
                ["HIDDEN", "VISIBLE"],
            )
            self.assertEqual(actual["dependencyOnlyAutomaticFees"], 1)
            self.assertEqual(actual["assessableAutomaticFees"], 1)
            self.assertEqual(actual["supportedAssessable"], 1)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser runtime input-key test skipped.",
    )
    def test_browser_runtime_accepts_trimmed_zero_value_input(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "TRIM",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "QTY",
                    "name": "Quantity Fee",
                    "section": "input-required",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 10,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": "Quantity ",
                                    "label": "Quantity (Quantity, Number)",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const schema = buildOptionUiSchema(fixture);\n"
            "const result = evaluateOption(fixture, {Quantity: 0});\n"
            "console.log(JSON.stringify({"
            "total: result.report.estimatedTotal,"
            "status: result.report.estimateStatus,"
            "label: schema.controls[0].displayLabel"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["total"], 0)
            self.assertEqual(
                actual["status"],
                "automatic-estimate-complete",
            )
            self.assertEqual(actual["label"], "Quantity")
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser runtime coverage test skipped.",
    )
    def test_browser_runtime_capability_analyzer_marks_supported_fixture(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = json.dumps(row_like_fixture())
        script = runtime + (
            "\nconst fixture = " + fixture + ";\n"
            "console.log(JSON.stringify(analyzeOptionCapabilities(fixture)));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["blockedAutomaticFees"], 0)
            self.assertEqual(
                actual["structurallySupportedAutomaticFees"],
                actual["automaticFees"],
            )
            self.assertEqual(
                actual["assessableAutomaticFees"],
                actual["automaticFees"],
            )
            self.assertEqual(
                actual["structurallySupportedAssessableAutomaticFees"],
                actual["assessableAutomaticFees"],
            )
            self.assertEqual(
                actual["dependencyOnlyAutomaticFees"],
                0,
            )
            self.assertEqual(actual["blockerArchetypes"], {})
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser event-fee coverage test skipped.",
    )
    def test_browser_capability_audit_excludes_event_contingent_fee(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = row_like_fixture()
        fixture["fees"].append(
            {
                "feeTemplateFeeId": "INSPECTION-EVENT",
                "name": "Inspection Event Fee",
                "section": "event-contingent",
                "applicability": {
                    "status": "event-contingent",
                    "conditions": [
                        {
                            "kind": "inspection",
                            "description": (
                                "Fee is contingent on an inspection event."
                            ),
                            "supported": False,
                        }
                    ],
                },
                "calculation": {
                    "mode": "fixed",
                    "fixedAmount": 50,
                    "rule": {},
                },
                "disposition": "later-event",
            }
        )
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "console.log(JSON.stringify({ui, capability}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertTrue(actual["ui"]["runtimeReady"])
            self.assertEqual(
                actual["ui"]["blockingRuntimeDiagnostics"],
                [],
            )
            self.assertEqual(
                len(actual["ui"]["reviewRuntimeDiagnostics"]),
                1,
            )
            capability = actual["capability"]
            self.assertEqual(capability["automaticFees"], 4)
            self.assertEqual(capability["reviewOnlyFees"], 1)
            self.assertEqual(capability["blockedAutomaticFees"], 0)
            self.assertEqual(capability["blockerArchetypes"], {})
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser operator-2 test skipped.",
    )
    def test_browser_runtime_supports_operator_2_not_equal_condition(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "NOT-EQUAL",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "NOT-EQUAL-FEE",
                    "name": "Not Equal Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "PermitWorkClassID",
                                            "operandFriendlyName": "Permit Work Class ID",
                                            "operatorID": 2,
                                            "compareExpression": {
                                                "value": "EXCLUDED"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 75,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const included = evaluateOption(fixture, {PermitWorkClassID: 'OTHER'});\n"
            "const excluded = evaluateOption(fixture, {PermitWorkClassID: 'EXCLUDED'});\n"
            "console.log(JSON.stringify({"
            "includedTotal: included.report.estimatedTotal,"
            "excludedTotal: excluded.report.estimatedTotal"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["includedTotal"], 75)
            self.assertEqual(actual["excludedTotal"], 0)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser operator-3 test skipped.",
    )
    def test_browser_runtime_supports_operator_3_greater_than_condition(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "GREATER-THAN",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "GT-FEE",
                    "name": "Greater Than Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "Quantity",
                                            "operandFriendlyName": "Quantity",
                                            "operatorID": 3,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 75,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const schema = buildOptionUiSchema(fixture);\n"
            "const included = evaluateOption(fixture, {Quantity: 2});\n"
            "const excluded = evaluateOption(fixture, {Quantity: 0});\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "console.log(JSON.stringify({"
            "controlType: schema.controls[0].controlType,"
            "includedTotal: included.report.estimatedTotal,"
            "excludedTotal: excluded.report.estimatedTotal,"
            "blocked: capability.blockedAutomaticFees"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["controlType"], "number")
            self.assertEqual(actual["includedTotal"], 75)
            self.assertEqual(actual["excludedTotal"], 0)
            self.assertEqual(actual["blocked"], 0)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser nullable-number test skipped.",
    )
    def test_browser_runtime_supports_nullable_number_with_isblank(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "NULLABLE-NUMBER",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "POSITIVE-FEE",
                    "name": "Positive Acres Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DisturbedAcres",
                                            "operandFriendlyName": "Disturbed Acres",
                                            "operatorID": 3,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 10,
                            "inputs": [
                                {
                                    "inputKey": "DisturbedAcres",
                                    "label": "Disturbed Acres",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                },
                {
                    "feeTemplateFeeId": "BLANK-FEE",
                    "name": "Blank Acres Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DisturbedAcres",
                                            "operandFriendlyName": "Disturbed Acres",
                                            "operatorID": 7,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 25,
                        "rule": {},
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "const blank = evaluateOption(fixture, {DisturbedAcres: ''});\n"
            "const numeric = evaluateOption(fixture, {DisturbedAcres: '2'});\n"
            "console.log(JSON.stringify({"
            "displayReady: ui.displayReady,"
            "valueType: ui.controls[0].valueType,"
            "controlType: ui.controls[0].controlType,"
            "blankValue: blank.values.DisturbedAcres,"
            "blankTotal: blank.report.estimatedTotal,"
            "numericTotal: numeric.report.estimatedTotal"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertTrue(actual["displayReady"])
            self.assertEqual(actual["valueType"], "nullable-number")
            self.assertEqual(actual["controlType"], "number")
            self.assertIsNone(actual["blankValue"])
            self.assertEqual(actual["blankTotal"], 25)
            self.assertEqual(actual["numericTotal"], 20)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser operator-7 test skipped.",
    )
    def test_browser_runtime_supports_operator_7_blank_condition(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "BLANK",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "BLANK-FEE",
                    "name": "Blank Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DisturbedAcres",
                                            "operandFriendlyName": "Disturbed Acres",
                                            "operatorID": 7,
                                            "compareExpression": {"value": 0},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const schema = buildOptionUiSchema(fixture);\n"
            "const blank = evaluateOption(fixture, {DisturbedAcres: false});\n"
            "const populated = evaluateOption(fixture, {DisturbedAcres: true});\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "console.log(JSON.stringify({"
            "controlType: schema.controls[0].controlType,"
            "blankTotal: blank.report.estimatedTotal,"
            "populatedTotal: populated.report.estimatedTotal,"
            "blocked: capability.blockedAutomaticFees"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["controlType"], "boolean")
            self.assertEqual(actual["blankTotal"], 50)
            self.assertEqual(actual["populatedTotal"], 0)
            self.assertEqual(actual["blocked"], 0)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; duplicate-choice test skipped.",
    )
    def test_browser_runtime_fails_closed_on_duplicate_choice_labels(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "DUPLICATE-CHOICE-LABELS",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [],
            "requiredInputs": [],
            "disclosures": [],
        }

        aliases = [
            ("ZONE-1-A", "1", 10),
            ("ZONE-1-B", "1", 20),
            ("ZONE-1-C", "1", 30),
            ("ZONE-2-A", "2", 40),
            ("ZONE-2-B", "2", 50),
        ]
        for index, (value, label, amount) in enumerate(aliases, start=1):
            fixture["fees"].append(
                {
                    "feeTemplateFeeId": f"FEE-{index}",
                    "name": f"Choice Alias Fee {index}",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [{
                            "kind": "expression",
                            "sourceRule": {
                                "isOrGroup": False,
                                "childConditions": [{
                                    "operandValue": "ThoroughfareZone",
                                    "operandFriendlyName": "Thoroughfare Zone",
                                    "operatorID": 1,
                                    "compareExpression": {
                                        "value": value,
                                        "label": label,
                                    },
                                }],
                            },
                        }],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": amount,
                        "rule": {},
                    },
                }
            )

        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "const control = ui.controls.find((item) => item.key === 'ThoroughfareZone');\n"
            "console.log(JSON.stringify({"
            "choices: control.choices,"
            "duplicateChoiceLabels: control.duplicateChoiceLabels,"
            "controlDisplayReady: control.displayReady,"
            "displayReady: ui.displayReady,"
            "blockerCode: ui.uiBlockers[0].code"
            "}));\n"
        )

        completed = run_node_module(script)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        actual = json.loads(completed.stdout.strip())
        self.assertEqual(
            [item["label"] for item in actual["choices"]],
            ["1", "2"],
        )
        self.assertEqual(
            actual["duplicateChoiceLabels"],
            ["1", "2"],
        )
        self.assertFalse(actual["controlDisplayReady"])
        self.assertFalse(actual["displayReady"])
        self.assertEqual(
            actual["blockerCode"],
            "DUPLICATE_CHOICE_LABELS_NOT_DISPLAY_READY",
        )

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; public duplicate-choice test skipped.",
    )
    def test_browser_runtime_rechecks_public_choice_labels(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "schemaVersion": "public-1.0",
            "option": {
                "optionKey": "o001",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "ui": {
                "controls": [
                    {
                        "key": "i001",
                        "label": "Zone",
                        "displayLabel": "Zone",
                        "valueType": "choice",
                        "controlType": "select",
                        "displayReady": True,
                        "choices": [
                            {"value": "c001", "label": "Zone 1"},
                            {"value": "c002", "label": "Zone 1"},
                        ],
                    }
                ],
                "presentation": {"mode": "flat", "groups": []},
                "uiBlockers": [],
                "blockingRuntimeDiagnostics": [],
                "runtimeReady": True,
                "displayReady": True,
            },
            "fees": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "console.log(JSON.stringify(ui));\n"
        )

        completed = run_node_module(script)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        actual = json.loads(completed.stdout.strip())
        self.assertFalse(actual["displayReady"])
        self.assertFalse(actual["controls"][0]["displayReady"])
        self.assertEqual(
            actual["controls"][0]["duplicateChoiceLabels"],
            ["Zone 1"],
        )
        self.assertEqual(
            actual["uiBlockers"][0]["code"],
            "DUPLICATE_CHOICE_LABELS_NOT_DISPLAY_READY",
        )

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser choice-label test skipped.",
    )
    def test_browser_runtime_uses_preserved_choice_label_for_guid(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        guid = "021FD65F-EA7C-48BF-84E9-383E5ADD28BE"
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "LABELED-CHOICE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "LABELED-FEE",
                    "name": "Labeled Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "PermitWorkClassID",
                                            "operandFriendlyName": "Permit Work Class",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": guid,
                                                "label": "New Construction",
                                                "labelSource": "PMPERMITWORKCLASS.NAME",
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 75,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "const result = evaluateOption(fixture, {PermitWorkClassID: '" + guid + "'});\n"
            "console.log(JSON.stringify({"
            "displayReady: ui.displayReady,"
            "controlType: ui.controls[0].controlType,"
            "choice: ui.controls[0].choices[0],"
            "total: result.report.estimatedTotal"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertTrue(actual["displayReady"])
            self.assertEqual(actual["controlType"], "select")
            self.assertEqual(actual["choice"]["value"], guid)
            self.assertEqual(actual["choice"]["label"], "New Construction")
            self.assertEqual(actual["total"], 75)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser field-evidence test skipped.",
    )
    def test_browser_ui_schema_exposes_field_occurrence_and_inheritance(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "EVIDENCE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "FEE-A",
                    "name": "Occasional Fee",
                    "templateRelationship": "Child",
                    "section": "input-required",
                    "occurrence": {
                        "status": "available",
                        "rate": 0.37,
                        "numerator": 37,
                        "denominator": 100,
                    },
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 10,
                            "inputs": [
                                {
                                    "inputKey": "Quantity",
                                    "label": "Quantity",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "console.log(JSON.stringify(ui.controls[0]));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["historicalOccurrence"]["rate"], 0.37)
            self.assertEqual(
                actual["historicalOccurrence"]["numerator"],
                37,
            )
            self.assertEqual(
                actual["historicalOccurrence"]["denominator"],
                100,
            )
            self.assertEqual(
                actual["historicalOccurrence"]["basis"],
                "highest-associated-fee-occurrence",
            )
            self.assertEqual(
                actual["provenance"]["scope"],
                "selected-permit",
            )
            self.assertTrue(actual["provenance"]["inheritedTemplate"])
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser selected-work-class test skipped.",
    )
    def test_browser_runtime_system_resolves_selected_work_class(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "SYSTEM-WORK-CLASS",
                "permitTypeKey": "t001",
                "permitType": "Generic Permit",
                "workClassKey": "w001",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "WORK-CLASS-FEE",
                    "name": "Work Class Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "systemField": "selected-work-class",
                                            "operandValue": "selected-work-class",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": "w001"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 75,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "const included = evaluateOption(fixture, {});\n"
            "const excludedFixture = JSON.parse(JSON.stringify(fixture));\n"
            "excludedFixture.option.workClassKey = 'w002';\n"
            "const excluded = evaluateOption(excludedFixture, {});\n"
            "console.log(JSON.stringify({"
            "controls: ui.controls,"
            "system: included.systemApplicabilityValues,"
            "includedTotal: included.report.estimatedTotal,"
            "excludedTotal: excluded.report.estimatedTotal"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["controls"], [])
            self.assertEqual(
                actual["system"]["selected-work-class"],
                "w001",
            )
            self.assertEqual(actual["includedTotal"], 75)
            self.assertEqual(actual["excludedTotal"], 0)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser review-only input test skipped.",
    )
    def test_browser_runtime_omits_review_and_event_only_fields(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "NON-INPUT-REVIEW-FIELDS",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "EVENT-FEE",
                    "name": "Reinspection",
                    "section": "event-contingent",
                    "applicability": {
                        "status": "event-contingent",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "InspectionStatusID",
                                            "operandFriendlyName": "Inspection Status",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": "STATUS-A"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "MANUAL-FEE",
                    "name": "Manual Workflow Fee",
                    "section": "manual-review",
                    "applicability": {
                        "status": "manual-review",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "manual",
                        "rule": {
                            "inputs": [
                                {
                                    "inputKey": "WorkflowValue",
                                    "label": "Workflow Value",
                                    "source": "custom-field",
                                }
                            ]
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "console.log(JSON.stringify({"
            "controls: ui.controls,"
            "runtimeReady: ui.runtimeReady"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["controls"], [])
            self.assertTrue(actual["runtimeReady"])
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser ApplicationDate test skipped.",
    )
    def test_browser_runtime_system_resolves_application_date(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "SYSTEM-APPLICATION-DATE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "DATE-FEE",
                    "name": "Date Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "systemField": "configuration-date",
                                            "operandValue": "configuration-date",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": (
                                                    "2021-04-05T00:00:00"
                                                )
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "const current = evaluateOption(fixture, {});\n"
            "const historicalFixture = JSON.parse(JSON.stringify(fixture));\n"
            "historicalFixture.configuration.asOfDate = '2021-04-05';\n"
            "const historical = evaluateOption(historicalFixture, {});\n"
            "console.log(JSON.stringify({"
            "controls: ui.controls,"
            "currentSystem: current.systemApplicabilityValues,"
            "currentTotal: current.report.estimatedTotal,"
            "historicalSystem: historical.systemApplicabilityValues,"
            "historicalTotal: historical.report.estimatedTotal"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["controls"], [])
            self.assertEqual(
                actual["currentSystem"],
                {"configuration-date": "2026-09-15T00:00:00"},
            )
            self.assertEqual(actual["currentTotal"], 0)
            self.assertEqual(
                actual["historicalSystem"],
                {"configuration-date": "2021-04-05T00:00:00"},
            )
            self.assertEqual(actual["historicalTotal"], 50)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser operator-8 test skipped.",
    )
    def test_browser_runtime_supports_operator_8_presence_condition(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "PRESENCE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "PRESENT",
                    "name": "Presence Conditional Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "DomesticStubSize",
                                            "operandFriendlyName": "Domestic Stub Size",
                                            "operatorID": 8,
                                            "compareExpression": {
                                                "value": "configured-guid"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                }
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "const included = evaluateOption(fixture, {DomesticStubSize: true});\n"
            "const excluded = evaluateOption(fixture, {DomesticStubSize: false});\n"
            "console.log(JSON.stringify({"
            "ui,"
            "includedTotal: included.report.estimatedTotal,"
            "excludedTotal: excluded.report.estimatedTotal"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertTrue(actual["ui"]["runtimeReady"])
            self.assertTrue(actual["ui"]["displayReady"])
            self.assertEqual(
                actual["ui"]["controls"][0]["controlType"],
                "boolean",
            )
            self.assertEqual(
                actual["ui"]["controls"][0]["valueType"],
                "presence",
            )
            self.assertEqual(actual["includedTotal"], 50)
            self.assertEqual(actual["excludedTotal"], 0)
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser runtime parity test skipped.",
    )
    def test_browser_runtime_reproduces_423_complex_archetype(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = json.dumps(row_like_fixture())
        script = runtime + (
            "\nconst fixture = " + fixture + ";\n"
            "const result = evaluateOption(fixture, {"
            "NumberofNewDriveways: 1,"
            "SidewalkLinearFeet: 100,"
            "SidewalkInstallation: true,"
            "SiteFinalOnly: false"
            "});\n"
            "console.log(JSON.stringify({"
            "total: result.report.estimatedTotal,"
            "status: result.report.estimateStatus"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["total"], 423)
            self.assertEqual(
                actual["status"],
                "automatic-estimate-complete",
            )
        finally:
            temp_path.unlink(missing_ok=True)


    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; validated zero-input utility test skipped.",
    )
    def test_browser_runtime_supports_only_validated_zero_input_utility_fee(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fee_id = "0d9e4154-3d96-4fb3-9954-177657a7b293"

        def zero_input(fee_key, name):
            return {
                "feeTemplateFeeId": fee_key,
                "name": name,
                "section": "configured",
                "applicability": {
                    "status": "configured",
                    "conditions": [],
                },
                "calculation": {
                    "mode": "input",
                    "rule": {
                        "amount": 2223,
                        "minimum": 0,
                        "maximum": 0,
                        "roundingType": 1,
                        "roundingValue": 0,
                        "inputs": [],
                        "publicBehavior": (
                            {"fixedInput": True}
                            if fee_key == fee_id
                            else {}
                        ),
                    },
                },
            }

        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-22"},
            "option": {
                "optionKey": "UTILITY-FIXED",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                zero_input(
                    fee_id,
                    'PU - 5/8" Sewer Capital Facility Fee',
                ),
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        unknown = {
            **fixture,
            "fees": [
                zero_input(
                    "unknown-zero-input",
                    "Unknown zero-input fee",
                ),
            ],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const unknown = " + json.dumps(unknown) + ";\n"
            "const result = evaluateOption(fixture, {});\n"
            "const knownCapability = analyzeOptionCapabilities(fixture);\n"
            "const unknownCapability = analyzeOptionCapabilities(unknown);\n"
            "const fee = result.report.regularPermitFees.find("
            "(item) => item.feeTemplateFeeId === '" + fee_id + "');\n"
            "console.log(JSON.stringify({"
            "amount: fee.amount,"
            "knownBlocked: knownCapability.blockedAutomaticFees,"
            "unknownBlocked: unknownCapability.blockedAutomaticFees,"
            "unknownArchetypes: unknownCapability.blockerArchetypes"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["amount"], 2223)
            self.assertEqual(actual["knownBlocked"], 0)
            self.assertEqual(actual["unknownBlocked"], 1)
            self.assertEqual(
                actual["unknownArchetypes"],
                {"missing-input": 1},
            )
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; validated total surcharge test skipped.",
    )
    def test_browser_runtime_supports_validated_total_based_surcharge(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        plumbing_id = "f09bbc1f-cd45-49f2-81a9-38e4434e08cf"
        electrical_id = "7210288d-2877-477f-9872-3af531dfe12b"
        surcharge_id = "dbeb60d1-9a93-4dee-b002-4fa9f96a6fa6"

        def fixed(fee_id, name, amount):
            return {
                "feeTemplateFeeId": fee_id,
                "name": name,
                "section": "configured",
                "applicability": {
                    "status": "configured",
                    "conditions": [],
                },
                "calculation": {
                    "mode": "fixed",
                    "fixedAmount": amount,
                    "rule": {},
                },
            }

        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "TOTAL-SURCHARGE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                fixed(plumbing_id, "Plumbing", 124),
                fixed(electrical_id, "Electrical", 124),
                {
                    "feeTemplateFeeId": surcharge_id,
                    "name": "MISC - Technology Surcharge",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 4,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 2,
                            "roundingValue": 1,
                            "inputs": [
                                {
                                    "inputKey": "Total",
                                    "label": "Total of Fees in Template",
                                    "source": "total",
                                }
                            ],
                            "publicBehavior": {
                                "totalComponentIds": [
                                    plumbing_id,
                                    electrical_id,
                                ],
                                "rateScale": 0.01,
                            },
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const result = evaluateOption(fixture, {});\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "const surcharge = result.report.regularPermitFees.find("
            "(item) => item.feeTemplateFeeId === '" + surcharge_id + "');\n"
            "console.log(JSON.stringify({"
            "amount: surcharge.amount,"
            "blocked: capability.blockedAutomaticFees,"
            "archetypes: capability.blockerArchetypes"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["amount"], 10)
            self.assertEqual(actual["blocked"], 0)
            self.assertEqual(actual["archetypes"], {})
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; unknown total surcharge test skipped.",
    )
    def test_browser_runtime_keeps_unknown_total_input_blocked(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "UNKNOWN-TOTAL",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "BASE",
                    "name": "Base Fee",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 100,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "UNKNOWN-TOTAL",
                    "name": "MISC - Technology Surcharge",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 4,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 2,
                            "roundingValue": 1,
                            "inputs": [
                                {
                                    "inputKey": "Total",
                                    "label": "Total of Fees in Template",
                                    "source": "total",
                                }
                            ],
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "console.log(JSON.stringify(analyzeOptionCapabilities(fixture)));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["blockedAutomaticFees"], 1)
            self.assertEqual(
                actual["blockerArchetypes"],
                {"total-input": 1},
            )
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; browser formula parity test skipped.",
    )
    def test_browser_runtime_supports_validated_row_formula(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "ROW-FORMULA",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "RATE",
                    "name": "ROW - Street Lane - (P) Major Rate (Hidden)",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 64.8,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "DAYS",
                    "name": "ROW - Occupancy Global Days (Hidden)",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 32,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "FORMULA",
                    "name": "ROW - (P) STREET LANE - Major LF",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "formula",
                        "rule": {
                            "inputs": [
                                {
                                    "inputKey": "RATE",
                                    "label": "ROW - Street Lane - (P) Major Rate (Hidden)",
                                    "source": "fee",
                                },
                                {
                                    "inputKey": "DAYS",
                                    "label": "ROW - Occupancy Global Days (Hidden)",
                                    "source": "fee",
                                },
                            ],
                            "expression": {"expressionType": 3},
                            "publicBehavior": {
                                "formula": {
                                    "kind": "multiply",
                                    "dependencyIds": ["RATE", "DAYS"],
                                    "rateId": "RATE",
                                    "daysId": "DAYS",
                                    "expressionType": 3,
                                }
                            },
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const result = evaluateOption(fixture, {});\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "const formula = result.report.regularPermitFees.find("
            "(item) => item.feeTemplateFeeId === 'FORMULA');\n"
            "console.log(JSON.stringify({"
            "amount: formula.amount,"
            "blocked: capability.blockedAutomaticFees,"
            "archetypes: capability.blockerArchetypes"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertAlmostEqual(actual["amount"], 2073.6)
            self.assertEqual(actual["blocked"], 0)
            self.assertEqual(actual["archetypes"], {})
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; Tree Conservation browser formula test skipped.",
    )
    def test_browser_runtime_supports_validated_tree_conservation_formula(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        parent_id = "17133e4e-ef4e-4ed9-af59-6c4810bfcba1"
        nsa_id = "c64859af-2ace-4e44-9945-35a850bc4f16"
        tca_id = "67708793-e39f-4507-92ae-037db95f2b1b"
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "TREE-FORMULA",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": nsa_id,
                    "name": "PRCR - HIDDEN FEE NSA 10% [All Other Zones]",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 3.719,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": tca_id,
                    "name": "PRCR - HIDDEN FEE TCA",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 3.79,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": parent_id,
                    "name": "UF - Tree Conservation 10% Fee (All Zones)",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "formula",
                        "rule": {
                            "amount": 1296,
                            "minimum": 306,
                            "maximum": 7014.13,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": nsa_id,
                                    "label": "PRCR - HIDDEN FEE NSA 10% [All Other Zones]",
                                    "source": "fee",
                                },
                                {
                                    "inputKey": tca_id,
                                    "label": "PRCR - HIDDEN FEE TCA",
                                    "source": "fee",
                                },
                            ],
                            "expression": {"expressionType": 5},
                            "publicBehavior": {
                                "formula": {
                                    "kind": "minimum-times-rate",
                                    "dependencyIds": [nsa_id, tca_id],
                                    "expressionType": 5,
                                }
                            },
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const result = evaluateOption(fixture, {});\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "const formula = result.report.regularPermitFees.find("
            "(item) => item.feeTemplateFeeId === '" + parent_id + "');\n"
            "console.log(JSON.stringify({"
            "amount: formula.amount,"
            "blocked: capability.blockedAutomaticFees,"
            "archetypes: capability.blockerArchetypes"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertAlmostEqual(actual["amount"], 4821.12)
            self.assertEqual(actual["blocked"], 0)
            self.assertEqual(actual["archetypes"], {})
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; Pump Station browser formula test skipped.",
    )
    def test_browser_runtime_supports_validated_pump_station_formula(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fee_id = "1484297c-4e7f-0dab-46c4-f72ef0863990"
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "PUMP-STATION-FORMULA",
                "permitType": "PU - Utility",
                "workClass": "Pump Station",
            },
            "fees": [
                {
                    "feeTemplateFeeId": fee_id,
                    "name": "PU - Pump Station Inspection Fee",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "formula",
                        "rule": {
                            "amount": 3000,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [],
                            "expression": {"expressionType": 7},
                            "publicBehavior": {
                                "formula": {
                                    "kind": "fixed-formula",
                                    "dependencyIds": [],
                                    "expressionType": 7,
                                }
                            },
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const result = evaluateOption(fixture, {});\n"
            "const capability = analyzeOptionCapabilities(fixture);\n"
            "const fee = result.report.regularPermitFees.find("
            "(item) => item.feeTemplateFeeId === '" + fee_id + "');\n"
            "console.log(JSON.stringify({"
            "amount: fee.amount,"
            "blocked: capability.blockedAutomaticFees,"
            "archetypes: capability.blockerArchetypes"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["amount"], 3000)
            self.assertEqual(actual["blocked"], 0)
            self.assertEqual(actual["archetypes"], {})
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; unsupported browser formula test skipped.",
    )
    def test_browser_runtime_keeps_unvalidated_formula_blocked(self):
        runtime = (DOCS / "runtime.js").read_text(
            encoding="utf-8"
        )
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "BAD-FORMULA",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "RATE",
                    "name": "Some Hidden Fee",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 10,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "DAYS",
                    "name": "Another Hidden Fee",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 2,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "FORMULA",
                    "name": "Other Formula",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "formula",
                        "rule": {
                            "inputs": [
                                {"inputKey": "RATE", "label": "rate", "source": "fee"},
                                {"inputKey": "DAYS", "label": "days", "source": "fee"},
                            ],
                            "expression": {"expressionType": 3},
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "console.log(JSON.stringify(analyzeOptionCapabilities(fixture)));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr,
            )
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["blockedAutomaticFees"], 1)
            self.assertEqual(actual["blockerArchetypes"], {"formula": 1})
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; formula dependency report test skipped.",
    )
    def test_browser_unvalidated_formula_blocks_downstream_fee_and_preserves_subtotal(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-24"},
            "option": {
                "optionKey": "FORMULA-DEPENDENCY",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "BASE",
                    "name": "Known Base Fees",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 265,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "FORMULA",
                    "name": "ROW - Threshold Formula Fee",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "SiteFinalOnly",
                                            "operandFriendlyName": "Site Final Only",
                                            "operatorID": 1,
                                            "compareExpression": {"value": False},
                                        },
                                        {
                                            "operandValue": "SidewalkLinearFeet",
                                            "operandFriendlyName": "Sidewalk Linear Feet",
                                            "operatorID": 3,
                                            "compareExpression": {"value": 340},
                                        },
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "formula",
                        "rule": {
                            "amount": 0.33,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": "SidewalkLinearFeet",
                                    "label": "Sidewalk Linear Feet",
                                    "source": "custom-field",
                                    "variable": True,
                                }
                            ],
                            "expression": {"expressionType": 2},
                        },
                    },
                },
                {
                    "feeTemplateFeeId": "TECH",
                    "name": "Technology Fee",
                    "section": "input-required",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 4,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 2,
                            "roundingValue": 1,
                            "inputs": [
                                {
                                    "inputKey": "FORMULA",
                                    "label": "Threshold Formula Fee",
                                    "source": "fee",
                                    "variable": True,
                                }
                            ],
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const result = evaluateOption("
            "fixture, {SidewalkLinearFeet: '341', SiteFinalOnly: 'false'});\n"
            "console.log(JSON.stringify({"
            "total: result.report.estimatedTotal,"
            "status: result.report.estimateStatus,"
            "unresolved: result.report.unresolvedApplicabilityFees.length,"
            "blocked: Object.fromEntries(result.report.blockedFees.map("
            "(item) => [item.feeTemplateFeeId, item.validationCode]))"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["total"], 265)
            self.assertEqual(actual["status"], "staff-review-required")
            self.assertEqual(actual["unresolved"], 0)
            self.assertEqual(
                actual["blocked"],
                {
                    "FORMULA": "FORMULA_CALCULATION_UNVALIDATED",
                    "TECH": "FORMULA_CALCULATION_UNVALIDATED",
                },
            )
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; inactive scenario runtime test skipped.",
    )
    def test_browser_runtime_derives_and_excludes_inactive_scenarios(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "SCENARIO-INACTIVE",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "ACTIVE",
                    "name": "Active Scenario Fee",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 2,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": "ActiveQuantity",
                                    "label": "Active Quantity",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                },
                {
                    "feeTemplateFeeId": "INACTIVE",
                    "name": "Inactive Scenario Fee",
                    "section": "configured",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 3,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 1,
                            "roundingValue": 0,
                            "inputs": [
                                {
                                    "inputKey": "InactiveQuantity",
                                    "label": "Inactive Quantity",
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        ui_schema = {
            "controls": [
                {
                    "key": "Major",
                    "displayReady": True,
                    "controlType": "number",
                    "valueType": "number",
                    "requiredForFeeIds": ["MAJOR", "SHARED"],
                    "presentationGroup": {
                        "key": "scenario:major",
                        "kind": "scenario",
                    },
                },
                {
                    "key": "Minor",
                    "displayReady": True,
                    "controlType": "number",
                    "valueType": "number",
                    "requiredForFeeIds": ["MINOR", "SHARED"],
                    "presentationGroup": {
                        "key": "scenario:minor",
                        "kind": "scenario",
                    },
                },
                {
                    "key": "Base",
                    "displayReady": True,
                    "controlType": "number",
                    "valueType": "number",
                    "requiredForFeeIds": ["BASE"],
                    "presentationGroup": None,
                },
            ],
            "presentation": {
                "groups": [
                    {
                        "key": "scenario:major",
                        "kind": "scenario",
                        "controlKeys": ["Major"],
                    },
                    {
                        "key": "scenario:minor",
                        "kind": "scenario",
                        "controlKeys": ["Minor"],
                    },
                ]
            },
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = " + json.dumps(ui_schema) + ";\n"
            "const inactive = deriveInactiveScenarioFeeIds("
            "ui, {Major: '10', Minor: '', Base: ''});\n"
            "const result = evaluateOption("
            "fixture, {ActiveQuantity: '5'}, "
            "{inactiveFeeIds: ['INACTIVE']});\n"
            "console.log(JSON.stringify({"
            "inactive,"
            "total: result.report.estimatedTotal,"
            "blocked: result.report.blockedFees.length,"
            "excluded: result.report.excludedFees.map("
            "(item) => item.feeTemplateFeeId)"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["inactive"], ["MINOR"])
            self.assertEqual(actual["total"], 10)
            self.assertEqual(actual["blocked"], 0)
            self.assertEqual(actual["excluded"], ["INACTIVE"])
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; later-event dependency parity test skipped.",
    )
    def test_browser_runtime_later_event_dependency_does_not_block_initial_estimate(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "LATER-EVENT-DEPENDENCY",
                "permitType": "Generic Permit",
                "workClass": "Generic Work",
            },
            "fees": [
                {
                    "feeTemplateFeeId": "BASE",
                    "name": "Base Permit Fee",
                    "section": "configured",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 124,
                        "rule": {},
                    },
                },
                {
                    "feeTemplateFeeId": "LATER",
                    "name": "DSENG - Voiding Permit Fee - Engineering (ROW)",
                    "section": "conditional",
                    "applicability": {
                        "status": "conditional",
                        "conditions": [
                            {
                                "kind": "expression",
                                "sourceRule": {
                                    "isOrGroup": False,
                                    "childConditions": [
                                        {
                                            "operandValue": "WorkflowAction",
                                            "operandFriendlyName": "Workflow Action",
                                            "operatorID": 1,
                                            "compareExpression": {
                                                "value": "Void Permit"
                                            },
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "calculation": {
                        "mode": "fixed",
                        "fixedAmount": 50,
                        "rule": {},
                    },
                    "disposition": "later-event",
                },
                {
                    "feeTemplateFeeId": "LATER-TECH",
                    "name": "Technology Fee",
                    "section": "input-required",
                    "applicability": {"status": "configured", "conditions": []},
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 4,
                            "minimum": 0,
                            "maximum": 0,
                            "roundingType": 2,
                            "roundingValue": 1,
                            "inputs": [
                                {
                                    "inputKey": "LATER",
                                    "label": "Later Event Fee",
                                    "source": "fee",
                                    "variable": True,
                                }
                            ],
                        },
                    },
                },
            ],
            "requiredInputs": [],
            "disclosures": [],
        }
        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const result = evaluateOption(fixture, {});\n"
            "console.log(JSON.stringify({"
            "total: result.report.estimatedTotal,"
            "status: result.report.estimateStatus,"
            "blocked: result.report.blockedFees.length,"
            "unresolved: result.report.unresolvedApplicabilityFees.length,"
            "excluded: result.report.excludedFees.map("
            "(item) => item.feeTemplateFeeId),"
            "laterTech: result.report.calculatedFees.find("
            "(item) => item.feeTemplateFeeId === 'LATER-TECH')?.amount"
            "}));\n"
        )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            actual = json.loads(completed.stdout.strip())
            self.assertEqual(actual["total"], 124)
            self.assertEqual(actual["status"], "automatic-estimate-complete")
            self.assertEqual(actual["blocked"], 0)
            self.assertEqual(actual["unresolved"], 0)
            self.assertIn("LATER", actual["excluded"])
            self.assertEqual(actual["laterTech"], 0)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_scenario_groups_activate_from_values_not_open_state(self):
        app = (DOCS / "app.js").read_text(encoding="utf-8")
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")

        self.assertIn("deriveInactiveScenarioFeeIds", app)
        self.assertIn("deriveInactiveScenarioFeeIds", runtime)
        self.assertIn(
            "If this situation applies, enter the details below.",
            app,
        )
        self.assertNotIn("details.open", runtime)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; label cleanup parity test skipped.",
    )
    def test_browser_runtime_humanizes_infrastructure_style_labels(self):
        runtime = (DOCS / "runtime.js").read_text(encoding="utf-8")
        fixture = {
            "catalogVersion": "test-r1",
            "configuration": {"asOfDate": "2026-09-15"},
            "option": {
                "optionKey": "INFRA-LABELS",
                "permitType": "Generic Infrastructure",
                "workClass": "Generic Work",
            },
            "fees": [],
            "requiredInputs": [],
        }
        fields = [
            [
                "PrivateSewerLFTotal",
                "PRIVATE IMPROVEMENT QUANTITIES "
                "(PrivateSewerLFTotal, Text)",
            ],
            [
                "PrivateWaterLFTotal",
                "PRIVATE IMPROVEMENT QUANTITIES "
                "(PrivateWaterLFTotal, Text)",
            ],
            [
                "SewerLF",
                "SewerLF(PUBLIC IMPROVEMENT QUANTITIES-PM - "
                "[Infrastructure] Sewer)",
            ],
            [
                "TotalStreetFullLF",
                "TotalStreetFullLF(PUBLIC IMPROVEMENT QUANTITIES-PM - "
                "[Infrastructure] Street)",
            ],
            [
                "TotalSewerLF",
                "TotalSewerLF(PUBLIC IMPROVEMENT QUANTITIES-PM - "
                "[Infrastructure] Street)",
            ],
        ]
        for index, (key, label) in enumerate(fields):
            fixture["fees"].append(
                {
                    "feeTemplateFeeId": f"FEE-{index}",
                    "name": f"Fee {index}",
                    "section": "input-required",
                    "applicability": {
                        "status": "configured",
                        "conditions": [],
                    },
                    "calculation": {
                        "mode": "input",
                        "rule": {
                            "amount": 1,
                            "inputs": [
                                {
                                    "inputKey": key,
                                    "label": label,
                                    "source": "custom-field",
                                }
                            ],
                        },
                    },
                }
            )

        script = runtime + (
            "\nconst fixture = " + json.dumps(fixture) + ";\n"
            "const ui = buildOptionUiSchema(fixture);\n"
            "console.log(JSON.stringify(Object.fromEntries("
            "ui.controls.map((control) => "
            "[control.key, control.displayLabel]))));\n"
        )
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".mjs",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["node", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            labels = json.loads(completed.stdout.strip())
            self.assertEqual(
                labels["PrivateSewerLFTotal"],
                "Private Sewer Linear Feet",
            )
            self.assertEqual(
                labels["PrivateWaterLFTotal"],
                "Private Water Linear Feet",
            )
            self.assertEqual(
                labels["SewerLF"],
                "Sewer Linear Feet",
            )
            self.assertEqual(
                labels["TotalStreetFullLF"],
                "Total Street Full Linear Feet",
            )
            self.assertEqual(
                labels["TotalSewerLF"],
                "Total Sewer Linear Feet",
            )
        finally:
            temp_path.unlink(missing_ok=True)

    @unittest.skipUnless(
        shutil.which("node"),
        "Node is optional; catalog runtime audit test skipped.",
    )
    def test_catalog_runtime_audit_breaks_down_applicability_blockers(self):
        fixture = row_like_fixture()
        condition = (
            fixture["fees"][2]["applicability"]["conditions"][0]
            ["sourceRule"]["childConditions"][0]
        )
        condition["operatorID"] = 99
        fixture["fees"].append(
            {
                "feeTemplateFeeId": "MISSING-INPUT",
                "name": "Missing Input Fee",
                "section": "configured",
                "applicability": {
                    "status": "configured",
                    "conditions": [],
                },
                "calculation": {
                    "mode": "input",
                    "rule": {
                        "amount": 10,
                        "inputs": [],
                    },
                },
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = Path(temp_dir)
            option_name = "option.json"
            (catalog / option_name).write_text(
                json.dumps(fixture),
                encoding="utf-8",
            )
            (catalog / "manifest.json").write_text(
                json.dumps(
                    {
                        "catalogVersion": "test-r1",
                        "options": [
                            {
                                "path": option_name,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "node",
                    str(ROOT / "scripts" / "audit_static_runtime_coverage.mjs"),
                    "--catalog-dir",
                    str(catalog),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            completed.returncode,
            0,
            completed.stderr,
        )
        self.assertIn(
            "APPLICABILITY BLOCKER DETAILS",
            completed.stdout,
        )
        self.assertIn(
            "operatorID=99\tfee-rows=1\tdiagnostics=1",
            completed.stdout,
        )
        self.assertIn(
            "UNSUPPORTED OPERATOR CONTEXTS",
            completed.stdout,
        )
        self.assertIn(
            "field=SidewalkInstallation"
            "\tlabel=Sidewalk Installation"
            "\tcompare=true"
            "\tfee-rows=1"
            "\toptions=1"
            "\tdiagnostics=1",
            completed.stdout,
        )
        self.assertIn(
            "CALCULATION BLOCKER CONTEXTS",
            completed.stdout,
        )
        self.assertIn(
            "blocker=missing-input"
            "\tmode=input"
            "\tinputs=0"
            "\tsources=(none)"
            "\texpressionType=(none)"
            "\tfee-rows=1"
            "\toptions=1"
            "\tfees=Missing Input Fee"
            "\tfee-ids=MISSING-INPUT"
            "\tinput-keys=(none)",
            completed.stdout,
        )
        self.assertIn("UI BLOCKERS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
