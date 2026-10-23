from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


TOLERANCE = Decimal("0.01")
REQUIRED_HISTORY_COLUMNS = {
    "ParentPermitID",
    "ParentPermitNumber",
    "ParentPermitType",
    "ParentWorkClass",
    "ParentEPLSummaryURL",
    "FeeOwnerRole",
    "FeeOwnerPermitID",
    "FeeOwnerPermitNumber",
    "FeeOwnerPermitType",
    "FeeOwnerWorkClass",
    "FeeOwnerEPLSummaryURL",
    "ComputedFeeID",
    "FeeTemplateFeeID",
    "FeeName",
    "FeeStatusName",
    "IsManuallyAdded",
    "IsProcessed",
    "BaseAmount",
    "ComputedAmount",
    "CreditAmount",
    "CPIAmount",
    "FeeProrationAmount",
    "FeeProrationRate",
    "FeeCreatedOn",
    "FeeLastChangedOn",
}

SUMMARY_FIELDS = [
    "CatalogVersion",
    "ConfigurationAsOfDate",
    "OptionKey",
    "PermitType",
    "WorkClass",
    "FeeKey",
    "FeeTemplateFeeID",
    "FeeName",
    "ConfiguredAmount",
    "SetupStartDate",
    "SetupEndDate",
    "ValidationStartDate",
    "ValidationEndDate",
    "OccurrenceStatus",
    "OccurrenceNumerator",
    "OccurrenceDenominator",
    "OccurrenceRate",
    "HistoricalRowsInValidationPeriod",
    "DistinctCases",
    "StandardHistoricalRows",
    "ManualHistoricalRows",
    "ExactBaseRows",
    "ExactComputedRows",
    "ExactBaseAndComputedRows",
    "BaseMatchesComputedAdjustedRows",
    "BaseMismatchRows",
    "StandardExactBaseRows",
    "StandardExactComputedRows",
    "StandardExactBaseAndComputedRows",
    "StandardBaseMatchesComputedAdjustedRows",
    "StandardBaseMismatchRows",
    "StandardBaseMatchRate",
    "StandardComputedMatchRate",
    "LatestFeeCreatedOn",
    "EvidenceStatus",
]

SAMPLE_FIELDS = [
    "SampleClass",
    "CatalogVersion",
    "OptionKey",
    "PermitType",
    "WorkClass",
    "FeeKey",
    "FeeTemplateFeeID",
    "CatalogFeeName",
    "ConfiguredAmount",
    "SetupStartDate",
    "SetupEndDate",
    "ParentPermitID",
    "ParentPermitNumber",
    "ParentEPLSummaryURL",
    "FeeOwnerPermitID",
    "FeeOwnerPermitNumber",
    "FeeOwnerEPLSummaryURL",
    "ComputedFeeID",
    "HistoricalFeeName",
    "FeeStatusName",
    "FeeCreatedOn",
    "FeeLastChangedOn",
    "IsManuallyAdded",
    "IsProcessed",
    "BaseAmount",
    "ComputedAmount",
    "CreditAmount",
    "CPIAmount",
    "FeeProrationAmount",
    "FeeProrationRate",
]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical(value):
    return " ".join(str(value or "").split()).casefold()


