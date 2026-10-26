from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fee_assessment_roles import annotate_assessment_roles
from option_ui_schema import build_option_ui_schema


DEFAULT_PERMIT_TEMPLATES = "output-prod/permit_fee_templates_clean.json"
DEFAULT_INSPECTION_TEMPLATES = (
    "output/inspection-prod/inspection_fee_templates_clean.json"
)
DEFAULT_RULE_VALIDATION = (
    "output/inspection-prod/inspection_fee_rule_validation.json"
)
DEFAULT_USAGE = (
    "output-prod/permit_fee_usage_patterns_cohort_remapped.json"
)
DEFAULT_DISCLOSURES = "schema/disclosure-codes.json"
DEFAULT_OUTPUT_DIR = "output-prod/catalog"
ESTIMATION_REVIEW_PATH = Path(__file__).resolve().parents[1] / "config/estimation-review.json"

LOW_SAMPLE_THRESHOLD = 30


def load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_estimation_review(path):
    document = load_json(path)
    if document.get("schemaVersion") != "1.0" or not isinstance(document.get("fees"), dict):
        raise ValueError("Estimation review must use schema 1.0 and a fee-ID mapping.")
    reviews = {}
    for fee_id, reason in document["fees"].items():
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Each estimation review needs a nonblank reason.")
        reviews[str(UUID(fee_id))] = reason.strip()
    return document, reviews


def apply_estimation_review(document, reviews):
    groups = [document["fees"], *(r["fees"] for r in document.get("relatedPermits") or [])]
    for fees in groups:
        for fee in fees:
            reason = reviews.get(str(fee.get("feeId") or "").strip().lower())
            if not reason:
                continue
            fee["estimationReview"] = reason
            validation = fee["validation"]
            validation["status"] = "blocked"
            validation["codes"] = sorted(set(validation.get("codes") or []) | {"ESTIMATION_REVIEW_REQUIRED"})
            if reason not in validation["notes"]:
                validation["notes"].append(reason)
    document["validation"] = option_validation(document["fees"])


def canonical(value):
    return " ".join(str(value or "").split()).casefold()


def normalized_input_key(value):
    return str(value or "").strip()


def safe_file_key(option_key):
    value = re.sub(r"[^A-Za-z0-9._-]+", "--", str(option_key or "option"))
    return value.strip(".-") or "option"


def source_entry(kind, path, document):
    return {
        "kind": kind,
        "name": path.name,
        "schemaVersion": document.get("schemaVersion"),
        "asOfDate": document.get("asOfDate")
        or document.get("sourceAsOfDate"),
    }


def csv_source_entry(kind, path):
    # These CSV inputs do not record an extraction date.
    return source_entry(kind, path, {"schemaVersion": "1.0"})


def build_permit_indexes(document):
    by_name = {}
    for option in document.get("permitOptions") or []:
        key = (
            canonical(option.get("permitType")),
            canonical(option.get("workClass")),
        )
        if key in by_name:
            raise ValueError(
                "Duplicate normalized permit option: "
                f"{option.get('permitType')} / {option.get('workClass')}"
            )
        by_name[key] = option
    return by_name


def build_inspection_fee_index(document):
    result = {}
    for template in document.get("feeTemplates") or []:
        for fee in template.get("fees") or []:
            fee_id = fee.get("feeTemplateFeeId")
            if not fee_id:
                continue
            if fee_id in result:
                raise ValueError(
                    f"Duplicate inspection FeeTemplateFeeID {fee_id}."
                )
            result[fee_id] = {
                "fee": fee,
                "template": template,
            }
    return result


def build_usage_index(document):
    result = {}
    for parent in document.get("parentPermitPatterns") or []:
        key = (
            canonical(parent.get("permitType")),
            canonical(parent.get("workClass")),
        )
        if key in result:
            raise ValueError(
                "Duplicate historical parent option: "
                f"{parent.get('permitType')} / {parent.get('workClass')}"
            )
        result[key] = parent
    return result


def _choice_key(operand_value, class_name, comparison_value):
    return (
        canonical(operand_value),
        canonical(class_name),
        str(comparison_value or "").strip().casefold(),
    )


def build_choice_label_index(path):
    required = {
        "OperandValue",
        "ClassName",
        "ComparisonValue",
        "FriendlyLabel",
        "LabelSource",
    }
    exact = {}
    fallback_candidates = defaultdict(set)
    fallback_sources = defaultdict(set)

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(
                "Choice-label CSV is missing columns: " + ", ".join(missing)
            )
        for row in reader:
            operand = str(row.get("OperandValue") or "").strip()
            class_name = str(row.get("ClassName") or "").strip()
            comparison = str(row.get("ComparisonValue") or "").strip()
            label = str(row.get("FriendlyLabel") or "").strip()
            source = str(row.get("LabelSource") or "").strip()
            if not operand or not comparison or not label or not source:
                raise ValueError(
                    "Choice-label row has an empty required value."
                )

            key = _choice_key(operand, class_name, comparison)
            existing = exact.get(key)
            candidate = {"label": label, "source": source}
            if existing is not None and existing != candidate:
                raise ValueError(
                    "Conflicting choice-label row: "
                    + " | ".join((operand, class_name, comparison))
                )
            exact[key] = candidate

            fallback_key = (
                canonical(operand),
                str(comparison).strip().casefold(),
            )
            fallback_candidates[fallback_key].add(label)
            fallback_sources[fallback_key].add(source)

    fallback = {}
    for key, labels in fallback_candidates.items():
        if len(labels) != 1:
            continue
        sources = fallback_sources[key]
        fallback[key] = {
            "label": next(iter(labels)),
            "source": next(iter(sources)) if len(sources) == 1 else "multiple",
        }

    return {"exact": exact, "fallback": fallback}


def _condition_choice_label(condition, choice_label_index):
    expression = condition.get("compareExpression") or {}
    if "value" not in expression:
        return None

    operand = condition.get("operandValue")
    comparison = expression.get("value")
    class_name = condition.get("className")
    exact_key = _choice_key(operand, class_name, comparison)
    match = choice_label_index["exact"].get(exact_key)
    if match is not None:
        return match

    fallback_key = (
        canonical(operand),
        str(comparison or "").strip().casefold(),
    )
    return choice_label_index["fallback"].get(fallback_key)


def enrich_condition_rule_labels(rule, choice_label_index):
    if not isinstance(rule, dict) or not choice_label_index:
        return rule

    for condition in rule.get("childConditions") or []:
        if not isinstance(condition, dict):
            continue
        match = _condition_choice_label(condition, choice_label_index)
        if match is not None:
            expression = condition.get("compareExpression")
            if isinstance(expression, dict):
                expression["label"] = match["label"]
                expression["labelSource"] = match["source"]

    for child in rule.get("childConditionGroups") or []:
        enrich_condition_rule_labels(child, choice_label_index)
    return rule


