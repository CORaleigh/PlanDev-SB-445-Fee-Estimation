from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from input_fee_evaluator import (
    VALIDATED_CENT_ROUNDED_FEE_DEPENDENCIES,
    VALIDATED_FEE_BASED_TECHNOLOGY_COMPONENTS,
    VALIDATED_PERCENTAGE_INPUT_FEE_NAMES,
    VALIDATED_TOTAL_BASED_TECHNOLOGY_COMPONENTS,
)


SAFE = "safe-configuration-change"
COMPATIBILITY = "compatibility-review"
REVALIDATE = "requires-revalidation"
UNKNOWN = "new-unknown"

SEVERITY = {
    SAFE: 1,
    COMPATIBILITY: 2,
    REVALIDATE: 3,
    UNKNOWN: 4,
}

SAFE_CALCULATION_FIELDS = {
    "amount",
    "computationValue",
    "minimum",
    "maximum",
    "startDate",
    "endDate",
    "tiers",
}
IGNORED_CALCULATION_FIELDS = {
    "feeSetupId",
}
KNOWN_INPUT_SOURCES = {
    "constant",
    "custom-field",
    "fee",
    "system",
    "total",
}
STRUCTURAL_FEE_FIELDS = {
    "feeId",
    "classification",
    "estimateMode",
    "selectable",
    "canAutoEstimate",
    "requiresInput",
    "sourceTemplateId",
    "templateRelationship",
}
REVALIDATION_FLAG_FIELDS = {
    "active",
    "manual",
    "disabled",
    "cpi",
    "prorated",
    "prorationByDate",
}
COMPATIBILITY_FLAG_FIELDS = {
    "hidden",
    "online",
    "tax",
    "accountsReceivable",
    "timeTracking",
    "reinspection",
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fee_key(fee):
    return str(
        fee.get("feeTemplateFeeId")
        or "|".join(
            str(fee.get(name) or "")
            for name in ("sourceTemplateId", "feeId", "name")
        )
    )


def fallback_fee_key(fee):
    return tuple(
        str(fee.get(name) or "")
        for name in ("feeId", "name", "sourceTemplateId")
    )


def option_index(document):
    return {
        str(option.get("key") or ""): option
        for option in document.get("permitOptions") or []
        if option.get("key")
    }


def fee_index(option):
    return {
        fee_key(fee): fee
        for fee in option.get("fees") or []
    }


def validated_assignment_ids():
    result = set()
    for mapping in (
        VALIDATED_TOTAL_BASED_TECHNOLOGY_COMPONENTS,
        VALIDATED_FEE_BASED_TECHNOLOGY_COMPONENTS,
        VALIDATED_CENT_ROUNDED_FEE_DEPENDENCIES,
    ):
        for consumer_id, dependency_ids in mapping.items():
            result.add(str(consumer_id))
            result.update(str(value) for value in dependency_ids)
    return result


VALIDATED_ASSIGNMENT_IDS = validated_assignment_ids()


def normalized_inputs(fee):
    calculation = fee.get("calculation") or {}
    result = []
    for item in calculation.get("inputs") or []:
        result.append(
            {
                "inputKey": item.get("inputKey"),
                "source": item.get("source"),
                "inputTypeId": item.get("inputTypeId"),
                "calculationInputType": item.get("calculationInputType"),
                "constant": item.get("constant"),
                "variable": item.get("variable"),
            }
        )
    return result


def compact_value(value):
    if isinstance(value, (dict, list)):
        return value
    return value


def add_change(changes, path, old, new):
    if old != new:
        changes.append(
            {
                "path": path,
                "old": compact_value(old),
                "new": compact_value(new),
            }
        )


def fee_changes(old_fee, new_fee):
    changes = []

    for field in sorted(STRUCTURAL_FEE_FIELDS):
        add_change(
            changes,
            field,
            old_fee.get(field),
            new_fee.get(field),
        )

    for field in ("name", "feeTypeId", "feeType", "order", "priority"):
        add_change(
            changes,
            field,
            old_fee.get(field),
            new_fee.get(field),
        )

    add_change(
        changes,
        "applicability",
        old_fee.get("applicability") or {},
        new_fee.get("applicability") or {},
    )

    old_flags = old_fee.get("flags") or {}
    new_flags = new_fee.get("flags") or {}
    for field in sorted(
        REVALIDATION_FLAG_FIELDS | COMPATIBILITY_FLAG_FIELDS
    ):
        add_change(
            changes,
            f"flags.{field}",
            old_flags.get(field),
            new_flags.get(field),
        )

    old_calc = old_fee.get("calculation") or {}
    new_calc = new_fee.get("calculation") or {}

    for field in sorted(SAFE_CALCULATION_FIELDS):
        add_change(
            changes,
            f"calculation.{field}",
            old_calc.get(field),
            new_calc.get(field),
        )

    for field in (
        "pricingStatus",
        "computationValueName",
        "roundingType",
        "roundingValue",
        "effectiveSetupCount",
        "expression",
        "advancedRules",
    ):
        add_change(
            changes,
            f"calculation.{field}",
            old_calc.get(field),
            new_calc.get(field),
        )

    add_change(
        changes,
        "calculation.inputs",
        normalized_inputs(old_fee),
        normalized_inputs(new_fee),
    )

    return changes


def changed_paths(changes):
    return {item["path"] for item in changes}


def inputs_use_known_sources(fee):
    for item in normalized_inputs(fee):
        if item.get("source") not in KNOWN_INPUT_SOURCES:
            return False
    return True


def classify_existing_fee(old_fee, new_fee, changes, *, same_as_of=False):
    if not changes:
        return None, None

    paths = changed_paths(changes)
    assignment_id = str(new_fee.get("feeTemplateFeeId") or "")
    old_assignment_id = str(old_fee.get("feeTemplateFeeId") or "")
    protected = (
        assignment_id in VALIDATED_ASSIGNMENT_IDS
        or old_assignment_id in VALIDATED_ASSIGNMENT_IDS
    )

    if old_assignment_id != assignment_id:
        return (
            REVALIDATE,
            "The fee assignment ID changed; validated behavior must not be "
            "transferred to a replacement assignment automatically.",
        )

    old_name = str(old_fee.get("name") or "").strip().casefold()
    new_name = str(new_fee.get("name") or "").strip().casefold()
    if "name" in paths and (
        protected
        or old_name in VALIDATED_PERCENTAGE_INPUT_FEE_NAMES
        or new_name in VALIDATED_PERCENTAGE_INPUT_FEE_NAMES
    ):
        return (
            REVALIDATE,
            "The fee name changed for an assignment whose calculation "
            "semantics depend on validated identity/name behavior.",
        )

    if "applicability" in paths:
        return (
            REVALIDATE,
            "Fee applicability or condition structure changed.",
        )

    if any(
        path in paths
        for path in {
            "feeId",
            "classification",
            "estimateMode",
            "selectable",
            "canAutoEstimate",
            "requiresInput",
            "sourceTemplateId",
            "templateRelationship",
            "calculation.pricingStatus",
            "calculation.computationValueName",
            "calculation.roundingType",
            "calculation.roundingValue",
            "calculation.effectiveSetupCount",
            "calculation.expression",
            "calculation.advancedRules",
        }
    ):
        return (
            REVALIDATE,
            "A calculation, applicability, or workflow semantic changed.",
        )

    if any(
        path == f"flags.{field}"
        for field in REVALIDATION_FLAG_FIELDS
        for path in paths
    ):
        return (
            REVALIDATE,
            "A fee activation/manual/proration semantic changed.",
        )

    if "calculation.inputs" in paths:
        if protected:
            return (
                REVALIDATE,
                "The input/dependency structure changed for an assignment "
                "with historically validated semantics.",
            )
        if not inputs_use_known_sources(new_fee):
            return (
                UNKNOWN,
                "The changed input structure contains an unrecognized source.",
            )
        return (
            COMPATIBILITY,
            "The input/dependency structure changed and must pass the "
            "supported-primitive compatibility checks.",
        )

    if any(
        path == f"flags.{field}"
        for field in COMPATIBILITY_FLAG_FIELDS
        for path in paths
    ) or bool(paths & {"name", "feeTypeId", "feeType", "order", "priority"}):
        return (
            COMPATIBILITY,
            "A non-pricing fee behavior/display flag changed.",
        )

    safe_paths = {
        f"calculation.{field}"
        for field in SAFE_CALCULATION_FIELDS
    }
    if paths <= safe_paths:
        if same_as_of:
            old_calc = old_fee.get("calculation") or {}
            new_calc = new_fee.get("calculation") or {}
            old_setup_id = old_calc.get("feeSetupId")
            new_setup_id = new_calc.get("feeSetupId")
            if old_setup_id and old_setup_id == new_setup_id:
                return (
                    REVALIDATE,
                    "Pricing values changed for the same effective fee setup "
                    "while the snapshot as-of date is unchanged. This is an "
                    "in-place source-configuration mutation, so historical "
                    "reproducibility and validated price fixtures must be "
                    "reviewed before rebuilding.",
                )
            return (
                REVALIDATE,
                "Pricing values changed while the snapshot as-of date is "
                "unchanged. Treat this as a retroactive source-configuration "
                "change and review historical reproducibility before "
                "rebuilding.",
            )
        return (
            SAFE,
            "Only supported pricing values or tier schedule values changed; "
            "rebuild the catalog and run regression tests.",
        )

    return (
        COMPATIBILITY,
        "The existing assignment changed outside the known pricing-only "
        "fields and needs compatibility review.",
    )


def change_record(
    classification,
    option,
    old_fee=None,
    new_fee=None,
    changes=None,
    reason=None,
):
    old_fee = old_fee or {}
    new_fee = new_fee or {}
    return {
        "classification": classification,
        "optionKey": option.get("key"),
        "permitType": option.get("permitType"),
        "workClass": option.get("workClass"),
        "oldFeeTemplateFeeId": old_fee.get("feeTemplateFeeId"),
        "newFeeTemplateFeeId": new_fee.get("feeTemplateFeeId"),
        "feeName": new_fee.get("name") or old_fee.get("name"),
        "reason": reason,
        "changes": list(changes or []),
    }


def compare_option(old_option, new_option, *, same_as_of=False):
    records = []
    if old_option.get("choiceContext") != new_option.get("choiceContext"):
        records.append(change_record(
            REVALIDATE,
            new_option,
            changes=[{
                "path": "choiceContext",
                "old": old_option.get("choiceContext"),
                "new": new_option.get("choiceContext"),
            }],
            reason="The selected form's choice fields or available values changed.",
        ))
    old_by_id = fee_index(old_option)
    new_by_id = fee_index(new_option)

    matched_old = set()
    matched_new = set()

    for key in sorted(set(old_by_id) & set(new_by_id)):
        old_fee = old_by_id[key]
        new_fee = new_by_id[key]
        changes = fee_changes(old_fee, new_fee)
        classification, reason = classify_existing_fee(
            old_fee,
            new_fee,
            changes,
            same_as_of=same_as_of,
        )
        if classification:
            records.append(
                change_record(
                    classification,
                    new_option,
                    old_fee,
                    new_fee,
                    changes,
                    reason,
                )
            )
        matched_old.add(key)
        matched_new.add(key)

    old_unmatched = [
        fee
        for key, fee in old_by_id.items()
        if key not in matched_old
    ]
    new_unmatched = [
        fee
        for key, fee in new_by_id.items()
        if key not in matched_new
    ]

    old_by_fallback = {}
    for fee in old_unmatched:
        old_by_fallback.setdefault(fallback_fee_key(fee), []).append(fee)
    new_by_fallback = {}
    for fee in new_unmatched:
        new_by_fallback.setdefault(fallback_fee_key(fee), []).append(fee)

    paired_old = set()
    paired_new = set()
    for key in sorted(set(old_by_fallback) & set(new_by_fallback)):
        old_matches = old_by_fallback[key]
        new_matches = new_by_fallback[key]
        if len(old_matches) != 1 or len(new_matches) != 1:
            continue
        old_fee = old_matches[0]
        new_fee = new_matches[0]
        changes = fee_changes(old_fee, new_fee)
        changes.insert(
            0,
            {
                "path": "feeTemplateFeeId",
                "old": old_fee.get("feeTemplateFeeId"),
                "new": new_fee.get("feeTemplateFeeId"),
            },
        )
        records.append(
            change_record(
                REVALIDATE,
                new_option,
                old_fee,
                new_fee,
                changes,
                "A likely replacement assignment has a new "
                "FeeTemplateFeeID.",
            )
        )
        paired_old.add(id(old_fee))
        paired_new.add(id(new_fee))

    for fee in old_unmatched:
        if id(fee) in paired_old:
            continue
        records.append(
            change_record(
                REVALIDATE,
                old_option,
                old_fee=fee,
                reason="A previously configured fee assignment disappeared.",
            )
        )

    for fee in new_unmatched:
        if id(fee) in paired_new:
            continue
        records.append(
            change_record(
                UNKNOWN,
                new_option,
                new_fee=fee,
                reason="A new fee assignment appeared and has no prior "
                "validated compatibility history.",
            )
        )

    return records


def compare_documents(old_document, new_document):
    records = []
    old_options = option_index(old_document)
    new_options = option_index(new_document)
    old_as_of = old_document.get("asOfDate")
    new_as_of = new_document.get("asOfDate")
    same_as_of = bool(old_as_of) and old_as_of == new_as_of

    for key in sorted(set(old_options) & set(new_options)):
        records.extend(
            compare_option(
                old_options[key],
                new_options[key],
                same_as_of=same_as_of,
            )
        )

    for key in sorted(set(old_options) - set(new_options)):
        option = old_options[key]
        records.append(
            {
                "classification": REVALIDATE,
                "optionKey": key,
                "permitType": option.get("permitType"),
                "workClass": option.get("workClass"),
                "oldFeeTemplateFeeId": None,
                "newFeeTemplateFeeId": None,
                "feeName": None,
                "reason": "A previously configured permit/work-class option "
                "disappeared.",
                "changes": [],
            }
        )

    for key in sorted(set(new_options) - set(old_options)):
        option = new_options[key]
        records.append(
            {
                "classification": UNKNOWN,
                "optionKey": key,
                "permitType": option.get("permitType"),
                "workClass": option.get("workClass"),
                "oldFeeTemplateFeeId": None,
                "newFeeTemplateFeeId": None,
                "feeName": None,
                "reason": "A new permit/work-class option appeared.",
                "changes": [],
            }
        )

    records.sort(
        key=lambda row: (
            -SEVERITY[row["classification"]],
            str(row.get("permitType") or ""),
            str(row.get("workClass") or ""),
            str(row.get("feeName") or ""),
        )
    )
    counts = Counter(row["classification"] for row in records)
    highest = (
        max(records, key=lambda row: SEVERITY[row["classification"]])[
            "classification"
        ]
        if records
        else None
    )

    return {
        "oldAsOfDate": old_as_of,
        "newAsOfDate": new_as_of,
        "summary": {
            "totalChanges": len(records),
            "counts": {
                key: counts.get(key, 0)
                for key in (SAFE, COMPATIBILITY, REVALIDATE, UNKNOWN)
            },
            "highestClassification": highest,
            "actionRequired": any(
                SEVERITY[row["classification"]] >= SEVERITY[COMPATIBILITY]
                for row in records
            ),
        },
        "changes": records,
    }


def print_summary(report):
    summary = report["summary"]
    counts = summary["counts"]
    print("FEE TEMPLATE CONFIGURATION CHANGE CHECK")
    print(
        f"Snapshot: {report.get('oldAsOfDate')} -> "
        f"{report.get('newAsOfDate')}"
    )
    print(f"Total material changes: {summary['totalChanges']}")
    print(f"  Safe configuration changes: {counts[SAFE]}")
    print(f"  Compatibility reviews:      {counts[COMPATIBILITY]}")
    print(f"  Revalidation required:      {counts[REVALIDATE]}")
    print(f"  New / unknown:              {counts[UNKNOWN]}")
    if summary["highestClassification"]:
        print(
            "Highest classification: "
            f"{summary['highestClassification']}"
        )
    else:
        print("Highest classification: none")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Compare two normalized EnerGov permit fee-template snapshots and "
            "flag configuration changes that could invalidate estimator "
            "assumptions."
        )
    )
    parser.add_argument("old_snapshot")
    parser.add_argument("new_snapshot")
    parser.add_argument(
        "--output",
        help="Optional JSON report destination.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Always exit zero even when review/revalidation is required.",
    )
    args = parser.parse_args()

    old_document = load_json(Path(args.old_snapshot))
    new_document = load_json(Path(args.new_snapshot))
    report = compare_documents(old_document, new_document)
    print_summary(report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        print(f"Report: {output_path}")

    if (
        report["summary"]["actionRequired"]
        and not args.report_only
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
