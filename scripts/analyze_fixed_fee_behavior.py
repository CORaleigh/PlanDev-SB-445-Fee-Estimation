"""Classify whether configured fixed fees behave as flat totals or unit rates."""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from build_fixed_fee_fixtures import (
    TOLERANCE,
    amounts_equal,
    canonical,
    decimal_value,
    is_true,
    parse_datetime,
    ready_fixed_entries,
)


REQUIRED_COLUMNS = {
    "ParentPermitType",
    "ParentWorkClass",
    "FeeOwnerRole",
    "FeeOwnerPermitID",
    "FeeOwnerPermitNumber",
    "FeeOwnerAppliedDate",
    "FeeOwnerEPLSummaryURL",
    "FeeTemplateFeeID",
    "ComputedFeeID",
    "FeeStatusName",
    "IsManuallyAdded",
    "IsProcessed",
    "InputValue",
    "DisplayInputValue",
    "BaseAmount",
    "ComputedAmount",
    "FeeCreatedOn",
    "FeeLastChangedOn",
    "HasComputedFeeXref",
    "ComputedFeeXrefRole",
}

SUMMARY_FIELDS = [
    "CatalogVersion",
    "OptionKey",
    "PermitType",
    "WorkClass",
    "FeeKey",
    "FeeTemplateFeeID",
    "FeeName",
    "ConfiguredAmount",
    "BehaviorStatus",
    "AllMatchedRows",
    "ManualRows",
    "RowsWithConfiguredBase",
    "FlatTotalRows",
    "IntegerMultiplierRows",
    "ZeroComputedRows",
    "OtherComputedVarianceRows",
    "MissingAmountRows",
    "PriorScheduleCarryoverCandidateRows",
    "PriorScheduleComputedCarryoverCandidateRows",
    "CurrentApplicationBaseMismatchRows",
    "UnknownApplicationDateBaseMismatchRows",
    "DistinctObservedMultipliers",
]

SAMPLE_FIELDS = [
    "BehaviorClass",
    "CatalogVersion",
    "OptionKey",
    "PermitType",
    "WorkClass",
    "FeeKey",
    "FeeTemplateFeeID",
    "FeeName",
    "ConfiguredAmount",
    "ObservedMultiplier",
    "FeeOwnerPermitID",
    "FeeOwnerPermitNumber",
    "FeeOwnerAppliedDate",
    "FeeOwnerEPLSummaryURL",
    "ComputedFeeID",
    "FeeStatusName",
    "IsProcessed",
    "InputValue",
    "DisplayInputValue",
    "BaseAmount",
    "ComputedAmount",
    "FeeCreatedOn",
    "FeeLastChangedOn",
    "HasComputedFeeXref",
    "ComputedFeeXrefRole",
]


def computed_behavior(base_amount, computed_amount):
    if base_amount is None or computed_amount is None:
        return "missing-amount", None
    if amounts_equal(base_amount, computed_amount):
        return "flat-total", Decimal("1")
    if abs(computed_amount) <= TOLERANCE:
        return "zero-computed", Decimal("0")
    if abs(base_amount) > TOLERANCE:
        ratio = computed_amount / base_amount
        rounded = ratio.to_integral_value()
        if rounded >= 2 and abs(computed_amount - base_amount * rounded) <= TOLERANCE:
            return "integer-multiplier", rounded
    return "other-computed-variance", None


def retain_sample(entry, behavior, fee_created, sample, limit):
    if limit <= 0:
        return
    samples = entry["behaviorSamples"].setdefault(behavior, [])
    samples.append((fee_created or datetime.min, sample))
    samples.sort(key=lambda item: item[0], reverse=True)
    del samples[limit:]


def application_cohort(applied, start_date, end_date):
    if applied is None:
        return "unknown"
    applied_date = applied.date()
    if applied_date < start_date:
        return "prior"
    if applied_date <= end_date:
        return "current"
    return "future"