def enrich_option_choice_labels(document, choice_label_index):
    if not choice_label_index:
        return 0

    enriched = 0
    fee_sets = [document.get("fees") or []]
    fee_sets.extend(
        relation.get("fees") or []
        for relation in document.get("relatedPermits") or []
    )
    for fees in fee_sets:
        for fee in fees:
            for condition_document in (
                (fee.get("applicability") or {}).get("conditions") or []
            ):
                rule = condition_document.get("sourceRule")
                if not isinstance(rule, dict):
                    continue
                before = json.dumps(rule, sort_keys=True)
                enrich_condition_rule_labels(rule, choice_label_index)
                after = json.dumps(rule, sort_keys=True)
                if after != before:
                    enriched += 1
    return enriched


def build_fixed_fee_behavior_index(path):
    required = {
        "OptionKey",
        "FeeTemplateFeeID",
        "BehaviorStatus",
        "RowsWithConfiguredBase",
        "FlatTotalRows",
        "IntegerMultiplierRows",
        "ZeroComputedRows",
        "OtherComputedVarianceRows",
        "DistinctObservedMultipliers",
    }
    result = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(
                "Fixed-fee behavior CSV is missing columns: "
                + ", ".join(missing)
            )
        for row in reader:
            key = (
                str(row.get("OptionKey") or ""),
                str(row.get("FeeTemplateFeeID") or ""),
            )
            if not all(key):
                raise ValueError("Fixed-fee behavior row has an empty key.")
            if key in result:
                raise ValueError(
                    "Duplicate fixed-fee behavior key: " + " | ".join(key)
                )
            result[key] = row
    return result


def apply_fixed_fee_behavior(fee, option_key, behavior_index):
    fee_id = str(fee.get("feeTemplateFeeId") or "")
    evidence = behavior_index.get((str(option_key or ""), fee_id))
    if evidence is None:
        return fee

    status = evidence["BehaviorStatus"]
    fee["sources"].append(f"fixed-fee-behavior:{status}")
    validation = fee["validation"]
    codes = set(validation.get("codes") or [])
    notes = list(validation.get("notes") or [])
    calculation = fee["calculation"]

    if status == "flat-total-supported":
        codes.add("FIXED_TOTAL_HISTORICALLY_SUPPORTED")
    elif status == "no-configured-base-evidence":
        codes.add("FIXED_TOTAL_CONFIGURATION_ONLY")
        notes.append(
            "Current EnerGov fixed amount has no matching historical "
            "assessment evidence in the analyzed extract."
        )
    elif status == "unit-multiplier-review":
        codes.add("FIXED_UNIT_MULTIPLIER_NOT_MAPPED")
        validation["status"] = "blocked"
        calculation["amountStatus"] = "unsupported"
        multipliers = evidence.get("DistinctObservedMultipliers") or "unknown"
        calculation["formulaDisplay"] = (
            f"Configured unit amount × quantity; observed multiplier(s): "
            f"{multipliers}. Quantity source is not mapped."
        )
        notes.append(
            "Historical computed amounts show a unit multiplier that is not "
            "represented by the normalized fee input."
        )
    elif status in {
        "calculation-behavior-review",
        "current-base-review",
        "schedule-date-review",
    }:
        codes.add("FIXED_CALCULATION_BEHAVIOR_REVIEW")
        validation["status"] = "blocked"
        calculation["amountStatus"] = "unsupported"
        notes.append(
            "Historical assessed-fee behavior does not consistently support "
            "automatic use of the configured fixed amount."
        )
    else:
        raise ValueError(f"Unknown fixed-fee behavior status: {status}")

    validation["codes"] = sorted(codes)
    validation["notes"] = notes
    return fee


DIRECT_INPUT_PREVALENCE_DENOMINATOR = "MATURE_ISSUED_OR_FINALIZED"


def _csv_int(value):
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Expected an integer count, got {text!r}.") from error
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"Expected an integer count, got {text!r}.")
    return int(number)


def _csv_float(value):
    text = str(value if value is not None else "").strip()
    return float(text) if text else None


def build_input_prevalence_index(paths):
    required = {
        "PermitType",
        "WorkClass",
        "DenominatorName",
        "PermitRows",
        "InputKey",
        "InputLabel",
        "SourceKind",
        "SourceColumn",
        "ValueKind",
        "PhysicalColumnFound",
        "PopulatedRows",
        "PopulatedPct",
        "ActiveRows",
        "ActivePct",
        "ActiveDefinition",
    }
    result = {}
    for path in paths or []:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                raise ValueError(
                    f"Input-prevalence CSV {path} is missing columns: "
                    + ", ".join(missing)
                )
            for row in reader:
                denominator_name = str(
                    row.get("DenominatorName") or ""
                ).strip()
                if denominator_name != DIRECT_INPUT_PREVALENCE_DENOMINATOR:
                    continue

                permit_type = str(row.get("PermitType") or "").strip()
                work_class = str(row.get("WorkClass") or "").strip()
                input_key = str(row.get("InputKey") or "").strip()
                if not permit_type or not work_class or not input_key:
                    raise ValueError(
                        f"Input-prevalence CSV {path} contains an empty "
                        "PermitType, WorkClass, or InputKey."
                    )

                key = (
                    canonical(permit_type),
                    canonical(work_class),
                    input_key,
                )
                if key in result:
                    raise ValueError(
                        "Duplicate direct input-prevalence key: "
                        f"{permit_type} | {work_class} | {input_key}"
                    )

                physical_found = (
                    str(row.get("PhysicalColumnFound") or "").strip()
                    in {"1", "true", "True", "TRUE"}
                )
                populated_pct = _csv_float(row.get("PopulatedPct"))
                active_pct = _csv_float(row.get("ActivePct"))
                denominator = _csv_int(row.get("PermitRows"))
                populated_rows = _csv_int(row.get("PopulatedRows"))
                active_rows = _csv_int(row.get("ActiveRows"))
                for count in (denominator, populated_rows, active_rows):
                    if count is not None and (count < 0 or (denominator is not None and count > denominator)):
                        raise ValueError(f"Invalid field-history count for {permit_type} | {work_class} | {input_key}.")
                for percent in (populated_pct, active_pct):
                    if percent is not None and (not math.isfinite(percent) or not 0 <= percent <= 100):
                        raise ValueError(f"Invalid field-history percentage for {permit_type} | {work_class} | {input_key}.")

                result[key] = {
                    "inputKey": input_key,
                    "inputLabel": str(
                        row.get("InputLabel") or input_key
                    ).strip(),
                    "status": (
                        "available"
                        if physical_found
                        and denominator is not None
                        and denominator > 0
                        and active_pct is not None
                        else "unavailable"
                    ),
                    "denominatorName": denominator_name,
                    "denominator": denominator,
                    "populatedRows": populated_rows,
                    "populatedRate": (
                        populated_pct / 100.0
                        if populated_pct is not None else None
                    ),
                    "activeRows": active_rows,
                    "activeRate": (
                        active_pct / 100.0
                        if active_pct is not None else None
                    ),
                    "activeDefinition": str(
                        row.get("ActiveDefinition") or ""
                    ).strip(),
                    "valueKind": str(row.get("ValueKind") or "").strip(),
                    "sourceKind": str(row.get("SourceKind") or "").strip(),
                    "sourceColumn": str(
                        row.get("SourceColumn") or ""
                    ).strip(),
                    "physicalColumnFound": physical_found,
                    "basis": "direct-input-prevalence-query",
                    "sourceFile": path.name,
                }
    return result