def decimal_value(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def is_true(value):
    return canonical(value) in {"1", "true", "yes", "y"}


def parse_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except ValueError:
        return None


def amounts_equal(left, right):
    return (
        left is not None
        and right is not None
        and abs(left - right) <= TOLERANCE
    )


def amount_class(configured, base_amount, computed_amount):
    base_match = amounts_equal(configured, base_amount)
    computed_match = amounts_equal(configured, computed_amount)
    if base_amount is None or computed_amount is None:
        return "missing-amount", base_match, computed_match
    if base_match and computed_match:
        return "exact-base-and-computed", True, True
    if base_match:
        return "base-matches-computed-adjusted", True, False
    if computed_match:
        return "computed-matches-base-differs", False, True
    return "amount-mismatch", False, False


def ready_fixed_entries(catalog_dir: Path):
    manifest = load_json(catalog_dir / "manifest.json")
    entries = {}
    for manifest_option in manifest.get("options") or []:
        document = load_json(catalog_dir / manifest_option["path"])
        option = document["option"]
        for fee in document.get("fees") or []:
            calculation = fee.get("calculation") or {}
            applicability = fee.get("applicability") or {}
            conditions = applicability.get("conditions") or []
            if not (
                fee.get("section") == "configured"
                and calculation.get("mode") == "fixed"
                and calculation.get("amountStatus") == "calculable"
                and calculation.get("fixedAmount") is not None
                and not conditions
            ):
                continue
            rule = calculation.get("rule") or {}
            key = (
                canonical(option.get("permitType")),
                canonical(option.get("workClass")),
                canonical(fee.get("feeTemplateFeeId")),
            )
            if key in entries:
                raise ValueError(
                    "Duplicate ready fixed configuration key: "
                    + " | ".join(key)
                )
            entries[key] = {
                "manifest": manifest,
                "option": option,
                "fee": fee,
                "configuredAmount": Decimal(
                    str(calculation.get("fixedAmount"))
                ),
                "setupStart": parse_datetime(rule.get("startDate")),
                "setupEnd": parse_datetime(rule.get("endDate")),
                "counts": Counter(),
                "caseIds": set(),
                "latestFeeCreatedOn": None,
                "samples": {},
            }
    return manifest, entries


def within_validation_period(
    fee_created,
    entry,
    validation_start,
    validation_end,
):
    if fee_created is None:
        return False
    fee_date = fee_created.date()
    if fee_date < validation_start or fee_date > validation_end:
        return False
    start = entry["setupStart"]
    end = entry["setupEnd"]
    if start and fee_date < start.date():
        return False
    if end and fee_date > end.date():
        return False
    return True


def retain_sample(entry, sample_class, fee_created, row, limit):
    if limit <= 0:
        return
    samples = entry["samples"].setdefault(sample_class, [])
    samples.append((fee_created or datetime.min, dict(row)))
    samples.sort(key=lambda item: item[0], reverse=True)
    del samples[limit:]


def stream_history(
    path,
    entries,
    validation_start,
    validation_end,
    samples_per_class,
    progress_every,
):
    rows_read = 0
    matched_rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(REQUIRED_HISTORY_COLUMNS - set(reader.fieldnames or []))
        if missing:
            raise ValueError(
                "Historical CSV is missing columns: " + ", ".join(missing)
            )
        for row in reader:
            rows_read += 1
            if progress_every and rows_read % progress_every == 0:
                print(
                    f"Read {rows_read:,} historical rows; "
                    f"matched {matched_rows:,} current fixed-fee rows..."
                )
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
            entry["counts"]["RowsMatchingConfigurationKey"] += 1
            fee_created = parse_datetime(row.get("FeeCreatedOn"))
            if not within_validation_period(
                fee_created,
                entry,
                validation_start,
                validation_end,
            ):
                entry["counts"]["RowsOutsideValidationPeriod"] += 1
                continue

            matched_rows += 1
            counts = entry["counts"]
            counts["HistoricalRowsInValidationPeriod"] += 1
            case_id = str(row.get("FeeOwnerPermitID") or "").strip()
            if case_id:
                entry["caseIds"].add(case_id)
            latest = entry["latestFeeCreatedOn"]
            if fee_created and (latest is None or fee_created > latest):
                entry["latestFeeCreatedOn"] = fee_created

            manual = is_true(row.get("IsManuallyAdded"))
            counts[
                "ManualHistoricalRows" if manual else "StandardHistoricalRows"
            ] += 1
            configured = entry["configuredAmount"]
            base_amount = decimal_value(row.get("BaseAmount"))
            computed_amount = decimal_value(row.get("ComputedAmount"))
            classification, base_match, computed_match = amount_class(
                configured,
                base_amount,
                computed_amount,
            )
            counts[classification] += 1
            if base_match:
                counts["ExactBaseRows"] += 1
            else:
                counts["BaseMismatchRows"] += 1
            if computed_match:
                counts["ExactComputedRows"] += 1
            if classification == "exact-base-and-computed":
                counts["ExactBaseAndComputedRows"] += 1
            if classification == "base-matches-computed-adjusted":
                counts["BaseMatchesComputedAdjustedRows"] += 1

            prefix = "Manual" if manual else "Standard"
            if base_match:
                counts[prefix + "ExactBaseRows"] += 1
            else:
                counts[prefix + "BaseMismatchRows"] += 1
            if computed_match:
                counts[prefix + "ExactComputedRows"] += 1
            if classification == "exact-base-and-computed":
                counts[prefix + "ExactBaseAndComputedRows"] += 1
            if classification == "base-matches-computed-adjusted":
                counts[prefix + "BaseMatchesComputedAdjustedRows"] += 1

            retain_sample(
                entry,
                ("manual-" if manual else "standard-") + classification,
                fee_created,
                row,
                samples_per_class,
            )
    return rows_read, matched_rows


def ratio(numerator, denominator):
    if not denominator:
        return None
    return round(numerator / denominator, 6)


def evidence_status(counts):
    if not counts["HistoricalRowsInValidationPeriod"]:
        return "no-validation-period-evidence"
    if not counts["StandardHistoricalRows"]:
        return "manual-only-evidence"
    if counts["StandardBaseMismatchRows"]:
        return "base-amount-review"
    if (
        counts["StandardExactComputedRows"]
        < counts["StandardHistoricalRows"]
    ):
        return "base-matches-with-computed-adjustments"
    return "matches-current-configuration"


def summary_row(entry, validation_start, validation_end):
    manifest = entry["manifest"]
    option = entry["option"]
    fee = entry["fee"]
    occurrence = fee.get("occurrence") or {}
    counts = entry["counts"]
    standard = counts["StandardHistoricalRows"]
    latest = entry["latestFeeCreatedOn"]
    return {
        "CatalogVersion": manifest.get("catalogVersion"),
        "ConfigurationAsOfDate": manifest.get("configurationAsOfDate"),
        "OptionKey": option.get("optionKey"),
        "PermitType": option.get("permitType"),
        "WorkClass": option.get("workClass"),
        "FeeKey": fee.get("feeKey"),
        "FeeTemplateFeeID": fee.get("feeTemplateFeeId"),
        "FeeName": fee.get("name"),
        "ConfiguredAmount": str(entry["configuredAmount"]),
        "SetupStartDate": (
            entry["setupStart"].date().isoformat()
            if entry["setupStart"] else None
        ),
        "SetupEndDate": (
            entry["setupEnd"].date().isoformat()
            if entry["setupEnd"] else None
        ),
        "ValidationStartDate": validation_start.isoformat(),
        "ValidationEndDate": validation_end.isoformat(),
        "OccurrenceStatus": occurrence.get("status"),
        "OccurrenceNumerator": occurrence.get("numerator"),
        "OccurrenceDenominator": occurrence.get("denominator"),
        "OccurrenceRate": occurrence.get("rate"),
        "HistoricalRowsInValidationPeriod": counts[
            "HistoricalRowsInValidationPeriod"
        ],
        "DistinctCases": len(entry["caseIds"]),
        "StandardHistoricalRows": standard,
        "ManualHistoricalRows": counts["ManualHistoricalRows"],
        "ExactBaseRows": counts["ExactBaseRows"],
        "ExactComputedRows": counts["ExactComputedRows"],
        "ExactBaseAndComputedRows": counts["ExactBaseAndComputedRows"],
        "BaseMatchesComputedAdjustedRows": counts[
            "BaseMatchesComputedAdjustedRows"
        ],
        "BaseMismatchRows": counts["BaseMismatchRows"],
        "StandardExactBaseRows": counts["StandardExactBaseRows"],
        "StandardExactComputedRows": counts["StandardExactComputedRows"],
        "StandardExactBaseAndComputedRows": counts[
            "StandardExactBaseAndComputedRows"
        ],
        "StandardBaseMatchesComputedAdjustedRows": counts[
            "StandardBaseMatchesComputedAdjustedRows"
        ],
        "StandardBaseMismatchRows": counts["StandardBaseMismatchRows"],
        "StandardBaseMatchRate": ratio(
            counts["StandardExactBaseRows"], standard
        ),
        "StandardComputedMatchRate": ratio(
            counts["StandardExactComputedRows"], standard
        ),
        "LatestFeeCreatedOn": latest.isoformat() if latest else None,
        "EvidenceStatus": evidence_status(counts),
    }


def sample_rows(entries):
    output = []
    for entry in entries.values():
        manifest = entry["manifest"]
        option = entry["option"]
        fee = entry["fee"]
        for sample_class, samples in sorted(entry["samples"].items()):
            for _, row in samples:
                output.append(
                    {
                        "SampleClass": sample_class,
                        "CatalogVersion": manifest.get("catalogVersion"),
                        "OptionKey": option.get("optionKey"),
                        "PermitType": option.get("permitType"),
                        "WorkClass": option.get("workClass"),
                        "FeeKey": fee.get("feeKey"),
                        "FeeTemplateFeeID": fee.get("feeTemplateFeeId"),
                        "CatalogFeeName": fee.get("name"),
                        "ConfiguredAmount": str(entry["configuredAmount"]),
                        "SetupStartDate": entry["setupStart"].date().isoformat(),
                        "SetupEndDate": entry["setupEnd"].date().isoformat(),
                        "ParentPermitID": row.get("ParentPermitID"),
                        "ParentPermitNumber": row.get("ParentPermitNumber"),
                        "ParentEPLSummaryURL": row.get("ParentEPLSummaryURL"),
                        "FeeOwnerPermitID": row.get("FeeOwnerPermitID"),
                        "FeeOwnerPermitNumber": row.get("FeeOwnerPermitNumber"),
                        "FeeOwnerEPLSummaryURL": row.get("FeeOwnerEPLSummaryURL"),
                        "ComputedFeeID": row.get("ComputedFeeID"),
                        "HistoricalFeeName": row.get("FeeName"),
                        "FeeStatusName": row.get("FeeStatusName"),
                        "FeeCreatedOn": row.get("FeeCreatedOn"),
                        "FeeLastChangedOn": row.get("FeeLastChangedOn"),
                        "IsManuallyAdded": row.get("IsManuallyAdded"),
                        "IsProcessed": row.get("IsProcessed"),
                        "BaseAmount": row.get("BaseAmount"),
                        "ComputedAmount": row.get("ComputedAmount"),
                        "CreditAmount": row.get("CreditAmount"),
                        "CPIAmount": row.get("CPIAmount"),
                        "FeeProrationAmount": row.get("FeeProrationAmount"),
                        "FeeProrationRate": row.get("FeeProrationRate"),
                    }
                )
    return output


def write_csv(path, rows, fieldnames):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Compare currently calculable direct fixed fees with historical "
            "EnerGov assessments created during an explicit fiscal "
            "validation window."
        )
    )
    parser.add_argument("catalog_dir")
    parser.add_argument("historical_csv")
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--samples-per-class", type=int, default=3)
    parser.add_argument("--progress-every", type=int, default=100000)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    catalog_dir = Path(args.catalog_dir)
    historical_path = Path(args.historical_csv)
    validation_start = datetime.strptime(
        args.validation_start, "%Y-%m-%d"
    ).date()
    validation_end = datetime.strptime(
        args.validation_end, "%Y-%m-%d"
    ).date()
    if validation_end < validation_start:
        raise ValueError("Validation end date precedes start date.")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else catalog_dir.with_name(catalog_dir.name + "-fixed-fixtures")
    )
    summary_path = output_dir / "fixed_fee_fixture_summary.csv"
    sample_path = output_dir / "fixed_fee_fixture_cases.csv"
    json_path = output_dir / "fixed_fee_fixture_summary.json"
    existing = [
        str(path)
        for path in (summary_path, sample_path, json_path)
        if path.exists()
    ]
    if existing and not args.overwrite:
        raise FileExistsError(
            "Output files already exist; use --overwrite: "
            + ", ".join(existing)
        )

    manifest, entries = ready_fixed_entries(catalog_dir)
    rows_read, matched_rows = stream_history(
        historical_path,
        entries,
        validation_start,
        validation_end,
        args.samples_per_class,
        args.progress_every,
    )
    summary_rows = sorted(
        (
            summary_row(entry, validation_start, validation_end)
            for entry in entries.values()
        ),
        key=lambda row: (
            row["PermitType"],
            row["WorkClass"],
            row["FeeName"],
            row["FeeKey"],
        ),
    )
    samples = sorted(
        sample_rows(entries),
        key=lambda row: (
            row["PermitType"],
            row["WorkClass"],
            row["CatalogFeeName"],
            row["SampleClass"],
            row["FeeCreatedOn"],
        ),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(summary_path, summary_rows, SUMMARY_FIELDS)
    write_csv(sample_path, samples, SAMPLE_FIELDS)

    statuses = Counter(row["EvidenceStatus"] for row in summary_rows)
    summary = {
        "schemaVersion": "1.0",
        "catalogVersion": manifest.get("catalogVersion"),
        "configurationAsOfDate": manifest.get("configurationAsOfDate"),
        "historicalCsv": str(historical_path),
        "historicalRowsRead": rows_read,
        "readyDirectFixedConfigurations": len(entries),
        "matchedHistoricalRowsInValidationPeriod": matched_rows,
        "configurationsByEvidenceStatus": dict(sorted(statuses.items())),
        "configurationsWithValidationPeriodEvidence": sum(
            row["HistoricalRowsInValidationPeriod"] > 0
            for row in summary_rows
        ),
        "configurationsWithStandardEvidence": sum(
            row["StandardHistoricalRows"] > 0 for row in summary_rows
        ),
        "configurationsWithStandardBaseMismatch": sum(
            row["StandardBaseMismatchRows"] > 0 for row in summary_rows
        ),
        "sampleRowsWritten": len(samples),
        "comparisonTolerance": str(TOLERANCE),
        "validationStartDate": validation_start.isoformat(),
        "validationEndDate": validation_end.isoformat(),
        "dateBasis": (
            "FeeCreatedOn within the explicit validation window and the "
            "catalog fee setup start/end dates"
        ),
        "notes": [
            "Only direct parent-fee rows are compared; related permits are scenario-dependent.",
            "Explicit validation dates are required because CASCHEDULE names carry annual versions while stored date fields can be broad or placeholder values.",
            "Configured fixed amount is compared separately with BaseAmount and ComputedAmount.",
            "Manual fee instances remain visible but are excluded from standard match rates.",
            "A computed-amount difference can reflect a valid adjustment even when BaseAmount matches.",
        ],
    }
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"Historical rows read:                 {rows_read:,}")
    print(f"Ready direct fixed configurations:   {len(entries):,}")
    print(
        "Validation window:                  "
        f"{validation_start} through {validation_end}"
    )
    print(f"Matched rows in validation window:   {matched_rows:,}")
    print(
        "Configurations with evidence:       "
        f"{summary['configurationsWithValidationPeriodEvidence']:,}"
    )
    print(
        "Configurations with base mismatch:  "
        f"{summary['configurationsWithStandardBaseMismatch']:,}"
    )
    print(f"Evidence statuses:                   {dict(sorted(statuses.items()))}")
    print(f"Fixture summary:                     {summary_path}")
    print(f"Fixture cases:                       {sample_path}")
    print(f"Fixture JSON:                        {json_path}")


if __name__ == "__main__":
    main()
