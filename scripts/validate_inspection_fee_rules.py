from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path


DEFAULT_INPUT = "output/inspection-prod/inspection_fee_templates_clean.json"
DEFAULT_STATUS_REFERENCE = "../data/12_inspection_fee_condition_statuses.csv"
DEFAULT_OUTPUT = "output/inspection-prod/inspection_fee_rule_validation.json"
DEFAULT_TECH_CSV = "output/inspection-prod/inspection_technology_fee_examples.csv"


def as_decimal(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))


def whole_dollar_ceiling(value):
    return value.quantize(Decimal("1"), rounding=ROUND_CEILING)


def load_status_reference(path):
    if not path or not path.exists():
        return {}
    result = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            status_id = row.get("ReferencedInspectionStatusID")
            if not status_id:
                continue
            resolved = str(row.get("Resolved") or "").strip() == "1"
            status_name = str(row.get("InspectionStatus") or "").strip()
            if status_name.casefold() in {"", "nan", "none"}:
                status_name = None
            result[status_id.casefold()] = {
                "statusId": status_id,
                "statusName": status_name,
                "resolved": resolved,
            }
    return result


def condition_predicates(group):
    if not isinstance(group, dict):
        return
    for predicate in group.get("childConditions") or []:
        if isinstance(predicate, dict):
            yield predicate
    for child in group.get("childConditionGroups") or []:
        yield from condition_predicates(child)


def fee_index(template):
    return {
        fee.get("feeTemplateFeeId"): fee
        for fee in template.get("fees") or []
        if fee.get("feeTemplateFeeId")
    }


def validate_technology_fee(template, fee):
    calculation = fee.get("calculation") or {}
    advanced = calculation.get("advancedRules") or {}
    inputs = calculation.get("inputs") or []
    fees = fee_index(template)
    rate_percent = as_decimal(calculation.get("amount"))
    references = []
    all_inputs_valid = bool(inputs)

    for item in inputs:
        source = fees.get(item.get("inputKey"))
        valid = (
            item.get("source") == "fee"
            and item.get("inputTypeId") == 3
            and source is not None
        )
        all_inputs_valid = all_inputs_valid and valid
        references.append(
            {
                "inputFeeTemplateFeeId": item.get("inputKey"),
                "inputLabel": item.get("label"),
                "resolvedInTemplate": source is not None,
                "resolvedFeeName": source.get("name") if source else None,
            }
        )

    matches_rule = (
        rate_percent == Decimal("4")
        and advanced.get("feeBased") is True
        and advanced.get("totalFeeBased") is False
        and calculation.get("roundingType") == 2
        and as_decimal(calculation.get("roundingValue")) == Decimal("1")
        and all_inputs_valid
    )
    return {
        "feeTemplate": template.get("feeTemplate"),
        "feeTemplateId": template.get("feeTemplateId"),
        "feeName": fee.get("name"),
        "feeTemplateFeeId": fee.get("feeTemplateFeeId"),
        "classification": fee.get("classification"),
        "selectable": bool(fee.get("selectable")),
        "ratePercent": float(rate_percent) if rate_percent is not None else None,
        "formula": "ceil(sum(referenced fee amounts) * 0.04, whole dollar)",
        "inputTypeId": 3,
        "inputMeaning": "referenced fee assignment amount",
        "roundingMeaning": "round up to the next whole dollar",
        "references": references,
        "matchesValidatedRule": matches_rule,
    }


def technology_examples(template, validation):
    fees = fee_index(template)
    rate = as_decimal(validation.get("ratePercent"))
    for reference in validation["references"]:
        source = fees.get(reference["inputFeeTemplateFeeId"])
        calculation = (source or {}).get("calculation") or {}
        base = as_decimal(calculation.get("amount"))
        surcharge = None
        total = None
        if base is not None and rate is not None:
            surcharge = whole_dollar_ceiling(base * rate / Decimal("100"))
            total = base + surcharge
        yield {
            "FeeTemplate": validation["feeTemplate"],
            "TechnologyFeeTemplateFeeID": validation["feeTemplateFeeId"],
            "ReferencedFeeTemplateFeeID": reference["inputFeeTemplateFeeId"],
            "ReferencedFeeName": reference["resolvedFeeName"],
            "ConfiguredExampleBaseAmount": str(base) if base is not None else "",
            "ExpectedTechnologyFee": (
                str(surcharge) if surcharge is not None else ""
            ),
            "ExpectedExampleTotal": str(total) if total is not None else "",
            "RuleValidated": validation["matchesValidatedRule"],
        }