def option_input_prevalence(option, prevalence_index):
    prefix = (
        canonical(option.get("permitType")),
        canonical(option.get("workClass")),
    )
    rows = [
        evidence
        for (permit_type, work_class, _input_key), evidence
        in prevalence_index.items()
        if (permit_type, work_class) == prefix
    ]
    return sorted(rows, key=lambda item: str(item.get("inputKey") or ""))


def validate_input_prevalence_coverage(document, prevalence_index):
    option = document["option"]
    prefix = (canonical(option["permitType"]), canonical(option["workClass"]))
    controls = [control for control in build_option_ui_schema(document)["controls"]
                if control.get("displayReady") and control.get("controlType") in {"number", "boolean", "select"}]
    missing = [control["key"] for control in controls if (*prefix, control["key"]) not in prevalence_index]
    if missing:
        raise ValueError(
            f"Missing field-history rows for {option['permitType']} / {option['workClass']}: "
            f"{len(missing)} fields ({', '.join(missing[:3])}). Rerun the all-option field-history query."
        )
    kinds = {"number": "numeric", "boolean": "boolean", "select": "choice"}
    for control in controls:
        if prevalence_index[(*prefix, control["key"])].get("valueKind") != kinds[control["controlType"]]:
            raise ValueError(f"Field-history type changed for {option['permitType']} / {option['workClass']} / {control['key']}.")
    return len(controls)



DIRECT_FEE_ASSESSMENT_DENOMINATOR = "MATURE_ISSUED_OR_FINALIZED"

VALIDATED_OPTION_DEPENDENCY_ONLY = {
    (
        canonical("DSINSP - Electrical (Non-Residential)"),
        canonical("Sub Permit"),
        "7728caad-06b5-438f-88b3-e637cdec2b97",
    ): "validated historical direct-assessment audit",
}


def build_fee_assessment_evidence_index(paths):
    required = {
        "PermitType",
        "WorkClass",
        "DenominatorName",
        "MaturePermitRows",
        "FeeTemplateFeeID",
        "ActiveDirectFeeRows",
    }
    result = {}
    for path in paths or []:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                raise ValueError(
                    f"Fee-assessment evidence CSV {path} is missing columns: "
                    + ", ".join(missing)
                )
            for row in reader:
                denominator_name = str(
                    row.get("DenominatorName") or ""
                ).strip()
                if denominator_name != DIRECT_FEE_ASSESSMENT_DENOMINATOR:
                    continue

                permit_type = str(row.get("PermitType") or "").strip()
                work_class = str(row.get("WorkClass") or "").strip()
                fee_id = str(row.get("FeeTemplateFeeID") or "").strip()
                if not permit_type or not work_class or not fee_id:
                    raise ValueError(
                        f"Fee-assessment evidence CSV {path} contains an "
                        "empty PermitType, WorkClass, or FeeTemplateFeeID."
                    )

                denominator = _csv_int(row.get("MaturePermitRows"))
                direct_rows = _csv_int(row.get("ActiveDirectFeeRows"))
                if denominator is None or direct_rows is None:
                    raise ValueError(
                        f"Fee-assessment evidence CSV {path} contains a "
                        "blank MaturePermitRows or ActiveDirectFeeRows value."
                    )
                if denominator < 0 or direct_rows < 0:
                    raise ValueError(
                        f"Fee-assessment evidence CSV {path} contains a "
                        "negative count."
                    )
                if denominator == 0 and direct_rows > 0:
                    raise ValueError(
                        f"Fee-assessment evidence CSV {path} contains direct "
                        "fee rows with a zero mature-permit denominator."
                    )

                key = (
                    canonical(permit_type),
                    canonical(work_class),
                    fee_id,
                )
                if key in result:
                    raise ValueError(
                        "Duplicate fee-assessment evidence key: "
                        f"{permit_type} | {work_class} | {fee_id}"
                    )

                result[key] = {
                    "feeTemplateFeeId": fee_id,
                    "historicalDenominator": denominator,
                    "activeDirectFeeRows": direct_rows,
                    "casesDirectlyCharged": (
                        _csv_int(row.get("CasesDirectlyCharged")) or 0
                    ),
                    "positiveAmountRows": (
                        _csv_int(row.get("PositiveAmountRows")) or 0
                    ),
                    "denominatorName": denominator_name,
                    "basis": "direct-fee-assessment-query",
                    "sourceFile": path.name,
                }
    return result


def option_fee_assessment_evidence(option, evidence_index):
    prefix = (
        canonical((option or {}).get("permitType")),
        canonical((option or {}).get("workClass")),
    )
    result = {
        fee_id: dict(evidence)
        for (permit_type, work_class, fee_id), evidence
        in (evidence_index or {}).items()
        if (permit_type, work_class) == prefix
    }

    for (
        permit_type,
        work_class,
        fee_id,
    ), basis in VALIDATED_OPTION_DEPENDENCY_ONLY.items():
        if (permit_type, work_class) != prefix:
            continue
        entry = result.setdefault(fee_id, {})
        entry["validatedDependencyOnlyBasis"] = basis

    return result


def history_by_exact_id(fees, category):
    result = {}
    for fee in fees or []:
        match = fee.get("configurationMatch") or {}
        if match.get("matchCategory") != category:
            continue
        fee_id = match.get("feeTemplateFeeId") or fee.get(
            "feeTemplateFeeId"
        )
        if fee_id:
            result[fee_id] = fee
    return result


def period_from_usage(usage):
    source = usage.get("source") or {}
    return {
        "basis": "ParentAppliedDate",
        "startDate": source.get("historicalStartDate"),
        "endDate": source.get("historicalEndDate"),
    }


def unavailable_occurrence():
    return {"status": "unavailable"}


def fee_occurrence(history, period, denominator, related=False):
    if not history or not denominator:
        return unavailable_occurrence()
    overall = history.get("overall") or {}
    numerator = int(overall.get("parentFamiliesCharged") or 0)
    rate_key = (
        "shareOfFamiliesWithRelatedPermit"
        if related
        else "shareOfEligibleParentFamilies"
    )
    rate = overall.get(rate_key)
    if rate is None:
        rate = numerator / denominator
    low_sample = denominator < LOW_SAMPLE_THRESHOLD
    return {
        "status": (
            "insufficient-sample" if low_sample else "available"
        ),
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(float(rate), 6),
        "period": period,
        "denominatorDefinition": (
            "Eligible parent families containing this related permit"
            if related
            else "All eligible parent families for this permit option"
        ),
        "sampleWarning": (
            f"Only {denominator} eligible families are in the historical "
            "denominator."
            if low_sample
            else None
        ),
    }


