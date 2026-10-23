from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from clean_permit_fee_templates import (
    add_all_fees_orphans,
    clean_text,
    collect_vocabulary,
    deduplicate_assignments,
    iter_json_array,
    prune,
    required_inputs,
    required_rule_types,
    sorted_vocabulary,
    walk_templates,
)


DEFAULT_INPUT = "output/inspection-prod/inspection_fee_templates.json"
DEFAULT_OUTPUT = (
    "output/inspection-prod/inspection_fee_templates_clean.json"
)


def normalize_inspection_type(value):
    if not isinstance(value, dict):
        return None
    return prune(
        {
            "inspectionTypeId": clean_text(value.get("InspectionTypeID")),
            "inspectionType": clean_text(value.get("InspectionType")),
            "description": clean_text(value.get("Description")),
            "feeTemplateId": clean_text(value.get("FeeTemplateID")),
            "inspectionTypeModuleId": value.get("InspectionTypeModuleID"),
            "inspectionLinkId": value.get("InspectionLinkID"),
            "inspectionTypeGroupId": clean_text(
                value.get("InspectionTypeGroupID")
            ),
            "departmentId": clean_text(value.get("DepartmentID")),
            "defaultInspectionStatusId": clean_text(
                value.get("DefaultInspectionStatusID")
            ),
            "reinspectionAssignmentId": value.get(
                "ReinspectionAssignmentID"
            ),
            "reinspectWaitTime": value.get("ReinspectWaitTime"),
        }
    )


def inspection_fee_treatment(fee):
    amount_calculable = bool(fee.pop("canAutoEstimate", False))
    fee["canAutoCalculateAmount"] = amount_calculable
    fee["includeInInitialPermitEstimate"] = False
    fee["applicabilityContext"] = "contingent-inspection-event"
    warning = (
        "Do not include by default in an initial permit estimate; an "
        "applicable inspection event or user-selected scenario is required."
    )
    if warning not in fee.setdefault("warnings", []):
        fee["warnings"].append(warning)
    return fee


def normalize_template_record(record, as_of, vocabulary):
    wrapper = record.get("Template") or {}
    root = wrapper.get("feeTemplate")
    advertised_template_id = clean_text(record.get("FeeTemplateID"))
    inspection_types = [
        item
        for value in record.get("InspectionTypes") or []
        if (item := normalize_inspection_type(value)) is not None
    ]

    if not isinstance(root, dict) or not root:
        return (
            prune(
                {
                    "feeTemplateId": advertised_template_id,
                    "hasFeeTemplate": False,
                    "inspectionTypeIds": [
                        item.get("inspectionTypeId")
                        for item in inspection_types
                        if item.get("inspectionTypeId")
                    ],
                    "fees": [],
                    "warnings": [
                        "No fee template was returned for these inspection "
                        "types."
                    ],
                }
            ),
            inspection_types,
        )

    root_template_id = clean_text(root.get("feeTemplateID"))
    rows = walk_templates(root, as_of)
    references = len(rows)
    orphan_count = add_all_fees_orphans(root, rows, as_of)
    fees = [inspection_fee_treatment(fee) for fee in rows]
    fees = deduplicate_assignments(fees)
    fees.sort(
        key=lambda fee: (
            fee.get("order", 0),
            str(fee.get("name") or ""),
            str(fee.get("feeTemplateFeeId") or ""),
        )
    )

    for fee in fees:
        calculation = fee.get("calculation") or {}
        collect_vocabulary(calculation.get("expression"), vocabulary)
        collect_vocabulary(fee.get("applicability"), vocabulary)
        for item in calculation.get("inputs") or []:
            if item.get("inputTypeId") is not None:
                vocabulary["inputTypeIds"].add(item["inputTypeId"])
            if item.get("calculationInputType") is not None:
                vocabulary["calculationInputTypes"].add(
                    item["calculationInputType"]
                )

    rules = required_rule_types(fees)
    if "inspection-event-applicability" not in rules:
        rules.append("inspection-event-applicability")
    classifications = Counter(fee.get("classification") for fee in fees)
    modes = Counter(fee.get("estimateMode") for fee in fees)
    warnings = []
    if (
        advertised_template_id
        and root_template_id
        and advertised_template_id != root_template_id
    ):
        warnings.append(
            "Inventory and returned root fee-template identifiers differ."
        )

    excluded_root_keys = {
        "feeTemplateID",
        "feeTemplateName",
        "fees",
        "allFees",
        "childTemplates",
    }
    template_rules = {
        key: value
        for key, value in root.items()
        if key not in excluded_root_keys
    }
    inspection_type_ids = sorted(
        {
            item.get("inspectionTypeId")
            for item in inspection_types
            if item.get("inspectionTypeId")
        }
    )
    inspection_type_names = sorted(
        {
            item.get("inspectionType")
            for item in inspection_types
            if item.get("inspectionType")
        }
    )
    result = {
        "feeTemplateId": root_template_id or advertised_template_id,
        "feeTemplate": clean_text(root.get("feeTemplateName")),
        "hasFeeTemplate": True,
        "inspectionTypeIds": inspection_type_ids,
        "inspectionTypeNames": inspection_type_names,
        "templateRules": prune(template_rules),
        "defaultEstimateTreatment": (
            "exclude-unless-inspection-scenario-selected"
        ),
        "fees": fees,
        "requiredInputs": required_inputs(fees),
        "requiredRuleTypes": sorted(rules),
        "summary": {
            "inspectionTypes": len(inspection_type_ids),
            "configuredFeeReferences": references,
            "allFeesOnlyAssignments": orphan_count,
            "distinctFeeAssignments": len(fees),
            "selectableFees": sum(
                bool(fee.get("selectable")) for fee in fees
            ),
            "amountAutoCalculableFees": sum(
                bool(fee.get("canAutoCalculateAmount")) for fee in fees
            ),
            "apiFlaggedReinspectionFees": sum(
                bool((fee.get("flags") or {}).get("reinspection"))
                for fee in fees
            ),
            "reinspectionNamedFees": sum(
                "reinspection" in str(fee.get("name") or "").casefold()
                for fee in fees
            ),
            "classificationCounts": dict(sorted(classifications.items())),
            "estimateModeCounts": dict(sorted(modes.items())),
        },
        "warnings": warnings,
    }
    return prune(result), inspection_types