def validate_condition(template, fee, predicate, statuses):
    expression = predicate.get("compareExpression") or {}
    value = str(expression.get("value") or "").strip()
    status = statuses.get(value.casefold()) if value else None
    structural_match = (
        predicate.get("operandValue") == "InspectionStatusID"
        and predicate.get("operatorID") == 1
        and expression.get("expressionType") == 7
        and expression.get("returnType") == 1
        and expression.get("variableType") == 1
        and bool(value)
    )
    return {
        "feeTemplate": template.get("feeTemplate"),
        "inspectionTypeNames": template.get("inspectionTypeNames") or [],
        "feeName": fee.get("name"),
        "feeTemplateFeeId": fee.get("feeTemplateFeeId"),
        "operand": predicate.get("operandValue"),
        "operatorId": predicate.get("operatorID"),
        "operatorMeaning": "equals",
        "expressionType": expression.get("expressionType"),
        "expressionMeaning": "identifier literal",
        "referencedStatusId": value or None,
        "referencedStatusName": status.get("statusName") if status else None,
        "statusResolved": bool(status and status.get("resolved")),
        "structureValidated": structural_match,
    }


def build_validation(document, statuses):
    technology = []
    examples = []
    conditions = []
    for template in document.get("feeTemplates") or []:
        for fee in template.get("fees") or []:
            if "technology" in str(fee.get("name") or "").casefold():
                result = validate_technology_fee(template, fee)
                technology.append(result)
                examples.extend(technology_examples(template, result))
            applicability = fee.get("applicability") or {}
            for key in ("feeCondition", "templateCondition"):
                for predicate in condition_predicates(applicability.get(key)):
                    conditions.append(
                        validate_condition(template, fee, predicate, statuses)
                    )

    active_technology = [item for item in technology if item["selectable"]]
    unresolved = [item for item in conditions if not item["statusResolved"]]
    summary = {
        "technologyFeeAssignments": len(technology),
        "activeTechnologyFeeAssignments": len(active_technology),
        "activeTechnologyRulesValidated": sum(
            item["matchesValidatedRule"] for item in active_technology
        ),
        "conditionPredicates": len(conditions),
        "conditionStructuresValidated": sum(
            item["structureValidated"] for item in conditions
        ),
        "conditionStatusesResolved": sum(
            item["statusResolved"] for item in conditions
        ),
        "conditionStatusesUnresolved": len(unresolved),
        "unresolvedTemplates": sorted(
            {item["feeTemplate"] for item in unresolved}
        ),
    }
    return {
        "schemaVersion": "1.0",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSchemaVersion": document.get("schemaVersion"),
        "sourceAsOfDate": document.get("asOfDate"),
        "validatedSemantics": {
            "feeInputType3": (
                "A fee-based input whose inputKey identifies another "
                "FeeTemplateFeeID in the same template."
            ),
            "expressionType7": (
                "A GUID identifier literal compared with InspectionStatusID; "
                "operator ID 1 is treated as equality."
            ),
            "technologySurcharge": (
                "Four percent of the referenced fee amounts, rounded up to "
                "the next whole dollar by EnerGov rounding type 2."
            ),
        },
        "evidence": {
            "developmentFeeGuide": (
                "https://cityofraleigh0drupal.blob.core.usgovcloudapi.net/"
                "drupal-prod/COR15/DevelopmentFeeGuide.pdf"
            ),
            "calculatorSource": (
                "../permit-calculators-react/src/Buildings/useBuildings.js"
            ),
            "statusReference": "queries/12_inspection_fee_condition_statuses.sql",
            "assessmentValidation": (
                "queries/13_technology_surcharge_formula_validation.sql"
            ),
        },
        "summary": summary,
        "technologyFees": technology,
        "conditionPredicates": conditions,
    }, examples


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if fieldnames:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Validate inspection status and technology-fee semantics."
    )
    parser.add_argument("json_file", nargs="?", default=DEFAULT_INPUT)
    parser.add_argument("--status-reference", default=DEFAULT_STATUS_REFERENCE)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--technology-csv", default=DEFAULT_TECH_CSV)
    args = parser.parse_args()

    input_path = Path(args.json_file)
    status_path = Path(args.status_reference) if args.status_reference else None
    output_path = Path(args.output)
    technology_path = Path(args.technology_csv)
    document = json.loads(input_path.read_text(encoding="utf-8"))
    statuses = load_status_reference(status_path)
    validation, examples = build_validation(document, statuses)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_csv(technology_path, examples)

    summary = validation["summary"]
    print(
        "Active technology rules:  "
        f"{summary['activeTechnologyRulesValidated']:,}/"
        f"{summary['activeTechnologyFeeAssignments']:,} validated"
    )
    print(
        "Condition structures:     "
        f"{summary['conditionStructuresValidated']:,}/"
        f"{summary['conditionPredicates']:,} validated"
    )
    print(
        "Condition status IDs:     "
        f"{summary['conditionStatusesResolved']:,}/"
        f"{summary['conditionPredicates']:,} resolved"
    )
    if summary["unresolvedTemplates"]:
        print("Unresolved template(s):   " + ", ".join(summary["unresolvedTemplates"]))
    print(f"Validation JSON:          {output_path}")
    print(f"Technology examples:      {technology_path}")


if __name__ == "__main__":
    main()