def behavior_status(counts):
    if counts["CurrentApplicationBaseMismatchRows"]:
        return "current-base-review"
    if counts["UnknownApplicationDateBaseMismatchRows"]:
        return "schedule-date-review"
    if counts["IntegerMultiplierRows"]:
        return "unit-multiplier-review"
    if (
        counts["ZeroComputedRows"]
        or counts["OtherComputedVarianceRows"]
        or counts["MissingAmountRows"]
    ):
        return "calculation-behavior-review"
    if counts["RowsWithConfiguredBase"]:
        return "flat-total-supported"
    if counts["PriorScheduleCarryoverCandidateRows"]:
        return "prior-schedule-only"
    return "no-configured-base-evidence"


def stream_history(path, entries, start_date, end_date, sample_limit, progress_every):
    rows_read = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
        if missing:
            raise ValueError("Historical CSV is missing columns: " + ", ".join(missing))
        for row in reader:
            rows_read += 1
            if progress_every and rows_read % progress_every == 0:
                print(f"Read {rows_read:,} historical rows...")
            if canonical(row.get("FeeOwnerRole")) != "parent":
                continue
            key = (
                canonical(row.get("ParentPermitType")),
                canonical(row.get("ParentWorkClass")),
                canonical(row.get("FeeTemplateFeeID")),
            )
            entry = entries.get(key)
            if entry is None:
                continue
            counts = entry["behaviorCounts"]
            counts["AllMatchedRows"] += 1
            if is_true(row.get("IsManuallyAdded")):
                counts["ManualRows"] += 1
                continue

            configured = entry["configuredAmount"]
            base_amount = decimal_value(row.get("BaseAmount"))
            computed_amount = decimal_value(row.get("ComputedAmount"))
            fee_created = parse_datetime(row.get("FeeCreatedOn"))
            applied = parse_datetime(row.get("FeeOwnerAppliedDate"))
            cohort = application_cohort(applied, start_date, end_date)
            in_observation_window = (
                fee_created is not None
                and start_date <= fee_created.date() <= end_date
            )

            if not amounts_equal(configured, base_amount):
                if in_observation_window and cohort == "prior":
                    behavior = "prior-schedule-carryover-candidate"
                    counts["PriorScheduleCarryoverCandidateRows"] += 1
                elif in_observation_window and cohort == "current":
                    behavior = "current-application-base-mismatch"
                    counts["CurrentApplicationBaseMismatchRows"] += 1
                elif in_observation_window and cohort == "unknown":
                    behavior = "unknown-application-date-base-mismatch"
                    counts["UnknownApplicationDateBaseMismatchRows"] += 1
                else:
                    continue
                multiplier = None
            else:
                counts["RowsWithConfiguredBase"] += 1
                behavior, multiplier = computed_behavior(base_amount, computed_amount)
                if (
                    cohort == "prior"
                    and behavior not in {"flat-total", "missing-amount"}
                ):
                    behavior = "prior-schedule-computed-carryover-candidate"
                    multiplier = None
                    counts["PriorScheduleComputedCarryoverCandidateRows"] += 1
                else:
                    field = {
                        "flat-total": "FlatTotalRows",
                        "integer-multiplier": "IntegerMultiplierRows",
                        "zero-computed": "ZeroComputedRows",
                        "other-computed-variance": "OtherComputedVarianceRows",
                        "missing-amount": "MissingAmountRows",
                    }[behavior]
                    counts[field] += 1
                    if behavior == "integer-multiplier":
                        entry["multipliers"].add(multiplier)

            if behavior == "flat-total":
                continue
            sample = {
                "BehaviorClass": behavior,
                "CatalogVersion": entry["manifest"].get("catalogVersion"),
                "OptionKey": entry["option"].get("optionKey"),
                "PermitType": entry["option"].get("permitType"),
                "WorkClass": entry["option"].get("workClass"),
                "FeeKey": entry["fee"].get("feeKey"),
                "FeeTemplateFeeID": entry["fee"].get("feeTemplateFeeId"),
                "FeeName": entry["fee"].get("name"),
                "ConfiguredAmount": str(configured),
                "ObservedMultiplier": str(multiplier) if multiplier is not None else None,
            }
            for field in SAMPLE_FIELDS[10:]:
                sample[field] = row.get(field)
            retain_sample(entry, behavior, fee_created, sample, sample_limit)
    return rows_read