def merge_template(existing, incoming):
    existing_ids = set(existing.get("inspectionTypeIds") or [])
    existing_ids.update(incoming.get("inspectionTypeIds") or [])
    existing["inspectionTypeIds"] = sorted(existing_ids)
    existing_names = set(existing.get("inspectionTypeNames") or [])
    existing_names.update(incoming.get("inspectionTypeNames") or [])
    existing["inspectionTypeNames"] = sorted(existing_names)
    if existing.get("fees") != incoming.get("fees"):
        warning = (
            "Duplicate fee-template records had different normalized fee "
            "content; the first record was retained."
        )
        if warning not in existing.setdefault("warnings", []):
            existing["warnings"].append(warning)


def build_document(input_path, as_of):
    vocabulary = {
        "expressionTypes": set(),
        "variableTypes": set(),
        "returnTypes": set(),
        "operandTypeIds": set(),
        "operandDataTypeIds": set(),
        "operatorIds": set(),
        "inputTypeIds": set(),
        "calculationInputTypes": set(),
    }
    template_index = {}
    inspection_type_index = {}

    for number, record in enumerate(iter_json_array(input_path), start=1):
        template, inspection_types = normalize_template_record(
            record,
            as_of,
            vocabulary,
        )
        template_id = template.get("feeTemplateId") or f"record-{number}"
        if template_id in template_index:
            merge_template(template_index[template_id], template)
        else:
            template_index[template_id] = template
        for item in inspection_types:
            inspection_type_id = item.get("inspectionTypeId")
            if inspection_type_id:
                inspection_type_index[inspection_type_id] = item
        if number % 10 == 0:
            print(f"Cleaned {number:,} inspection fee templates...")

    templates = sorted(
        template_index.values(),
        key=lambda item: (
            str(item.get("feeTemplate") or ""),
            str(item.get("feeTemplateId") or ""),
        ),
    )
    inspection_types = sorted(
        inspection_type_index.values(),
        key=lambda item: (
            str(item.get("inspectionType") or ""),
            str(item.get("inspectionTypeId") or ""),
        ),
    )
    all_fees = [
        fee for template in templates for fee in template.get("fees") or []
    ]
    classifications = Counter(
        fee.get("classification") for fee in all_fees
    )
    modes = Counter(fee.get("estimateMode") for fee in all_fees)

    return {
        "schemaVersion": "1.0",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "asOfDate": as_of.isoformat(),
        "source": {
            "file": input_path.name,
            "configurationOnly": True,
            "allFeesHandling": (
                "Template trees are authoritative. Root allFees entries are "
                "used only when an assignment is absent from the tree."
            ),
        },
        "calculationContract": {
            "defaultApplicability": (
                "Inspection fees are excluded from an initial permit "
                "estimate unless an applicable inspection event or explicit "
                "scenario is selected."
            ),
            "amountAutoCalculable": (
                "An amount may be auto-calculable after inspection-event "
                "applicability and all fee rules are resolved."
            ),
            "futureEngineMust": [
                "Resolve the applicable inspection event or scenario.",
                "Evaluate fee and template conditions.",
                "Resolve child-template applicability.",
                "Evaluate every encountered EnerGov expression type.",
                "Resolve required inputs and apply per-unit calculations.",
                "Apply tiers, rounding, discounts, proration, and late fees.",
                "Reject an amount when a required rule is unsupported.",
            ],
            "reinspectionFlagCaveat": (
                "The production API returned false for isReInspectionFee on "
                "historically confirmed reinspection assignments. Preserve "
                "the flag, but do not use it as the sole classifier."
            ),
        },
        "ruleVocabulary": sorted_vocabulary(vocabulary),
        "summary": {
            "inspectionTypes": len(inspection_types),
            "distinctFeeTemplates": len(templates),
            "distinctFeeAssignments": len(all_fees),
            "uniqueFeeTemplateFeeIds": len(
                {
                    fee.get("feeTemplateFeeId")
                    for fee in all_fees
                    if fee.get("feeTemplateFeeId")
                }
            ),
            "selectableFees": sum(
                bool(fee.get("selectable")) for fee in all_fees
            ),
            "amountAutoCalculableFees": sum(
                bool(fee.get("canAutoCalculateAmount")) for fee in all_fees
            ),
            "apiFlaggedReinspectionFees": sum(
                bool((fee.get("flags") or {}).get("reinspection"))
                for fee in all_fees
            ),
            "reinspectionNamedFees": sum(
                "reinspection" in str(fee.get("name") or "").casefold()
                for fee in all_fees
            ),
            "classificationCounts": dict(sorted(classifications.items())),
            "estimateModeCounts": dict(sorted(modes.items())),
        },
        "inspectionTypes": inspection_types,
        "feeTemplates": templates,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create normalized, estimator-oriented JSON from the raw "
            "EnerGov inspection fee-template extract."
        )
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        default=DEFAULT_INPUT,
        help=f"Raw inspection template JSON (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Clean JSON destination (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--as-of",
        default=date.today().isoformat(),
        help="Effective date used to select fee setups (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Write compact JSON instead of indented JSON.",
    )
    args = parser.parse_args()

    input_path = Path(args.json_file)
    output_path = Path(args.output)
    if not input_path.exists():
        raise FileNotFoundError(f"Could not find {input_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different.")

    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
    document = build_document(input_path, as_of)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(
                document,
                handle,
                ensure_ascii=False,
                indent=None if args.compact else 2,
                separators=(",", ":") if args.compact else None,
            )
            handle.write("\n")
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    summary = document["summary"]
    print(f"Inspection types:          {summary['inspectionTypes']:,}")
    print(f"Fee templates:             {summary['distinctFeeTemplates']:,}")
    print(f"Fee assignments:           {summary['distinctFeeAssignments']:,}")
    print(f"Unique assignment IDs:     {summary['uniqueFeeTemplateFeeIds']:,}")
    print(f"Selectable fees:           {summary['selectableFees']:,}")
    print(
        "Amount auto-calculable:  "
        f"{summary['amountAutoCalculableFees']:,}"
    )
    print(
        "API reinspection flags:  "
        f"{summary['apiFlaggedReinspectionFees']:,}"
    )
    print(f"Clean JSON:               {output_path}")


if __name__ == "__main__":
    main()