def related_occurrence(relation, period, parent_denominator):
    if not relation or not parent_denominator:
        return unavailable_occurrence()
    overall = relation.get("overall") or {}
    numerator = int(overall.get("parentFamiliesWithRelatedPermit") or 0)
    rate = overall.get("shareOfEligibleParentFamilies")
    if rate is None:
        rate = numerator / parent_denominator
    low_sample = parent_denominator < LOW_SAMPLE_THRESHOLD
    return {
        "status": (
            "insufficient-sample" if low_sample else "available"
        ),
        "numerator": numerator,
        "denominator": parent_denominator,
        "rate": round(float(rate), 6),
        "period": period,
        "denominatorDefinition": (
            "All eligible parent families for the selected permit option"
        ),
        "sampleWarning": (
            f"Only {parent_denominator} eligible families are in the "
            "historical denominator."
            if low_sample
            else None
        ),
    }


def related_active_fee_occurrence(relation, period, parent_denominator):
    if not relation or not parent_denominator:
        return unavailable_occurrence()
    overall = relation.get("overall") or {}
    numerator_value = overall.get(
        "parentFamiliesWithRelatedPermitAndActiveFee"
    )
    rate_value = overall.get(
        "shareOfEligibleParentFamiliesWithRelatedPermitAndActiveFee"
    )
    if numerator_value is None and rate_value is None:
        return unavailable_occurrence()
    numerator = int(numerator_value or 0)
    rate = (
        float(rate_value)
        if rate_value is not None
        else numerator / parent_denominator
    )
    low_sample = parent_denominator < LOW_SAMPLE_THRESHOLD
    return {
        "status": (
            "insufficient-sample" if low_sample else "available"
        ),
        "numerator": numerator,
        "denominator": parent_denominator,
        "rate": round(rate, 6),
        "period": period,
        "denominatorDefinition": (
            "All eligible parent families for the selected permit option; "
            "numerator requires this related permit and at least one active fee"
        ),
        "sampleWarning": (
            f"Only {parent_denominator} eligible families are in the "
            "historical denominator."
            if low_sample
            else None
        ),
    }


def input_document(item, used_by_fee_keys=None):
    source = item.get("source")
    if source not in ("custom-field", "fee", "system"):
        source = "unknown"
    entry_mode = {
        "custom-field": "user",
        "fee": "derived",
        "system": "system",
        "unknown": "review",
    }[source]
    if used_by_fee_keys is None:
        used_by = [
            value
            for value in item.get("feeTemplateFeeIds") or []
            if value
        ]
    else:
        used_by = sorted(set(used_by_fee_keys))
    return {
        "key": normalized_input_key(
            item.get("inputKey") or item.get("label") or "unknown"
        ),
        "label": str(item.get("label") or item.get("inputKey") or "Input"),
        "valueType": "unknown",
        "source": source,
        "entryMode": entry_mode,
        "unit": None,
        "required": True,
        "usedByFeeKeys": used_by,
    }


def required_input_documents(fees, configured_inputs=None):
    """Return a complete, fee-scoped input registry for published fees."""
    configured_by_key = {}
    for item in configured_inputs or []:
        key = normalized_input_key(
            item.get("inputKey") or item.get("key")
        )
        if key:
            configured_by_key.setdefault(key, item)

    embedded_by_key = {}
    used_by_key = defaultdict(set)
    for fee in fees:
        calculation = fee.get("calculation") or {}
        rule = calculation.get("rule") or {}
        for item in rule.get("inputs") or []:
            key = normalized_input_key(
                item.get("inputKey") or item.get("key")
            )
            if key:
                embedded_by_key.setdefault(key, item)
        for key in calculation.get("inputKeys") or []:
            key = normalized_input_key(key)
            if key:
                used_by_key[key].add(fee["feeKey"])

    output = []
    for key in sorted(used_by_key):
        source = configured_by_key.get(key) or embedded_by_key.get(key)
        if source is None:
            source = {
                "inputKey": key,
                "label": key,
                "source": "unknown",
            }
        output.append(
            input_document(
                source,
                used_by_fee_keys=used_by_key[key],
            )
        )
    return output


def condition_documents(fee, source_kind, inspection_types=None):
    result = []
    applicability = fee.get("applicability") or {}
    fee_condition = applicability.get("feeCondition")
    template_condition = applicability.get("templateCondition")
    template_expression = applicability.get("templateExpression")

    if fee_condition:
        result.append(
            {
                "kind": "expression",
                "scope": "fee",
                "description": "EnerGov fee condition must be evaluated.",
                "supported": False,
                "sourceRule": fee_condition,
            }
        )

    if template_condition:
        result.append(
            {
                "kind": "expression",
                "scope": "template",
                "description": (
                    "EnerGov source-template condition must be evaluated."
                ),
                "supported": False,
                "sourceRule": template_condition,
            }
        )

    if template_expression:
        result.append(
            {
                "kind": "template-expression",
                "scope": "template",
                "description": (
                    "EnerGov source-template expression affects applicability "
                    "and is not yet supported."
                ),
                "supported": False,
                "sourceRule": template_expression,
            }
        )

    if (
        applicability.get("hasCondition")
        and not fee_condition
        and not template_condition
        and not template_expression
    ):
        result.append(
            {
                "kind": "unresolved-condition",
                "description": (
                    "EnerGov marks this fee/template as conditional, but no "
                    "normalized source rule was preserved."
                ),
                "supported": False,
            }
        )

    if source_kind == "inspection-template":
        labels = ", ".join(inspection_types or [])
        result.append(
            {
                "kind": "inspection",
                "description": (
                    "Fee is contingent on an inspection event"
                    + (f": {labels}" if labels else ".")
                ),
                "supported": False,
            }
        )
    return result


