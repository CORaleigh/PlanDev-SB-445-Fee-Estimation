from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from option_ui_schema import build_option_ui_schema


MATURE_DENOMINATOR = "MATURE_ISSUED_OR_FINALIZED"

OUTPUT_COLUMNS = [
    "PermitType",
    "WorkClass",
    "DenominatorName",
    "PermitRows",
    "FieldOrder",
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
]


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_option(catalog_dir: Path, permit_type: str, work_class: str):
    manifest = load_json(catalog_dir / "manifest.json")
    matches = [
        item
        for item in manifest.get("options") or []
        if str(item.get("permitType") or "") == permit_type
        and str(item.get("workClass") or "") == work_class
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one catalog option for {permit_type!r} / "
            f"{work_class!r}; found {len(matches)}."
        )
    return load_json(catalog_dir / str(matches[0]["path"]))


def control_index(document):
    schema = build_option_ui_schema(document)
    return {
        str(control.get("key") or "").strip(): control
        for control in schema.get("controls") or []
        if str(control.get("key") or "").strip()
    }


def source_metadata(input_key: str, control: dict):
    if input_key == "ValuationValue":
        return "permit", "VALUE"

    source = str(control.get("source") or "").strip().lower()
    if source == "system" and input_key == "ValuationValue":
        return "permit", "VALUE"

    return "custom-saver", input_key


def active_definition(value_kind: str):
    value_kind = str(value_kind or "").strip().lower()
    if value_kind == "numeric":
        return "numeric <> 0"
    if value_kind == "boolean":
        return "boolean = true"
    if value_kind == "choice":
        return "nonblank choice"
    return "unknown"


def normalize_bool(value):
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return "1"
    if text in {"0", "false", "no", "n"}:
        return "0"
    return text


def read_source_rows(path: Path, denominator_name: str):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"Source prevalence CSV is empty: {path}")

    required = {
        "FieldOrder",
        "InputKey",
        "ValueKind",
        "PhysicalColumnFound",
        "PermitRows",
        "PopulatedRows",
        "PopulatedPct",
        "ActiveRows",
        "ActivePct",
    }
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(
            f"Source prevalence CSV {path} is missing columns: "
            + ", ".join(missing)
        )

    if "DenominatorName" in rows[0]:
        rows = [
            row
            for row in rows
            if str(row.get("DenominatorName") or "").strip()
            == denominator_name
        ]
        if not rows:
            raise ValueError(
                f"No rows with DenominatorName={denominator_name!r} "
                f"in {path}."
            )

    return rows


def normalize_rows(
    rows,
    document,
    permit_type,
    work_class,
    denominator_name=MATURE_DENOMINATOR,
):
    controls = control_index(document)
    normalized = []

    for row in rows:
        input_key = str(row.get("InputKey") or "").strip()
        if not input_key:
            raise ValueError("Source row has empty InputKey.")
        control = controls.get(input_key)
        if control is None:
            raise ValueError(
                f"InputKey {input_key!r} is not a compiled control for "
                f"{permit_type} / {work_class}."
            )

        input_label = str(
            row.get("InputLabel")
            or control.get("displayLabel")
            or control.get("label")
            or input_key
        ).strip()
        value_kind = str(row.get("ValueKind") or "").strip().lower()
        source_kind = str(row.get("SourceKind") or "").strip()
        source_column = str(row.get("SourceColumn") or "").strip()
        if not source_kind or not source_column:
            source_kind, source_column = source_metadata(
                input_key,
                control,
            )

        normalized.append(
            {
                "PermitType": permit_type,
                "WorkClass": work_class,
                "DenominatorName": denominator_name,
                "PermitRows": str(row.get("PermitRows") or "").strip(),
                "FieldOrder": str(row.get("FieldOrder") or "").strip(),
                "InputKey": input_key,
                "InputLabel": input_label,
                "SourceKind": source_kind,
                "SourceColumn": source_column,
                "ValueKind": value_kind,
                "PhysicalColumnFound": normalize_bool(
                    row.get("PhysicalColumnFound")
                ),
                "PopulatedRows": str(
                    row.get("PopulatedRows") or ""
                ).strip(),
                "PopulatedPct": str(
                    row.get("PopulatedPct") or ""
                ).strip(),
                "ActiveRows": str(
                    row.get("ActiveRows") or ""
                ).strip(),
                "ActivePct": str(
                    row.get("ActivePct") or ""
                ).strip(),
                "ActiveDefinition": str(
                    row.get("ActiveDefinition")
                    or active_definition(value_kind)
                ).strip(),
            }
        )

    return sorted(
        normalized,
        key=lambda row: int(row["FieldOrder"]),
    )


def write_rows(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Normalize prior direct-field prevalence query output into the "
            "catalog --input-prevalence import schema."
        )
    )
    parser.add_argument("source_csv")
    parser.add_argument("--catalog-dir", required=True)
    parser.add_argument("--permit-type", required=True)
    parser.add_argument("--work-class", required=True)
    parser.add_argument(
        "--denominator-name",
        default=MATURE_DENOMINATOR,
    )
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    source = Path(args.source_csv)
    catalog_dir = Path(args.catalog_dir)
    output = Path(args.output)

    document = load_option(
        catalog_dir,
        args.permit_type,
        args.work_class,
    )
    rows = read_source_rows(source, args.denominator_name)
    normalized = normalize_rows(
        rows,
        document,
        args.permit_type,
        args.work_class,
        args.denominator_name,
    )
    write_rows(output, normalized)

    positive = sum(
        1
        for row in normalized
        if float(row.get("ActivePct") or 0) > 0
    )
    print(f"Option:       {args.permit_type} / {args.work_class}")
    print(f"Input rows:   {len(normalized)}")
    print(f"Active > 0:   {positive}")
    print(f"Output:       {output}")


if __name__ == "__main__":
    main()