def summary_row(entry):
    counts = entry["behaviorCounts"]
    row = {
        "CatalogVersion": entry["manifest"].get("catalogVersion"),
        "OptionKey": entry["option"].get("optionKey"),
        "PermitType": entry["option"].get("permitType"),
        "WorkClass": entry["option"].get("workClass"),
        "FeeKey": entry["fee"].get("feeKey"),
        "FeeTemplateFeeID": entry["fee"].get("feeTemplateFeeId"),
        "FeeName": entry["fee"].get("name"),
        "ConfiguredAmount": str(entry["configuredAmount"]),
        "BehaviorStatus": behavior_status(counts),
        "DistinctObservedMultipliers": ", ".join(
            str(value) for value in sorted(entry["multipliers"])
        ),
    }
    for field in SUMMARY_FIELDS[9:-1]:
        row[field] = counts[field]
    return row


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog_dir")
    parser.add_argument("historical_csv")
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--samples-per-class", type=int, default=3)
    parser.add_argument("--progress-every", type=int, default=100000)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    start_date = datetime.strptime(args.validation_start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.validation_end, "%Y-%m-%d").date()
    if end_date < start_date:
        raise ValueError("Validation end date precedes start date.")
    output_dir = Path(args.output_dir)
    paths = {
        "summary": output_dir / "fixed_fee_behavior_summary.csv",
        "cases": output_dir / "fixed_fee_behavior_cases.csv",
        "json": output_dir / "fixed_fee_behavior_summary.json",
    }
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError("Output files already exist; use --overwrite: " + ", ".join(existing))

    manifest, entries = ready_fixed_entries(Path(args.catalog_dir))
    for entry in entries.values():
        entry["behaviorCounts"] = Counter()
        entry["behaviorSamples"] = {}
        entry["multipliers"] = set()
    rows_read = stream_history(
        Path(args.historical_csv), entries, start_date, end_date,
        args.samples_per_class, args.progress_every,
    )
    summaries = sorted(
        (summary_row(entry) for entry in entries.values()),
        key=lambda row: (row["PermitType"], row["WorkClass"], row["FeeName"]),
    )
    cases = []
    for entry in entries.values():
        for samples in entry["behaviorSamples"].values():
            cases.extend(sample for _, sample in samples)
    cases.sort(key=lambda row: (
        row["BehaviorClass"], row["PermitType"], row["WorkClass"],
        row["FeeName"], row["FeeCreatedOn"] or "",
    ))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(paths["summary"], summaries, SUMMARY_FIELDS)
    write_csv(paths["cases"], cases, SAMPLE_FIELDS)
    statuses = Counter(row["BehaviorStatus"] for row in summaries)
    report = {
        "schemaVersion": "1.0",
        "catalogVersion": manifest.get("catalogVersion"),
        "validationStartDate": start_date.isoformat(),
        "validationEndDate": end_date.isoformat(),
        "historicalRowsRead": rows_read,
        "fixedConfigurations": len(entries),
        "behaviorStatuses": dict(sorted(statuses.items())),
        "notes": [
            "Configured-base behavior is evaluated across history; price-mismatched rows are only classified inside the validation observation window.",
            "Prior-schedule carryover is a candidate classification based on FeeOwnerAppliedDate and requires business-rule confirmation.",
            "Any multiplier, zero, or other computed variance keeps a configuration in review status.",
        ],
    }
    with paths["json"].open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    print(f"Historical rows read:       {rows_read:,}")
    print(f"Fixed configurations:       {len(entries):,}")
    print(f"Behavior statuses:          {dict(sorted(statuses.items()))}")
    print(f"Behavior summary:           {paths['summary']}")
    print(f"Behavior cases:             {paths['cases']}")
    print(f"Behavior JSON:              {paths['json']}")


if __name__ == "__main__":
    main()