def calculation_document(fee):
    estimate_mode = fee.get("estimateMode")
    source_calculation = fee.get("calculation") or {}
    calculation = dict(source_calculation)
    calculation["inputs"] = [
        {
            **item,
            "inputKey": normalized_input_key(item.get("inputKey")),
        }
        for item in source_calculation.get("inputs") or []
    ]
    mode = {
        "fixed": "fixed",
        "conditional-fixed": "fixed",
        "input-rule-review": "input",
        "formula-review": "formula",
        "tier-rule-review": "tiered",
        "manual-review": "manual",
        "unavailable": "unsupported",
    }.get(estimate_mode, "unsupported")
    if fee.get("canAutoEstimate") or fee.get("canAutoCalculateAmount"):
        amount_status = "calculable"
    elif mode == "input":
        amount_status = "needs-input"
    elif mode == "manual":
        amount_status = "manual-review"
    else:
        amount_status = "unsupported"

    amount = calculation.get("amount")
    fixed_amount = amount if mode == "fixed" else None
    input_keys = [
        normalized_input_key(item.get("inputKey"))
        for item in calculation.get("inputs") or []
        if normalized_input_key(item.get("inputKey"))
    ]
    if mode == "input":
        formula_display = (
            f"{amount:g} × input" if isinstance(amount, (int, float)) else None
        )
    elif mode == "tiered":
        formula_display = "Tiered EnerGov schedule; staff review required"
    elif mode == "formula":
        formula_display = "EnerGov expression; evaluator not yet implemented"
    elif mode == "manual":
        formula_display = "Amount determined during staff review"
    else:
        formula_display = None

    rounding_type = calculation.get("roundingType")
    rounding_value = calculation.get("roundingValue")
    rounding = (
        f"EnerGov rounding type {rounding_type}, value {rounding_value}"
        if rounding_type is not None
        else None
    )
    return {
        "mode": mode,
        "amountStatus": amount_status,
        "currency": "USD",
        "fixedAmount": fixed_amount,
        "formulaDisplay": formula_display,
        "rounding": rounding,
        "minimumAmount": calculation.get("minimum"),
        "maximumAmount": calculation.get("maximum"),
        "inputKeys": input_keys,
        "rule": calculation,
    }


def fee_section(fee, source_kind):
    mode = fee.get("estimateMode")
    if not fee.get("selectable", True) or mode == "unavailable":
        return "inactive"
    if source_kind == "inspection-template":
        return "event-contingent"
    if mode == "manual-review":
        return "manual-review"
    applicability = fee.get("applicability") or {}
    if applicability.get("hasCondition"):
        return "conditional"
    if fee.get("requiresInput"):
        return "input-required"
    return "configured"


def applicability_document(fee, section, conditions):
    status = {
        "inactive": "inactive",
        "event-contingent": "event-contingent",
        "manual-review": "manual-review",
        "conditional": "conditional",
    }.get(section, "configured")
    explanations = {
        "inactive": "Fee is disabled, inactive, or lacks a current setup.",
        "event-contingent": (
            "Fee applies only if the associated inspection event occurs."
        ),
        "manual-review": (
            "EnerGov marks this fee as manual or its amount requires review."
        ),
        "conditional": (
            "Fee applicability depends on an explicit EnerGov condition."
        ),
        "input-required": (
            "Fee is configured, but its amount requires project input."
        ),
        "configured": (
            "Fee is configured directly for this permit option."
        ),
    }
    return {
        "status": status,
        "explanation": explanations[section],
        "conditions": conditions,
    }


def fee_validation(fee, section, calculation):
    warnings = list(fee.get("warnings") or [])
    codes = []
    if section == "inactive":
        status = "unvalidated"
        codes.append("INACTIVE_CONFIGURATION")
    elif calculation["amountStatus"] == "calculable" and not warnings:
        status = "partially-validated"
        codes.append("CONFIGURATION_NORMALIZED")
    elif section == "event-contingent":
        status = "partially-validated"
        codes.append("EVENT_TRIGGER_NOT_IMPLEMENTED")
    elif calculation["amountStatus"] == "unsupported":
        status = "blocked"
        codes.append("CALCULATION_NOT_IMPLEMENTED")
    else:
        status = "unvalidated"
        codes.append("STAFF_OR_RULE_REVIEW_REQUIRED")
    if warnings:
        codes.append("SOURCE_WARNINGS_PRESENT")
    return {
        "status": status,
        "codes": sorted(set(codes)),
        "notes": warnings,
    }


def repeat_document(fee, history, source_kind):
    overall = (history or {}).get("overall") or {}
    average = overall.get("averageInstancesPerChargedFamily")
    name = canonical(fee.get("name"))
    if "reinspection" in name:
        repeatable = True
        basis = "event"
        trigger = "Additional reinspection event"
    elif average is not None and float(average) > 1:
        repeatable = True
        basis = "historical"
        trigger = "Historical records contain repeat instances"
    else:
        repeatable = None
        basis = "unknown"
        trigger = None
    return {
        "repeatable": repeatable,
        "basis": basis,
        "trigger": trigger,
        "averageInstancesPerChargedFamily": average,
    }


def transform_fee(
    fee,
    history,
    period,
    denominator,
    source_kind,
    related=False,
    inspection_types=None,
):
    fee_id = (
        fee.get("feeTemplateFeeId")
        or fee.get("feeId")
        or hashlib.sha256(
            str(fee.get("name") or "fee").encode("utf-8")
        ).hexdigest()[:16]
    )
    conditions = condition_documents(
        fee,
        source_kind,
        inspection_types=inspection_types,
    )
    section = fee_section(fee, source_kind)
    calculation = calculation_document(fee)
    sources = [
        f"{source_kind}:{fee.get('sourceTemplateId') or 'unknown'}",
        f"fee-template-assignment:{fee_id}",
    ]
    if history:
        sources.append(
            f"historical-fee-pattern:{history.get('feeTemplateFeeId')}"
        )
    result = {
        "feeKey": f"{source_kind}:{fee_id}",
        "feeTemplateFeeId": fee.get("feeTemplateFeeId"),
        "feeId": fee.get("feeId"),
        "name": str(fee.get("name") or "Unnamed fee"),
        "description": fee.get("description"),
        "feeTypeId": fee.get("feeTypeId"),
        "feeType": fee.get("feeType"),
        "sourceTemplateId": fee.get("sourceTemplateId"),
        "sourceTemplate": fee.get("sourceTemplate"),
        "templateRelationship": fee.get("templateRelationship"),
        "section": section,
        "applicability": applicability_document(
            fee,
            section,
            conditions,
        ),
        "calculation": calculation,
        "occurrence": fee_occurrence(
            history,
            period,
            denominator,
            related=related,
        ),
        "repeat": repeat_document(fee, history, source_kind),
        "sources": sources,
        "validation": fee_validation(fee, section, calculation),
    }
    return result


def option_validation(fees):
    rank = {
        "validated": 0,
        "partially-validated": 1,
        "unvalidated": 2,
        "blocked": 3,
    }
    statuses = [
        fee["validation"]["status"]
        for fee in fees
    ]
    status = max(statuses, key=rank.get) if statuses else "unvalidated"
    counts = Counter(statuses)
    codes = [f"{key.upper().replace('-', '_')}={value}" for key, value in sorted(counts.items())]
    return {
        "status": status,
        "codes": codes,
        "notes": [
            "Option status reflects its least-supported published fee rule."
        ],
    }


def add_inspection_history_fees(
    output,
    seen_ids,
    historical_fees,
    inspection_index,
    period,
    denominator,
    related,
):
    for history in historical_fees or []:
        match = history.get("configurationMatch") or {}
        if match.get("matchCategory") != "exact-inspection-id":
            continue
        fee_id = match.get("feeTemplateFeeId")
        if not fee_id or fee_id in seen_ids:
            continue
        configured = inspection_index.get(fee_id)
        if configured is None:
            raise ValueError(
                f"Exact inspection match {fee_id} is absent from the "
                "inspection template document."
            )
        output.append(
            transform_fee(
                configured["fee"],
                history,
                period,
                denominator,
                "inspection-template",
                related=related,
                inspection_types=match.get("inspectionTypes") or [],
            )
        )
        seen_ids.add(fee_id)


