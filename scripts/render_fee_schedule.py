from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

from input_fee_evaluator import VALIDATED_PERCENTAGE_INPUT_FEE_NAMES, _validated_multi_input_sum
from option_ui_schema import build_option_ui_schema, clean_staff_facing_label, display_option_label
from project_public_catalog import DEFAULT_MINIMUM_SAMPLE_SIZE, OptionProjection
from prototype_estimator import display_fee_name, is_explicit_later_event_fee

ROOT = Path(__file__).resolve().parents[1]
DRAFT = "Draft: Staff Review Pending"
LIMITS = (
    "This document describes configured fees and calculation methods. It does not assess a project, "
    "calculate a total, or provide an invoice or final fee statement. Staff must confirm applicability and amounts."
)


def text(value):
    return str(value if value is not None else "").replace("\u2014", " - ").strip()


def label(value, surface="general"):
    return text(clean_staff_facing_label(value, surface=surface, strip_technical_context=True))


def number(value):
    return f"{value:,.10g}" if isinstance(value, (int, float)) else "not specified"


def money(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "not specified"
    amount = Decimal(str(value))
    places = max(2, -amount.as_tuple().exponent)
    return f"${amount:,.{places}f}"


class Schedule:
    """Describe source configuration without evaluating fees or project applicability."""

    def __init__(self, document, *, internal=False):
        self.document = document
        self.internal = internal
        self.controls = build_option_ui_schema(document)["controls"]
        self.fields = {item["key"]: item for item in self.controls}
        self.fees = {str(f.get("feeTemplateFeeId")): f for f in document["fees"]}
        if len(self.fees) != len(document["fees"]) or any(not f.get("feeTemplateFeeId") for f in document["fees"]):
            raise ValueError("Fee assignments must have unique source identities.")
        self.references = {key: f"F{index:03d}" for index, key in enumerate(self.fees, 1)}

    def input_label(self, item):
        key = str(item.get("inputKey") or "")
        if item.get("source") == "fee":
            fee = self.fees.get(key)
            if fee:
                suffix = "; inactive, staff review required" if fee.get("section") == "inactive" else ""
                return self.references[key] + suffix
            return "a fee that staff must identify"
        field = self.fields.get(key) or {}
        return text(field.get("displayLabel")) or label(item.get("label") or key)

    def condition(self, condition):
        key = str(condition.get("operandValue") or "")
        field = self.fields.get(key) or {}
        name = text(field.get("displayLabel")) or label(condition.get("operandFriendlyName") or key)
        operator = condition.get("operatorID")
        if field and not field.get("displayReady", True):
            return f"{name}: staff must confirm the field and available choices"
        if operator in (7, 8):
            return f"{name} {'is blank' if operator == 7 else 'is provided'}"
        expression = condition.get("compareExpression") or {}
        if operator not in (1, 2, 3) or "value" not in expression or expression.get("expressionType", 7) != 7:
            return f"{name}: staff must confirm this condition"
        value = expression["value"]
        choices = field.get("choices") or []
        if choices:
            matches = [c for c in choices if str(c["value"]).strip().casefold() == str(value).strip().casefold()]
            if len(matches) != 1 or not matches[0].get("label"):
                return f"{name}: configured choice is not available on the selected form; staff review required"
            value = matches[0]["label"]
        elif re.fullmatch(r"[0-9a-fA-F-]{36}", str(value)):
            return f"{name}: staff must confirm the configured selection"
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        operation = {1: "is", 2: "is not", 3: "is greater than"}[operator]
        return f"{name} {operation} {text(value)}"

    def group(self, group):
        parts = [self.condition(c) for c in group.get("childConditions") or []]
        parts += [self.group(g) for g in group.get("childConditionGroups") or []]
        if not parts:
            return "Staff must confirm this condition"
        joiner = " OR " if group.get("isOrGroup") else " AND "
        return "(" + joiner.join(parts) + ")" if len(parts) > 1 else parts[0]

    def conditions(self, fee):
        parts = []
        for condition in (fee.get("applicability") or {}).get("conditions") or []:
            if condition.get("kind") == "expression" and isinstance(condition.get("sourceRule"), dict):
                parts.append(self.group(condition["sourceRule"]))
            else:
                parts.append("Staff must confirm the configured condition or event.")
        return " AND ".join(parts) or "No separate condition listed."

    def method(self, fee):
        calculation = fee.get("calculation") or {}
        rule = calculation.get("rule") or {}
        inputs = rule.get("inputs") or []
        names = [self.input_label(item) for item in inputs]
        mode, rate = calculation.get("mode"), rule.get("amount")
        component = fee.get("assessmentRole") == "dependency-only"
        amount = number if component else money
        lines = []
        if mode == "fixed":
            lines.append(("Fixed value: " if component else "Fixed fee: ") + amount(calculation.get("fixedAmount")))
        elif mode == "input":
            percent = str(fee.get("name") or "").strip().casefold() in VALIDATED_PERCENTAGE_INPUT_FEE_NAMES
            unique_inputs = len({(i.get("source"), i.get("inputKey")) for i in inputs}) == len(inputs)
            if percent and (len(inputs) == 1 or (unique_inputs and _validated_multi_input_sum(fee, inputs))):
                basis = ("sum of " if len(inputs) > 1 else "") + "; ".join(names)
                lines.append(f"{number(rate)}% of {basis}")
            else:
                lines.append(f"Configured rate/multiplier: {number(rate)}")
                lines.append("Inputs: " + "; ".join(names) if names else "Staff must confirm the input basis.")
                if len(inputs) > 1:
                    lines.append("Staff must confirm how the inputs are combined.")
        elif mode == "tiered":
            lines.append("Tier schedule; input: " + ("; ".join(names) or "staff confirmation required"))
            for tier in rule.get("tiers") or []:
                upper = "no upper limit" if tier.get("unlimited") else number(tier.get("upperLimit"))
                line = f"{number(tier.get('lowerLimit'))} to {upper}: {amount(tier.get('amount'))}"
                if tier.get("additionalAmount"):
                    line += (f" plus {amount(tier['additionalAmount'])} per {number(tier.get('additionalUnit'))} "
                             f"units above {number(tier.get('over'))}")
                lines.append(line)
            if not rule.get("tiers"):
                lines.append("Tier details are missing; staff must confirm the method.")
        elif mode == "formula":
            lines.append("Formula inputs: " + ("; ".join(names) or "not specified"))
            lines.append("Complete formula: staff confirmation required.")
        else:
            if rate:
                lines.append(f"Configured amount or rate: {number(rate)}.")
        for key, heading in (("minimumAmount", "Minimum"), ("maximumAmount", "Maximum")):
            value = calculation.get(key)
            if isinstance(value, (int, float)) and value > 0:
                lines.append(f"{heading}: {amount(value)}")
        rounding = (rule.get("roundingType"), rule.get("roundingValue"))
        unit = "unit" if component else "dollar"
        if rounding == (2, 1):
            lines.append(f"Round up to a whole {unit}.")
        elif rounding == (4, 1):
            lines.append(f"Round to the nearest whole {unit}; halves round up.")
        elif rounding[0] not in (None, 1) or rounding[1] not in (None, 0):
            lines.append("Staff must confirm the configured rounding method.")
        return lines

    def project(self):
        groups = {"primaryFees": [], "additionalFees": [], "calculationComponents": []}
        omitted = 0
        for fee in self.document["fees"]:
            section = fee.get("section")
            if section == "inactive":
                omitted += 1
                continue
            review = text(fee.get("estimationReview"))
            if section == "manual-review":
                review = review or "Staff must confirm the amount and whether this fee applies."
            calculation = fee.get("calculation") or {}
            if calculation.get("mode") not in {"fixed", "input", "tiered", "formula"}:
                review = review or "Staff must confirm the calculation method."
            if calculation.get("mode") == "fixed" and calculation.get("fixedAmount") is None:
                review = review or "The fixed amount is missing; staff must confirm it."
            if fee.get("assessmentRole") == "dependency-only":
                group = "calculationComponents"
            elif review or section in ("manual-review", "event-contingent") or is_explicit_later_event_fee(fee):
                group = "additionalFees"
            else:
                group = "primaryFees"
            groups[group].append({
                "reference": self.references[str(fee.get("feeTemplateFeeId"))],
                "name": label(display_fee_name(fee["name"]), "fee"),
                "method": self.method(fee), "conditions": self.conditions(fee), "review": review,
            })
        # Reuse the site's history suppression and rounding, not raw counts.
        public = OptionProjection(self.document, option_id="schedule", permit_type_id="permit",
                                  work_class_id="work", minimum_sample_size=DEFAULT_MINIMUM_SAMPLE_SIZE).project()
        related = []
        for relation in public["relatedPermits"]:
            history = relation["history"]
            if not (history.get("status") == "limited-history" or (history.get("percent") or 0) > 0):
                continue
            if relation["historyBasis"] != "active-fee" and not relation["fees"]:
                continue
            related.append({
                "permitType": text(display_option_label(relation["permitType"])),
                "workClass": text(display_option_label(relation["workClass"])),
                "history": "Limited history; no percentage shown" if history["status"] == "limited-history"
                           else f"{history['percent']}% of similar past projects",
                "historyBasis": "Related permit with active fees" if relation["historyBasis"] == "active-fee" else "Related permit",
                "feeHistoryBasis": "Past projects containing this related permit",
                "fees": [{"name": label(display_fee_name(f["name"]), "fee"),
                          "history": "Limited history" if f["history"]["status"] != "available"
                                     else ("Less than 1%" if f["history"]["percent"] == 0 else f"{f['history']['percent']}%")}
                         for f in relation["fees"]],
            })
        option = self.document["option"]
        return {
            "schemaVersion": "schedule-draft-1.0",
            "status": "Internal Draft: Staff Review Pending" if self.internal else DRAFT,
            "internal": self.internal,
            **({"configurationDate": self.document["configuration"]["asOfDate"],
                "historicalPeriod": self.document["configuration"].get("historicalPeriod") or {}}
               if self.internal else {}),
            "permitType": text(display_option_label(option["permitType"])),
            "workClass": text(display_option_label(option["workClass"])),
            "limitations": LIMITS, "inactiveFeesOmitted": omitted,
            "inputs": [{"name": text(c["displayLabel"]),
                        "group": text((c.get("presentationGroup") or {}).get("label")),
                        "detail": "Staff must confirm the field and available choices" if not c["displayReady"]
                                  else "Choices: " + "; ".join(text(x["label"]) for x in c["choices"])
                                  if c["choices"] else "Numeric project value"} for c in self.controls],
            **groups, "relatedPermits": related,
        }


def render_html(model):
    esc = lambda value: html.escape(text(value), quote=True)
    logo = base64.b64encode((ROOT / "docs/images/raleigh_horizontal.png").read_bytes()).decode("ascii")
    orientation = " landscape" if sum(len(model[k]) for k in ("primaryFees", "additionalFees", "calculationComponents")) > 40 else ""

    def table(headings, rows):
        header = "".join(f"<th>{esc(h)}</th>" for h in headings)
        body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
        return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"

    def fees(title, key, note):
        rows = []
        for fee in model[key]:
            method = "<br>".join(esc(line) for line in fee["method"])
            if fee["review"]:
                method += f"<p class='review'>{esc(fee['review'])}</p>"
            rows.append((f"<small>{esc(fee['reference'])}</small><br><strong>{esc(fee['name'])}</strong>",
                         method, esc(fee["conditions"])))
        body = table(("Fee / Reference", "Configured Method", "When It May Apply"), rows) if rows else "<p>No entries listed.</p>"
        return f"<section><h2>{esc(title)}</h2><p>{esc(note)}</p>{body}</section>"

    grouped = {}
    for item in model["inputs"]:
        grouped.setdefault(item["group"] or "Project Details", []).append(item)
    inputs = table(("Project Details", "Fields and Choices"), [
        (esc(group), "<br>".join(f"<strong>{esc(i['name'])}</strong>: {esc(i['detail'])}" for i in items))
        for group, items in grouped.items()
    ]) if grouped else "<p>No customer-entered project fields are listed for the primary fees. Staff may need other information.</p>"
    input_section = ("<h2>Project Information Used by the Fee Rules</h2>"
                     "<p>The fields below describe information that may be needed, not requirements for every project.</p>" + inputs
                     if grouped else "<p><strong>Project Information:</strong> No customer-entered fields are listed for the primary fees. Staff may need other information.</p>")
    related_rows = []
    for relation in model["relatedPermits"]:
        fee_names = "<br>".join(f"{esc(f['name'])} ({esc(f['history'])})" for f in relation["fees"])
        related_rows.append((f"<strong>{esc(relation['permitType'])}</strong><br>{esc(relation['workClass'])}",
                             esc(relation["history"]) + "<br><small>" + esc(relation["historyBasis"]) + "</small>",
                             fee_names or "No observed fee names listed"))
    related = ("<p>Informational only. Historical occurrence does not establish that a permit or fee is required. "
               "Related-permit fees are not added to a total. Permit percentages use similar past projects of the selected type and work class; "
               "fee percentages use only past projects containing that related permit. Small samples do not receive a percentage.</p>"
               + table(("Related Permit", "Historical Occurrence", "Fees Seen with This Related Permit (Informational Only)"), related_rows)
               if related_rows else "<p>No related permits are shown under the estimator's history rules.</p>")
    footnote = ""
    if model.get("internal"):
        parts = []
        if model.get("configurationDate"):
            parts.append("Fee configuration as of " + text(model["configurationDate"]))
        period = model.get("historicalPeriod") or {}
        if period.get("startDate") and period.get("endDate"):
            parts.append(f"Past projects reviewed: {period['startDate']} to {period['endDate']}")
        if parts:
            footnote = '<p class="internal-note"><em>' + esc(". ".join(parts) + ".") + '</em></p>'
    footer_label = "Internal Draft" if model.get("internal") else "Draft"
    component_section = fees("Calculation Components: Not Separate Charges", "calculationComponents",
                             "These values are inputs to other calculations. They are not additional charges. References identify the configured rows.") if model["calculationComponents"] else ""
    primary = fees("Primary Fee Schedule and Methodology", "primaryFees",
                   "These are configured fees, not a list of charges due. Conditions and project details determine which may apply.")
    additional = fees("Additional Fees That May Apply", "additionalFees",
                      "Informational only. These fees may apply depending on the project and its scope, staff review, or later events such as reinspections. They are not added to a total.")
    title = f"{model['permitType']} / {model['workClass']}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>{esc(title)}: Draft Fee Schedule</title>
<style>
@page {{ size: letter{orientation}; margin: 12mm 12mm 15mm;
  @bottom-left {{ content: "{footer_label}"; font: 8pt Arial; color: #555; }}
  @bottom-right {{ content: "Page " counter(page) " of " counter(pages); font: 8pt Arial; color: #555; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; color: #202124; font: 9pt/1.35 Arial, sans-serif; }}
header {{ display: flex; align-items: center; gap: 22px; border-bottom: 2px solid #277443; padding-bottom: 12px; }}
header img {{ width: 125px; height: auto; }}
h1 {{ font-size: 18pt; margin: 0 0 4px; }}
h2 {{ font-size: 12pt; color: #245c36; margin: 14px 0 6px; break-after: avoid; }}
h2 + p {{ break-after: avoid; }}
p {{ margin: 6px 0 10px; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 12px; }}
th, td {{ text-align: left; vertical-align: top; padding: 5px 7px; border: 1px solid #cfd7d1; overflow-wrap: anywhere; }}
th {{ background: #eaf0ec; font-size: 8.5pt; }}
th:first-child {{ width: 27%; }}
th:nth-child(2) {{ width: 40%; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
small {{ color: #555; font-size: 8pt; }}
.draft {{ border-left: 4px solid #956900; background: #fff7df; padding: 10px 12px; margin: 14px 0; }}
.draft strong {{ display: block; margin-bottom: 4px; }}
.review {{ color: #773a00; font-weight: bold; margin: 5px 0 0; }}
.internal-note {{ font-size: 8pt; color: #666; }}
@media screen {{ body {{ max-width: 820px; margin: 30px auto; padding: 20px; }} }}
</style></head><body>
<header><img alt="City of Raleigh" src="data:image/png;base64,{logo}"><div><h1>Fee Schedule and Calculation Methods</h1>
<strong>{esc(model['permitType'])}</strong><br>{esc(model['workClass'])}</div></header>
<div class="draft"><strong>{esc(model['status'])}</strong>{esc(model['limitations'])}</div>
{input_section}
{primary}{additional}{component_section}
<section><h2>Related Permits</h2>{related}</section>
<h2>Source and Limitations</h2>
<p>Fee information comes from EnerGov. Fees may have changed; staff must confirm the current amounts.
F-number references identify rows in this document, not additional charges. Absence of a listed condition does not establish that a fee applies.
Inactive fee rows are omitted ({model['inactiveFeesOmitted']}). No fees have been added together.</p>
{footnote}
</body></html>"""


def load_option(catalog, permit_type, work_class, *, internal=False):
    catalog = Path(catalog).resolve()
    manifest_path = catalog / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "1.0":
        raise ValueError("Use the complete internal catalog, not the reduced docs/data package.")
    matches = [o for o in manifest["options"] if (o["permitType"], o["workClass"]) == (permit_type, work_class)]
    if len(matches) != 1:
        raise ValueError("Select exactly one catalog option using its source permit type and work class.")
    source = (catalog / matches[0]["path"]).resolve()
    if not source.is_relative_to(catalog / "options"):
        raise ValueError("Option path must remain inside the catalog's options directory.")
    document = json.loads(source.read_text(encoding="utf-8"))
    option = document.get("option") or {}
    if (option.get("permitType"), option.get("workClass")) != (permit_type, work_class) or option.get("optionKey") != matches[0].get("optionKey"):
        raise ValueError("Option identity does not match its manifest entry.")
    if document.get("schemaVersion") != "1.0" or document.get("catalogVersion") != manifest.get("catalogVersion"):
        raise ValueError("Option and manifest versions do not match.")
    if document.get("configuration", {}).get("asOfDate") != manifest.get("configurationAsOfDate"):
        raise ValueError("Option and manifest configuration dates do not match.")
    model = Schedule(document, internal=internal).project()
    model.update({
        "sourceOptionSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "sourceManifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    })
    return model


def print_pdf(source, destination, browser=None):
    candidates = [browser, shutil.which("google-chrome"), shutil.which("chromium"),
                  str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe")]
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    if not executable:
        raise FileNotFoundError("Chrome was not found. Supply --browser or print the generated HTML to PDF.")
    with tempfile.TemporaryDirectory(prefix="sb445-schedule-", ignore_cleanup_errors=True) as profile:
        command = [executable, "--headless=new", "--no-first-run", "--no-default-browser-check",
                   "--disable-background-networking", "--disable-component-update", "--disable-sync",
                   "--disable-extensions", "--user-data-dir=" + profile, "--no-pdf-header-footer",
                   "--print-to-pdf=" + str(destination.resolve()), source.resolve().as_uri()]
        result = subprocess.run(command, capture_output=True, timeout=45,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not destination.is_file() or not destination.read_bytes().startswith(b"%PDF-"):
        raise RuntimeError("Chrome did not produce a valid PDF. The HTML and rendering JSON are retained.")


def main():
    parser = argparse.ArgumentParser(description="Render an internal draft fee schedule without calculating a project estimate.")
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--permit-type", required=True)
    parser.add_argument("--work-class", required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="New local directory; existing files are never overwritten.")
    parser.add_argument("--pdf", action="store_true", help="Print locally using installed Chrome.")
    parser.add_argument("--internal", action="store_true", help="Include source dates as a small footnote for internal use.")
    parser.add_argument("--browser", help="Optional Chrome executable path.")
    args = parser.parse_args()
    model = load_option(args.catalog, args.permit_type, args.work_class, internal=args.internal)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    source = args.output_dir / "fee-schedule.html"
    source.write_text(render_html(model), encoding="utf-8")
    (args.output_dir / "fee-schedule.json").write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.pdf:
        print_pdf(source, args.output_dir / "fee-schedule.pdf", args.browser)
    print(f"DRAFT SCHEDULE: {model['permitType']} / {model['workClass']}")
    print("Rows: " + ", ".join(f"{key}={len(model[key])}" for key in
                               ("primaryFees", "additionalFees", "calculationComponents", "relatedPermits")))
    print(f"Files: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
