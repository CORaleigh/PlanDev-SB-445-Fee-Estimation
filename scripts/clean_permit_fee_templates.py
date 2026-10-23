from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from analyze_permit_fees import (
    choose_current_setup,
    classify_fee,
    is_true,
    parse_date,
)


DEFAULT_INPUT = "output/permit_fee_templates.json"
DEFAULT_OUTPUT = "output/permit_fee_templates_clean.json"
SYSTEM_INPUT_KEYS = {"SquareFeet", "ValuationValue"}


def clean_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def prune(value):
    """Remove empty API scaffolding while preserving zero and false."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            cleaned = prune(item)
            if cleaned not in (None, "", [], {}):
                result[key] = cleaned
        return result
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := prune(item)) not in (None, "", [], {})
        ]
    return clean_text(value)


def condition_has_rules(condition):
    if not isinstance(condition, dict):
        return bool(clean_text(condition))
    if condition.get("childConditions"):
        return True
    return any(
        condition_has_rules(group)
        for group in condition.get("childConditionGroups") or []
    )


def variable_input(item):
    if not isinstance(item, dict):
        return False
    if is_true(item.get("isConstantBased")):
        return False
    return True


def normalize_input(item):
    if not isinstance(item, dict):
        return None

    input_key = clean_text(item.get("inputValue"))
    input_type_id = item.get("feeTemplateInputTypeID")

    if is_true(item.get("isConstantBased")):
        source = "constant"
    elif is_true(item.get("isCustomField")):
        source = "custom-field"
    elif is_true(item.get("isTotalBased")):
        source = "total"
    elif is_true(item.get("isFeeBased")):
        source = "fee"
    elif str(input_type_id) == "1" and input_key in SYSTEM_INPUT_KEYS:
        source = "system"
    else:
        source = "other"

    return {
        "feeInputId": clean_text(
            item.get("workflowTemplateFeeInputID")
        ),
        "inputKey": input_key,
        "label": clean_text(item.get("inputFriendlyName")),
        "source": source,
        "inputTypeId": input_type_id,
        "inputType": item.get("inputType"),
        "calculationInputType": item.get("feeCalculationInputType"),
        "constant": item.get("feeConstant"),
        "translations": prune(item.get("translations")),
        "displayedInEnerGov": is_true(item.get("isDisplay")),
        "variable": variable_input(item),
    }


def normalize_tier(row):
    if not isinstance(row, dict):
        return None
    return prune(
        {
            "lowerLimit": row.get("lowerLimit"),
            "upperLimit": row.get("upperLimit"),
            "unlimited": row.get("unlimited"),
            "amount": row.get("amount"),
            "additionalAmount": row.get("additionalAmount"),
            "additionalUnit": row.get("additionalUnit"),
            "over": row.get("over"),
        }
    )


def setup_counts(setups, as_of):
    dated = 0
    undated = 0
    for setup in setups or []:
        if not isinstance(setup, dict):
            continue
        start = parse_date(setup.get("startDate"))
        end = parse_date(setup.get("endDate"))
        if start is None and end is None:
            undated += 1
        elif (start is None or start <= as_of) and (
            end is None or end >= as_of
        ):
            dated += 1
    return dated, undated


def has_advanced_rules(fee, setup, node):
    return any(
        (
            is_true(fee.get("isCPIFee")),
            is_true(fee.get("isProrateFee")),
            is_true(fee.get("isProrationByDate")),
            is_true(fee.get("isTotalFeeBased")),
            is_true(fee.get("isFeeBased")),
            bool(fee.get("discounts")),
            bool(fee.get("feeProrationInputs")),
            bool(setup and setup.get("prorateSchedules")),
            bool(setup and setup.get("feeLateFeeCalcs")),
            bool(node.get("discounts")),
        )
    )


def estimate_mode(
    classification,
    relationship,
    has_condition,
    advanced_rules,
):
    if classification in ("Disabled / inactive", "No current setup"):
        return "unavailable"
    if classification == "Manual":
        return "manual-review"
    if classification == "Formula-based":
        return "formula-review"
    if classification == "Tiered":
        return "tier-rule-review"
    if classification == "Input / per-unit":
        return "input-rule-review"
    if advanced_rules:
        return "advanced-rule-review"
    if relationship == "AllFeesOnly" or has_condition:
        return "conditional-fixed"
    if classification == "Fixed-dollar":
        return "fixed"
    return "configuration-review"


def fee_warnings(
    classification,
    relationship,
    has_condition,
    effective_setup_count,
    advanced_rules,
):
    warnings = []
    if relationship == "AllFeesOnly":
        warnings.append(
            "Assignment appears only in allFees; applicability is unresolved."
        )
    if has_condition:
        warnings.append("Fee condition must be evaluated.")
    if classification == "Input / per-unit":
        warnings.append("Input and rate semantics require validation.")
    elif classification == "Tiered":
        warnings.append("Tier selection and calculation require validation.")
    elif classification == "Formula-based":
        warnings.append("EnerGov expression requires a supported evaluator.")
    elif classification == "Manual":
        warnings.append("Amount requires manual review.")
    elif classification == "No current setup":
        warnings.append("No pricing setup is effective on the as-of date.")
    elif classification == "Disabled / inactive":
        warnings.append("Fee is disabled or inactive.")
    if effective_setup_count > 1:
        warnings.append("Multiple dated setups are effective on the as-of date.")
    if advanced_rules:
        warnings.append("Advanced fee rules must be evaluated.")
    return warnings


def normalize_fee(
    fee,
    node,
    depth,
    path,
    as_of,
    relationship=None,
):
    setups = fee.get("feeSetups") or []
    setup, pricing_status = choose_current_setup(setups, as_of)
    classification = classify_fee(fee, setup, pricing_status)
    relationship = relationship or ("Direct" if depth == 0 else "Child")
    fee_condition = fee.get("condition")
    template_condition = node.get("condition")
    template_expression = prune(node.get("expression"))
    has_fee_condition = condition_has_rules(fee_condition)
    has_template_condition = condition_has_rules(template_condition)
    has_template_rule = has_template_condition or bool(template_expression)
    has_condition = has_fee_condition or has_template_rule
    advanced_rules = has_advanced_rules(fee, setup, node)
    dated_count, undated_count = setup_counts(setups, as_of)
    effective_count = dated_count or (
        undated_count if pricing_status == "Undated fallback" else 0
    )
    mode = estimate_mode(
        classification,
        relationship,
        has_condition,
        advanced_rules,
    )

    inputs = [
        normalized
        for item in fee.get("feeInputs") or []
        if (normalized := normalize_input(item)) is not None
    ]
    tiers = []
    if setup:
        tiers = [
            normalized
            for row in setup.get("feeAdjustableCalcs") or []
            if (normalized := normalize_tier(row)) is not None
        ]
        tiers.sort(
            key=lambda row: (
                row.get("lowerLimit") is None,
                row.get("lowerLimit", 0),
            )
        )

    return prune(
        {
            "feeTemplateFeeId": fee.get("feeTemplateFeeID"),
            "feeId": fee.get("feeID"),
            "name": fee.get("feeName"),
            "description": fee.get("feeDescription"),
            "notes": fee.get("notes"),
            "feeTypeId": fee.get("feeTypeID"),
            "feeType": fee.get("feeType"),
            "order": fee.get("feeOrder"),
            "priority": fee.get("feePriority"),
            "classification": classification,
            "estimateMode": mode,
            "selectable": mode != "unavailable",
            "canAutoEstimate": mode == "fixed",
            "requiresInput": classification in (
                "Input / per-unit",
                "Tiered",
            ),
            "sourceTemplateId": node.get("feeTemplateID"),
            "sourceTemplate": node.get("feeTemplateName"),
            "templateRelationship": relationship,
            "templatePaths": [path],
            "flags": {
                "active": fee.get("active"),
                "manual": fee.get("isManual"),
                "hidden": fee.get("isHidden"),
                "disabled": fee.get("isDisable"),
                "online": fee.get("isOnlineFee"),
                "tax": fee.get("isTaxFee"),
                "accountsReceivable": fee.get("isARFee"),
                "timeTracking": fee.get("isTimeTrackingFee"),
                "cpi": fee.get("isCPIFee"),
                "prorated": fee.get("isProrateFee"),
                "prorationByDate": fee.get("isProrationByDate"),
                "reinspection": fee.get("isReInspectionFee"),
            },
            "applicability": {
                "hasCondition": has_condition,
                "feeCondition": (
                    prune(fee_condition) if has_fee_condition else None
                ),
                "templateCondition": (
                    prune(template_condition)
                    if has_template_condition
                    else None
                ),
                "templateExpression": template_expression,
            },
            "calculation": {
                "pricingStatus": pricing_status,
                "feeSetupId": setup.get("feeSetupID") if setup else None,
                "startDate": setup.get("startDate") if setup else None,
                "endDate": setup.get("endDate") if setup else None,
                "amount": setup.get("amount") if setup else None,
                "computationValue": (
                    setup.get("computationValue") if setup else None
                ),
                "computationValueName": (
                    setup.get("computationValueName") if setup else None
                ),
                "minimum": setup.get("minimum") if setup else None,
                "maximum": setup.get("maximum") if setup else None,
                "roundingType": setup.get("roundingType") if setup else None,
                "roundingValue": setup.get("roundingValue") if setup else None,
                "effectiveSetupCount": effective_count,
                "inputs": inputs,
                "tiers": tiers,
                "expression": prune(fee.get("expression")),
                "advancedRules": {
                    "totalFeeBased": fee.get("isTotalFeeBased"),
                    "feeBased": fee.get("isFeeBased"),
                    "feeRoundingType": fee.get("roundingType"),
                    "feeRoundingValue": fee.get("roundingValue"),
                    "discounts": prune(fee.get("discounts")),
                    "feeProrationInputs": prune(
                        fee.get("feeProrationInputs")
                    ),
                    "prorateModelId": (
                        setup.get("prorateModelID") if setup else None
                    ),
                    "setupProrationByDate": (
                        setup.get("isProrationByDate") if setup else None
                    ),
                    "prorateSchedules": (
                        prune(setup.get("prorateSchedules"))
                        if setup
                        else None
                    ),
                    "lateFeeCalculations": (
                        prune(setup.get("feeLateFeeCalcs"))
                        if setup
                        else None
                    ),
                    "templateDiscounts": prune(node.get("discounts")),
                },
            },
            "warnings": fee_warnings(
                classification,
                relationship,
                has_condition,
                effective_count,
                advanced_rules,
            ),
        }
    )


def assignment_key(fee):
    return (
        fee.get("feeTemplateFeeId")
        or "|".join(
            str(fee.get(name) or "")
            for name in (
                "sourceTemplateId",
                "feeId",
                "name",
            )
        )
    )


def raw_assignment_key(fee, template_id=None):
    return (
        clean_text(fee.get("feeTemplateFeeID"))
        or "|".join(
            str(value or "")
            for value in (
                template_id,
                clean_text(fee.get("feeID")),
                clean_text(fee.get("feeName")),
            )
        )
    )


def walk_templates(node, as_of, depth=0, path=None):
    path = list(path or [])
    template_metadata = {
        key: value
        for key, value in node.items()
        if key
        not in {
            "feeTemplateID",
            "feeTemplateName",
            "fees",
            "allFees",
            "childTemplates",
        }
    }
    current = {
        "templateId": clean_text(node.get("feeTemplateID")),
        "templateName": clean_text(node.get("feeTemplateName")),
        "metadataAndRules": prune(template_metadata),
    }
    path.append(prune(current))

    rows = []
    for fee in node.get("fees") or []:
        if isinstance(fee, dict):
            rows.append(
                normalize_fee(fee, node, depth, path, as_of)
            )

    for child in node.get("childTemplates") or []:
        if isinstance(child, dict):
            rows.extend(
                walk_templates(child, as_of, depth + 1, path)
            )
    return rows


def add_all_fees_orphans(root, rows, as_of):
    """Preserve allFees entries missing from the traversable template tree."""
    known = {assignment_key(fee) for fee in rows}
    path = [
        prune(
            {
                "templateId": root.get("feeTemplateID"),
                "templateName": root.get("feeTemplateName"),
            }
        ),
        {"templateName": "(allFees only)"},
    ]
    added = 0
    for fee in root.get("allFees") or []:
        if not isinstance(fee, dict):
            continue
        key = raw_assignment_key(fee, root.get("feeTemplateID"))
        if key in known:
            continue
        normalized = normalize_fee(
            fee,
            root,
            1,
            path,
            as_of,
            relationship="AllFeesOnly",
        )
        rows.append(normalized)
        known.add(assignment_key(normalized))
        added += 1
    return added


def deduplicate_assignments(rows):
    unique = {}
    for row in rows:
        key = assignment_key(row)
        existing = unique.get(key)
        if existing is None:
            row["duplicateTemplateReferences"] = 0
            unique[key] = row
            continue

        existing["duplicateTemplateReferences"] += 1
        for path in row.get("templatePaths") or []:
            if path not in existing["templatePaths"]:
                existing["templatePaths"].append(path)
        for warning in row.get("warnings") or []:
            if warning not in existing.setdefault("warnings", []):
                existing["warnings"].append(warning)

    return list(unique.values())


def required_inputs(fees):
    combined = {}
    for fee in fees:
        if not fee.get("selectable"):
            continue
        calculation = fee.get("calculation") or {}
        for item in calculation.get("inputs") or []:
            if not item.get("variable"):
                continue
            key = (
                item.get("inputKey")
                or item.get("feeInputId")
                or item.get("label")
                or "(unnamed input)"
            )
            entry = combined.setdefault(
                str(key),
                {
                    "inputKey": item.get("inputKey"),
                    "label": item.get("label"),
                    "source": item.get("source"),
                    "inputTypeId": item.get("inputTypeId"),
                    "inputType": item.get("inputType"),
                    "calculationInputType": item.get(
                        "calculationInputType"
                    ),
                    "feeTemplateFeeIds": [],
                    "feeNames": [],
                },
            )
            fee_id = fee.get("feeTemplateFeeId")
            fee_name = fee.get("name")
            if fee_id and fee_id not in entry["feeTemplateFeeIds"]:
                entry["feeTemplateFeeIds"].append(fee_id)
            if fee_name and fee_name not in entry["feeNames"]:
                entry["feeNames"].append(fee_name)
    return [prune(combined[key]) for key in sorted(combined)]


def required_rule_types(fees):
    rules = set()
    for fee in fees:
        mode = fee.get("estimateMode")
        if mode:
            rules.add(mode)
        applicability = fee.get("applicability") or {}
        if applicability.get("hasCondition"):
            rules.add("condition-evaluation")
        advanced = (fee.get("calculation") or {}).get("advancedRules") or {}
        if any(
            advanced.get(name)
            for name in (
                "discounts",
                "feeProrationInputs",
                "prorateSchedules",
                "lateFeeCalculations",
                "templateDiscounts",
            )
        ):
            rules.add("advanced-fee-rules")
    return sorted(rules)


def collect_vocabulary(value, vocabulary):
    if isinstance(value, dict):
        field_map = {
            "expressionType": "expressionTypes",
            "variableType": "variableTypes",
            "returnType": "returnTypes",
            "operandTypeID": "operandTypeIds",
            "operandDataTypeID": "operandDataTypeIds",
            "operatorID": "operatorIds",
        }
        for key, target in field_map.items():
            if value.get(key) is not None:
                vocabulary[target].add(value[key])
        for item in value.values():
            collect_vocabulary(item, vocabulary)
    elif isinstance(value, list):
        for item in value:
            collect_vocabulary(item, vocabulary)


def choice_context(layout):
    if not isinstance(layout, dict):
        return {"layoutId": None, "fields": []}
    if not isinstance(layout.get("customFields"), list):
        return {"layoutId": clean_text(layout.get("customFieldLayoutID")), "fields": []}
    fields = []
    for field in layout["customFields"]:
        key = clean_text(field.get("fieldName"))
        if not key or not field.get("isPickList"):
            continue
        fields.append({
            "key": key,
            "fieldId": clean_text(field.get("customFieldID")),
            "label": clean_text(field.get("label")) or key,
            "active": field.get("isActive") is True,
            "multiple": field.get("allowMultipleSelections") is not False,
            "choices": [
                {"value": item["customFieldPickListItemID"], "label": item.get("value")}
                for item in field.get("pickListItems") or []
                if item.get("retire") is False
                and clean_text(item.get("customFieldPickListItemID"))
            ],
        })
    return {"layoutId": clean_text(layout.get("customFieldLayoutID")), "fields": fields}


def clean_record(record, as_of, vocabulary):
    template = record.get("Template") or {}
    root = template.get("feeTemplate")
    option = {
        "key": "|".join(
            str(record.get(name) or "")
            for name in ("PermitTypeID", "WorkClassID")
        ),
        "permitTypeId": clean_text(record.get("PermitTypeID")),
        "permitType": clean_text(record.get("PermitType")),
        "workClassId": clean_text(record.get("WorkClassID")),
        "workClass": clean_text(record.get("WorkClass")),
        "hasFeeTemplate": isinstance(root, dict) and bool(root),
        "fees": [],
        "requiredInputs": [],
        "requiredRuleTypes": [],
        "choiceContext": choice_context(template.get("customFieldsLayout")),
    }
    if not option["hasFeeTemplate"]:
        option["summary"] = {
            "configuredFeeReferences": 0,
            "distinctFeeAssignments": 0,
        }
        option["warnings"] = [
            "No fee template was returned for this permit type/work class."
        ]
        return option

    option["feeTemplateId"] = clean_text(root.get("feeTemplateID"))
    option["feeTemplate"] = clean_text(root.get("feeTemplateName"))
    rows = walk_templates(root, as_of)
    references = len(rows)
    orphan_count = add_all_fees_orphans(root, rows, as_of)
    fees = deduplicate_assignments(rows)
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

    classifications = Counter(fee.get("classification") for fee in fees)
    modes = Counter(fee.get("estimateMode") for fee in fees)
    option["fees"] = fees
    option["requiredInputs"] = required_inputs(fees)
    option["requiredRuleTypes"] = required_rule_types(fees)
    option["summary"] = {
        "configuredFeeReferences": references,
        "allFeesOnlyAssignments": orphan_count,
        "distinctFeeAssignments": len(fees),
        "selectableFees": sum(bool(fee.get("selectable")) for fee in fees),
        "currentlyAutoEstimableFees": sum(
            bool(fee.get("canAutoEstimate")) for fee in fees
        ),
        "classificationCounts": dict(sorted(classifications.items())),
        "estimateModeCounts": dict(sorted(modes.items())),
    }
    cleaned = prune(option)
    cleaned["choiceContext"] = option["choiceContext"]
    return cleaned


def iter_json_array(path, chunk_size=1024 * 1024):
    """Yield objects from a top-level JSON array without loading it all."""
    decoder = json.JSONDecoder()
    buffer = ""
    started = False

    with path.open("r", encoding="utf-8") as handle:
        while True:
            if not buffer:
                buffer = handle.read(chunk_size)
                if not buffer:
                    raise ValueError("Input JSON is empty.")

            buffer = buffer.lstrip()
            if not started:
                if not buffer.startswith("["):
                    raise ValueError("Input JSON must be a top-level array.")
                buffer = buffer[1:]
                started = True

            while True:
                buffer = buffer.lstrip()
                if buffer.startswith(","):
                    buffer = buffer[1:]
                    continue
                if buffer.startswith("]"):
                    return
                if buffer:
                    break
                chunk = handle.read(chunk_size)
                if not chunk:
                    raise ValueError("Input JSON ended before the array closed.")
                buffer += chunk

            while True:
                try:
                    value, end = decoder.raw_decode(buffer)
                    break
                except json.JSONDecodeError as exc:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        raise ValueError(
                            "Input JSON ended inside an array item."
                        ) from exc
                    buffer += chunk

            if not isinstance(value, dict):
                raise ValueError("Each top-level array item must be an object.")
            yield value
            buffer = buffer[end:]


def sorted_vocabulary(vocabulary):
    return {
        key: sorted(values, key=lambda value: str(value))
        for key, values in sorted(vocabulary.items())
        if values
    }


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
    options = []
    for number, record in enumerate(iter_json_array(input_path), start=1):
        options.append(clean_record(record, as_of, vocabulary))
        if number % 25 == 0:
            print(f"Cleaned {number:,} permit type/work class pairs...")

    options.sort(
        key=lambda option: (
            str(option.get("permitType") or ""),
            str(option.get("workClass") or ""),
        )
    )
    all_fees = [
        fee
        for option in options
        for fee in option.get("fees") or []
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
            "currentlyAutoEstimable": (
                "Active unconditional fixed-dollar fees, including fees from "
                "structurally included child templates."
            ),
            "futureEngineMust": [
                "Evaluate explicit fee/template conditions before including a fee.",
                "Evaluate every encountered EnerGov expression type.",
                "Resolve required inputs and apply per-unit calculations.",
                "Apply tier boundaries, additional amounts, and rounding.",
                "Apply any relevant discounts, proration, and late-fee rules.",
                "Reject an estimate when a required rule is unsupported.",
            ],
            "amountSemantics": (
                "Configured values are preserved; non-fixed calculation "
                "semantics must be implemented and validated against EnerGov."
            ),
        },
        "ruleVocabulary": sorted_vocabulary(vocabulary),
        "summary": {
            "permitTypeWorkClassPairs": len(options),
            "pairsWithFeeTemplate": sum(
                bool(option.get("hasFeeTemplate")) for option in options
            ),
            "distinctFeeAssignments": len(all_fees),
            "selectableFees": sum(
                bool(fee.get("selectable")) for fee in all_fees
            ),
            "currentlyAutoEstimableFees": sum(
                bool(fee.get("canAutoEstimate")) for fee in all_fees
            ),
            "classificationCounts": dict(sorted(classifications.items())),
            "estimateModeCounts": dict(sorted(modes.items())),
        },
        "permitOptions": options,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create normalized, web-app-oriented JSON from the raw "
            "EnerGov permit fee-template extract."
        )
    )
    parser.add_argument(
        "json_file",
        nargs="?",
        default=DEFAULT_INPUT,
        help=f"Raw template JSON (default: {DEFAULT_INPUT})",
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
    print()
    print(f"Permit type/work class pairs: {summary['permitTypeWorkClassPairs']:,}")
    print(f"Distinct fee assignments:     {summary['distinctFeeAssignments']:,}")
    print(f"Selectable fees:              {summary['selectableFees']:,}")
    print(
        "Currently auto-estimable:    "
        f"{summary['currentlyAutoEstimableFees']:,}"
    )
    print(f"Clean JSON: {output_path}")


if __name__ == "__main__":
    main()