def build_fee_list(
    option,
    historical_fees,
    inspection_index,
    fixed_behavior_index,
    period,
    denominator,
    related=False,
    assessment_evidence=None,
):
    permit_history = history_by_exact_id(historical_fees, "exact-id")
    output = []
    seen_ids = set()
    for fee in (option or {}).get("fees") or []:
        fee_id = fee.get("feeTemplateFeeId")
        output.append(
            apply_fixed_fee_behavior(
                transform_fee(
                    fee,
                    permit_history.get(fee_id),
                    period,
                    denominator,
                    "permit-template",
                    related=related,
                ),
                (option or {}).get("key"),
                fixed_behavior_index,
            )
        )
        if fee_id:
            seen_ids.add(fee_id)
    add_inspection_history_fees(
        output,
        seen_ids,
        historical_fees,
        inspection_index,
        period,
        denominator,
        related,
    )
    annotate_assessment_roles(
        output,
        historical_denominator=denominator,
        assessment_evidence=assessment_evidence,
    )
    output.sort(
        key=lambda item: (
            item["section"],
            item["name"],
            item["feeKey"],
        )
    )
    return output


def build_observed_related_fee_list(
    option,
    historical_fees,
    inspection_index,
    fixed_behavior_index,
    period,
    denominator,
    assessment_evidence=None,
):
    configured_fees = {
        fee.get("feeTemplateFeeId"): fee
        for fee in (option or {}).get("fees") or []
        if fee.get("feeTemplateFeeId")
    }
    output = []
    seen_ids = set()
    for history in historical_fees or []:
        match = history.get("configurationMatch") or {}
        if match.get("matchCategory") != "exact-id":
            continue
        fee_id = match.get("feeTemplateFeeId")
        fee = configured_fees.get(fee_id)
        if fee is None:
            raise ValueError(
                f"Exact permit match {fee_id} is absent from the related "
                "permit option."
            )
        output.append(
            apply_fixed_fee_behavior(
                transform_fee(
                    fee,
                    history,
                    period,
                    denominator,
                    "permit-template",
                    related=True,
                ),
                (option or {}).get("key"),
                fixed_behavior_index,
            )
        )
        seen_ids.add(fee_id)
    add_inspection_history_fees(
        output,
        seen_ids,
        historical_fees,
        inspection_index,
        period,
        denominator,
        True,
    )
    annotate_assessment_roles(
        output,
        historical_denominator=denominator,
        assessment_evidence=assessment_evidence,
    )
    output.sort(
        key=lambda item: (
            item["section"],
            item["name"],
            item["feeKey"],
        )
    )
    return output


def related_key(relation):
    value = "|".join(
        str(relation.get(name) or "")
        for name in ("relationshipFamily", "permitType", "workClass")
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def build_related_permit(
    relation,
    permit_index,
    inspection_index,
    fixed_behavior_index,
    period,
    parent_denominator,
    assessment_evidence_index=None,
):
    option = permit_index.get(
        (
            canonical(relation.get("permitType")),
            canonical(relation.get("workClass")),
        )
    )
    relation_denominator = int(
        (relation.get("overall") or {}).get(
            "parentFamiliesWithRelatedPermit"
        )
        or 0
    )
    fees = build_observed_related_fee_list(
        option,
        relation.get("fees") or [],
        inspection_index,
        fixed_behavior_index,
        period,
        relation_denominator,
        assessment_evidence=option_fee_assessment_evidence(
            option,
            assessment_evidence_index,
        ),
    )
    required_inputs = required_input_documents(
        fees,
        configured_inputs=(option or {}).get("requiredInputs"),
    )
    configured_option_key = (option or {}).get("key")
    return {
        "relatedPermitKey": related_key(relation),
        "relationshipFamily": str(relation.get("relationshipFamily")),
        "permitTypeId": (option or {}).get("permitTypeId"),
        "permitType": str(relation.get("permitType")),
        "workClassId": (option or {}).get("workClassId"),
        "workClass": str(relation.get("workClass")),
        "configuredOptionKey": configured_option_key,
        "configuredOptionPath": (
            f"options/{safe_file_key(configured_option_key)}.json"
            if configured_option_key
            else None
        ),
        "evidence": (
            "configured-and-historical" if option else "historical"
        ),
        "occurrence": related_occurrence(
            relation,
            period,
            parent_denominator,
        ),
        "activeFeeOccurrence": related_active_fee_occurrence(
            relation,
            period,
            parent_denominator,
        ),
        "requiredInputs": required_inputs,
        "fees": fees,
        "disclosureCodes": [
            "HISTORICAL_OCCURRENCE_NOT_REQUIREMENT",
            "RELATED_PERMIT_SCOPE_DEPENDENT",
            "RELATED_PERMIT_REVIEW_DETERMINED",
        ],
    }


def disclosure_index(document):
    return {
        item["code"]: item
        for item in document.get("codes") or []
    }


def select_disclosures(index, codes):
    missing = sorted(set(codes) - set(index))
    if missing:
        raise ValueError(
            "Disclosure registry is missing: " + ", ".join(missing)
        )
    return [index[code] for code in sorted(set(codes))]


def build_option_document(
    option,
    history,
    permit_index,
    inspection_index,
    fixed_behavior_index,
    disclosure_lookup,
    configuration,
    catalog_version,
    generated_at,
    assessment_evidence_index=None,
):
    period = configuration["historicalPeriod"]
    parent_denominator = int(
        ((history or {}).get("observed") or {}).get("parentFamilies") or 0
    )
    historical_fees = (history or {}).get("parentFees") or []
    fees = build_fee_list(
        option,
        historical_fees,
        inspection_index,
        fixed_behavior_index,
        period,
        parent_denominator,
        assessment_evidence=option_fee_assessment_evidence(
            option,
            assessment_evidence_index,
        ),
    )
    related = [
        build_related_permit(
            relation,
            permit_index,
            inspection_index,
            fixed_behavior_index,
            period,
            parent_denominator,
            assessment_evidence_index=assessment_evidence_index,
        )
        for relation in (history or {}).get("relatedPermitPatterns") or []
    ]
    related.sort(
        key=lambda item: (
            item["relationshipFamily"],
            item["permitType"],
            item["workClass"],
        )
    )

    disclosure_codes = {
        "ENERGOV_SOURCE_OF_TRUTH",
        "ESTIMATE_NOT_INVOICE",
        "CONFIGURATION_AS_OF_DATE",
    }
    if history:
        disclosure_codes.add("HISTORICAL_OCCURRENCE_NOT_REQUIREMENT")
    if parent_denominator and parent_denominator < LOW_SAMPLE_THRESHOLD:
        disclosure_codes.add("LOW_SAMPLE")
    if related:
        disclosure_codes.update(
            {
                "RELATED_PERMIT_SCOPE_DEPENDENT",
                "RELATED_PERMIT_REVIEW_DETERMINED",
            }
        )
    for fee in fees:
        section = fee["section"]
        amount_status = fee["calculation"]["amountStatus"]
        if section == "event-contingent":
            disclosure_codes.add("EVENT_CONTINGENT")
        if section == "manual-review":
            disclosure_codes.add("MANUAL_REVIEW")
        if amount_status == "needs-input":
            disclosure_codes.add("INPUT_REQUIRED")
        if amount_status == "unsupported":
            disclosure_codes.add("UNSUPPORTED_RULE")
        if fee["repeat"]["repeatable"]:
            disclosure_codes.add("REPEATABLE_FEE")

    required_inputs = required_input_documents(
        fees,
        configured_inputs=option.get("requiredInputs"),
    )
    return {
        "schemaVersion": "1.0",
        "catalogVersion": catalog_version,
        "generatedAtUtc": generated_at,
        "configuration": configuration,
        "option": {
            "optionKey": str(option.get("key")),
            "permitTypeId": str(option.get("permitTypeId")),
            "permitType": str(option.get("permitType")),
            "workClassId": str(option.get("workClassId")),
            "workClass": str(option.get("workClass")),
            "feeTemplateId": option.get("feeTemplateId"),
            "feeTemplate": option.get("feeTemplate"),
        },
        "requiredInputs": required_inputs,
        "fees": fees,
        "relatedPermits": related,
        **({"choiceContext": option["choiceContext"]} if "choiceContext" in option else {}),
        "disclosures": select_disclosures(
            disclosure_lookup,
            disclosure_codes,
        ),
        "validation": option_validation(fees),
    }


def json_dump(path, document, pretty):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            document,
            handle,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        handle.write("\n")


def build_catalog(
    permit_path,
    inspection_path,
    validation_path,
    usage_path,
    fixed_behavior_path,
    disclosure_path,
    output_dir,
    catalog_version=None,
    pretty=False,
    choice_labels_path=None,
    input_prevalence_paths=None,
    assessment_evidence_paths=None,
    require_input_prevalence=False,
):
    review_document, reviews = load_estimation_review(ESTIMATION_REVIEW_PATH)
    permit_document = load_json(permit_path)
    inspection_document = load_json(inspection_path)
    validation_document = load_json(validation_path)
    usage_document = load_json(usage_path)
    fixed_behavior_index = build_fixed_fee_behavior_index(
        fixed_behavior_path
    )
    disclosures_document = load_json(disclosure_path)
    choice_label_index = (
        build_choice_label_index(choice_labels_path)
        if choice_labels_path is not None
        else None
    )
    input_prevalence_paths = list(input_prevalence_paths or [])
    input_prevalence_index = build_input_prevalence_index(
        input_prevalence_paths
    )
    assessment_evidence_paths = list(assessment_evidence_paths or [])
    assessment_evidence_index = build_fee_assessment_evidence_index(
        assessment_evidence_paths
    )

    if not str(usage_document.get("schemaVersion") or "").startswith("2."):
        raise ValueError(
            "Historical usage must use the all-parent schema version 2.x."
        )
    permit_index = build_permit_indexes(permit_document)
    inspection_index = build_inspection_fee_index(inspection_document)
    usage_index = build_usage_index(usage_document)
    disclosure_lookup = disclosure_index(disclosures_document)

    as_of_date = permit_document.get("asOfDate")
    if not as_of_date:
        raise ValueError("Permit template document has no asOfDate.")
    catalog_version = str(catalog_version or as_of_date)
    generated_at = datetime.now(timezone.utc).isoformat()
    period = period_from_usage(usage_document)
    if not period["startDate"] or not period["endDate"]:
        raise ValueError("Historical usage has no complete observation period.")

    configuration = {
        "environment": "prod",
        "asOfDate": as_of_date,
        "sourceAuthority": "EnerGov",
        "sources": [
            {
                **source_entry("staff-review", ESTIMATION_REVIEW_PATH, review_document),
                "sha256": hashlib.sha256(ESTIMATION_REVIEW_PATH.read_bytes()).hexdigest(),
            },
            source_entry("permit-template", permit_path, permit_document),
            source_entry(
                "inspection-template",
                inspection_path,
                inspection_document,
            ),
            source_entry(
                "historical-family-cohort",
                usage_path,
                usage_document,
            ),
            source_entry(
                "validated-rule",
                validation_path,
                validation_document,
            ),
            csv_source_entry("fixed-fee-behavior", fixed_behavior_path),
            *(
                [
                    csv_source_entry("condition-choice-labels", choice_labels_path)
                ]
                if choice_labels_path is not None
                else []
            ),
            *[
                csv_source_entry("direct-input-prevalence", path)
                for path in input_prevalence_paths
            ],
            *[
                csv_source_entry("direct-fee-assessment-evidence", path)
                for path in assessment_evidence_paths
            ],
        ],
        "historicalPeriod": period,
    }

    staging_dir = output_dir.with_name(output_dir.name + ".tmp")
    if output_dir.exists():
        raise FileExistsError(
            f"Output directory already exists: {output_dir}"
        )
    if staging_dir.exists():
        raise FileExistsError(
            f"Temporary output directory already exists: {staging_dir}"
        )
    options_dir = staging_dir / "options"
    options_dir.mkdir(parents=True)

    manifest_options = []
    summary = Counter()
    validation_counts = Counter()
    sorted_options = sorted(
        permit_document.get("permitOptions") or [],
        key=lambda item: (
            str(item.get("permitType") or ""),
            str(item.get("workClass") or ""),
            str(item.get("key") or ""),
        ),
    )
    for option in sorted_options:
        usage_key = (
            canonical(option.get("permitType")),
            canonical(option.get("workClass")),
        )
        history = usage_index.get(usage_key)
        option_document = build_option_document(
            option,
            history,
            permit_index,
            inspection_index,
            fixed_behavior_index,
            disclosure_lookup,
            configuration,
            catalog_version,
            generated_at,
            assessment_evidence_index=assessment_evidence_index,
        )
        apply_estimation_review(option_document, reviews)
        summary["choiceLabelDocumentsEnriched"] += (
            enrich_option_choice_labels(
                option_document,
                choice_label_index,
            )
        )
        option_document["inputPrevalence"] = option_input_prevalence(
            option,
            input_prevalence_index,
        )
        if require_input_prevalence:
            summary["inputPrevalenceControlsChecked"] += validate_input_prevalence_coverage(
                option_document, input_prevalence_index,
            )
        summary["inputPrevalenceEntries"] += len(
            option_document["inputPrevalence"]
        )
        filename = safe_file_key(option.get("key")) + ".json"
        relative_path = f"options/{filename}"
        json_dump(options_dir / filename, option_document, pretty)

        validation_status = option_document["validation"]["status"]
        validation_counts[validation_status] += 1
        direct_fees = option_document["fees"]
        related_fee_entries = [
            fee
            for relation in option_document["relatedPermits"]
            for fee in relation["fees"]
        ]
        summary["options"] += 1
        summary["directFeeEntries"] += len(direct_fees)
        summary["relatedFeeEntries"] += len(related_fee_entries)
        summary["fees"] += len(direct_fees) + len(related_fee_entries)
        summary["relatedPermits"] += len(
            option_document["relatedPermits"]
        )
        summary["requiredInputs"] += len(
            option_document["requiredInputs"]
        )
        summary["relatedRequiredInputs"] += sum(
            len(relation["requiredInputs"])
            for relation in option_document["relatedPermits"]
        )
        summary["optionsWithHistory"] += int(history is not None)
        summary["eventContingentFees"] += sum(
            fee["section"] == "event-contingent"
            for fee in direct_fees + related_fee_entries
        )
        manifest_options.append(
            {
                "optionKey": option_document["option"]["optionKey"],
                "permitType": option_document["option"]["permitType"],
                "workClass": option_document["option"]["workClass"],
                "path": relative_path,
                "validationStatus": validation_status,
            }
        )

    summary["feeAssessmentEvidenceEntries"] = len(
        assessment_evidence_index
    )

    manifest = {
        "schemaVersion": "1.0",
        "catalogVersion": catalog_version,
        "generatedAtUtc": generated_at,
        "configurationAsOfDate": as_of_date,
        "sourceAuthority": "EnerGov",
        "disclosureCodesPath": "disclosure-codes.json",
        "options": manifest_options,
    }
    json_dump(staging_dir / "manifest.json", manifest, pretty)
    json_dump(
        staging_dir / "disclosure-codes.json",
        disclosures_document,
        pretty,
    )
    staging_dir.replace(output_dir)
    return {
        **dict(summary),
        "fixedFeeBehaviorStatuses": dict(sorted(Counter(
            row["BehaviorStatus"]
            for row in fixed_behavior_index.values()
        ).items())),
        "validationStatuses": dict(sorted(validation_counts.items())),
        "outputDirectory": str(output_dir),
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build compact estimator option files and a manifest from "
            "normalized EnerGov configuration and cohort-based history."
        )
    )
    parser.add_argument(
        "--permit-templates",
        default=DEFAULT_PERMIT_TEMPLATES,
    )
    parser.add_argument(
        "--inspection-templates",
        default=DEFAULT_INSPECTION_TEMPLATES,
    )
    parser.add_argument(
        "--rule-validation",
        default=DEFAULT_RULE_VALIDATION,
    )
    parser.add_argument("--usage-json", default=DEFAULT_USAGE)
    parser.add_argument("--fixed-fee-behavior", required=True)
    parser.add_argument("--disclosures", default=DEFAULT_DISCLOSURES)
    parser.add_argument(
        "--choice-labels",
        help=(
            "Optional CSV from Query 116 containing authoritative condition "
            "GUID labels. Unmapped values remain raw and UI-blocked."
        ),
    )
    parser.add_argument(
        "--input-prevalence",
        action="append",
        default=[],
        help=(
            "Import-ready Result 2 CSV from "
            "build_option_input_prevalence_query.py. May be repeated. "
            "Only direct field prevalence can drive staff-facing percentages."
        ),
    )
    parser.add_argument(
        "--require-input-prevalence",
        action="store_true",
        help="Require a field-history row for every supported display-ready project field, including unavailable rows.",
    )
    parser.add_argument(
        "--assessment-evidence",
        action="append",
        default=[],
        help=(
            "Import-ready Result 2 CSV from "
            "build_fee_assessment_evidence_query.py. May be repeated. "
            "Used only to classify exact fee assignments as assessed, "
            "dependency-only, or unresolved."
        ),
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--catalog-version")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Write indented JSON instead of compact JSON.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    input_paths = {
        "permit templates": Path(args.permit_templates),
        "inspection templates": Path(args.inspection_templates),
        "rule validation": Path(args.rule_validation),
        "historical usage": Path(args.usage_json),
        "fixed-fee behavior": Path(args.fixed_fee_behavior),
        "disclosures": Path(args.disclosures),
    }
    if args.choice_labels:
        input_paths["choice labels"] = Path(args.choice_labels)
    input_prevalence_paths = [
        Path(value) for value in args.input_prevalence
    ]
    assessment_evidence_paths = [
        Path(value) for value in args.assessment_evidence
    ]
    for index, path in enumerate(input_prevalence_paths, start=1):
        input_paths[f"input prevalence {index}"] = path
    for index, path in enumerate(assessment_evidence_paths, start=1):
        input_paths[f"assessment evidence {index}"] = path
    for label, path in input_paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Could not find {label}: {path}")

    summary = build_catalog(
        permit_path=input_paths["permit templates"],
        inspection_path=input_paths["inspection templates"],
        validation_path=input_paths["rule validation"],
        usage_path=input_paths["historical usage"],
        fixed_behavior_path=input_paths["fixed-fee behavior"],
        disclosure_path=input_paths["disclosures"],
        output_dir=Path(args.output_dir),
        catalog_version=args.catalog_version,
        pretty=args.pretty,
        choice_labels_path=input_paths.get("choice labels"),
        input_prevalence_paths=input_prevalence_paths,
        assessment_evidence_paths=assessment_evidence_paths,
        require_input_prevalence=args.require_input_prevalence,
    )
    print(f"Permit option files:       {summary['options']:,}")
    print(f"Options with history:      {summary['optionsWithHistory']:,}")
    print(f"Published fee entries:     {summary['fees']:,}")
    print(f"  Direct option entries:   {summary['directFeeEntries']:,}")
    print(f"  Related-permit entries:  {summary['relatedFeeEntries']:,}")
    print(f"Event-contingent fees:     {summary['eventContingentFees']:,}")
    print(f"Related-permit patterns:   {summary['relatedPermits']:,}")
    print(f"Required-input entries:    {summary['requiredInputs']:,}")
    print(
        "Related input entries:     "
        f"{summary['relatedRequiredInputs']:,}"
    )
    print(
        "Fixed-fee behavior:       "
        + json.dumps(summary["fixedFeeBehaviorStatuses"], sort_keys=True)
    )
    print(
        "Validation statuses:      "
        + json.dumps(summary["validationStatuses"], sort_keys=True)
    )
    print(
        "Choice-label documents:   "
        f"{summary.get('choiceLabelDocumentsEnriched', 0):,}"
    )
    print(
        "Direct input prevalence:  "
        f"{summary.get('inputPrevalenceEntries', 0):,}"
    )
    print(
        "Fee assessment evidence: "
        f"{summary.get('feeAssessmentEvidenceEntries', 0):,}"
    )
    print(f"Catalog directory:         {summary['outputDirectory']}")


if __name__ == "__main__":
    main()
