from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

from energov_api import (
    DEFAULT_DOTENV,
    WEBAPI_ENVIRONMENTS,
    EnerGovWebApiClient,
    case_insensitive_get,
    load_api_credentials,
    unwrap_webapi_result,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_MAX_TEMPLATE_REQUESTS = 750


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Export inspection-type fee-template configuration from the "
            "EnerGov WebAPI."
        )
    )
    parser.add_argument(
        "--environment",
        choices=tuple(WEBAPI_ENVIRONMENTS),
        default="train",
        help="EnerGov environment to query (default: train)",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_DOTENV,
        help="Optional dotenv file (default: project-root .env)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Generated-file directory (default: project-root output)",
    )
    parser.add_argument(
        "--include-types-without-template",
        action="store_true",
        help=(
            "Call the template endpoint even when setup data has no "
            "feeTemplateID."
        ),
    )
    parser.add_argument(
        "--inventory-only",
        action="store_true",
        help=(
            "Write the inspection-type inventory without calling the "
            "per-type template endpoint."
        ),
    )
    parser.add_argument(
        "--call-per-inspection-type",
        action="store_true",
        help=(
            "Call every configured inspection type instead of reusing one "
            "response per shared feeTemplateID."
        ),
    )
    parser.add_argument(
        "--max-template-requests",
        type=int,
        default=DEFAULT_MAX_TEMPLATE_REQUESTS,
        help=(
            "Safety cap for template endpoint calls (default: 750). The run "
            "stops before template calls if the planned count exceeds it."
        ),
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.10,
        help="Seconds between template requests (default: 0.10)",
    )
    parser.add_argument(
        "--skip-raw-json",
        action="store_true",
        help="Do not write the raw inspection template responses",
    )
    return parser.parse_args()


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def as_list(value):
    return value if isinstance(value, list) else []


def ci(obj, *names, default=None):
    if not isinstance(obj, dict):
        return default
    return case_insensitive_get(obj, *names, default=default)


def json_text(value):
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return clean(value)


def get_inspection_types(client):
    response = client.call(
        "GET",
        "/api/inspections/small-secondary-data",
        query={"isNew": "true"},
    )
    result = unwrap_webapi_result(response)
    if not isinstance(result, dict):
        raise RuntimeError("Inspection secondary data returned no object.")

    types = as_list(ci(result, "inspectionTypes", default=[]))
    unique = {}
    for item in types:
        if not isinstance(item, dict):
            continue
        inspection_type_id = clean(
            ci(item, "inspectionTypeID", "inspectionTypeId")
        )
        if inspection_type_id:
            unique[inspection_type_id] = item
    return list(unique.values())


def get_template(client, inspection_type_id):
    response = client.call(
        "GET",
        "/api/inspections/setup/template",
        query={"inspectionTypeID": inspection_type_id},
    )
    result = unwrap_webapi_result(response)
    if not isinstance(result, dict):
        raise RuntimeError(
            f"Inspection type {inspection_type_id} returned no template."
        )
    return result


def inspection_type_row(item):
    return {
        "InspectionTypeID": clean(
            ci(item, "inspectionTypeID", "inspectionTypeId")
        ),
        "InspectionType": clean(ci(item, "name")),
        "Description": clean(ci(item, "description")),
        "FeeTemplateID": clean(
            ci(item, "feeTemplateID", "feeTemplateId")
        ),
        "InspectionTypeModuleID": ci(
            item, "inspectionTypeModuleID", "inspectionTypeModuleId", default=""
        ),
        "InspectionLinkID": ci(
            item, "inspectionLinkID", "inspectionLinkId", default=""
        ),
        "InspectionTypeGroupID": clean(
            ci(item, "inspectionTypeGroupID", "inspectionTypeGroupId")
        ),
        "DepartmentID": clean(ci(item, "departmentID", "departmentId")),
        "DefaultInspectionStatusID": clean(
            ci(item, "defaultInspectionStatusID", "defaultInspectionStatusId")
        ),
        "ReinspectionAssignmentID": ci(
            item,
            "reinspectionAssignmentID",
            "reinspectionAssignmentId",
            default="",
        ),
        "ReinspectWaitTime": ci(item, "reinspectWaitTime", default=""),
    }


def template_path(path):
    return " > ".join(
        clean(ci(item, "feeTemplateName"))
        or clean(ci(item, "feeTemplateID"))
        or "(unnamed template)"
        for item in path
    )


def walk_template(node, depth=0, path=None):
    if not isinstance(node, dict):
        return []
    path = [*(path or []), node]
    rows = []
    for fee in as_list(ci(node, "fees", default=[])):
        if isinstance(fee, dict):
            rows.append((fee, node, "Direct" if depth == 0 else "Child", path))
    for child in as_list(ci(node, "childTemplates", default=[])):
        rows.extend(walk_template(child, depth + 1, path))
    return rows


def fee_key(fee, source_template):
    return (
        clean(ci(fee, "feeTemplateFeeID"))
        or "|".join(
            (
                clean(ci(source_template, "feeTemplateID")),
                clean(ci(fee, "feeID")),
                clean(ci(fee, "feeName")),
            )
        )
    )


def template_assignments(root):
    assignments = walk_template(root)
    known = {fee_key(fee, node) for fee, node, _, _ in assignments}
    orphan_path = [root]
    for fee in as_list(ci(root, "allFees", default=[])):
        if not isinstance(fee, dict):
            continue
        key = fee_key(fee, root)
        if key not in known:
            assignments.append((fee, root, "AllFeesOnly", orphan_path))
            known.add(key)
    return assignments


def flatten_template(type_info, template):
    type_row = inspection_type_row(type_info)
    root = ci(template, "feeTemplate", default={}) or {}
    if not isinstance(root, dict):
        root = {}

    root_fields = {
        "RootFeeTemplateID": clean(ci(root, "feeTemplateID")),
        "RootFeeTemplate": clean(ci(root, "feeTemplateName")),
        "AskUserAddReinspectionFees": ci(
            root, "isAskUserAddReinspectionFees", default=""
        ),
        "AutoInvoiceReinspectionFees": ci(
            root, "isAutoInvoiceReinspectionFees", default=""
        ),
    }
    rows = []
    for fee, source, relationship, path in template_assignments(root):
        setups = as_list(ci(fee, "feeSetups", default=[])) or [None]
        for setup in setups:
            setup = setup or {}
            rows.append(
                {
                    **type_row,
                    **root_fields,
                    "SourceFeeTemplateID": clean(
                        ci(source, "feeTemplateID")
                    ),
                    "SourceFeeTemplate": clean(
                        ci(source, "feeTemplateName")
                    ),
                    "TemplateRelationship": relationship,
                    "TemplatePath": template_path(path),
                    "SourceTemplateCondition": json_text(
                        ci(source, "condition")
                    ),
                    "FeeTemplateFeeID": clean(
                        ci(fee, "feeTemplateFeeID")
                    ),
                    "FeeID": clean(ci(fee, "feeID")),
                    "FeeName": clean(ci(fee, "feeName")),
                    "FeeDescription": clean(
                        ci(fee, "feeDescription")
                    ),
                    "FeeTypeID": ci(fee, "feeTypeID", default=""),
                    "FeeType": clean(ci(fee, "feeType")),
                    "IsReinspectionFee": ci(
                        fee, "isReInspectionFee", default=""
                    ),
                    "Active": ci(fee, "active", default=""),
                    "Manual": ci(fee, "isManual", default=""),
                    "Hidden": ci(fee, "isHidden", default=""),
                    "Disabled": ci(fee, "isDisable", default=""),
                    "OnlineFee": ci(fee, "isOnlineFee", default=""),
                    "TaxFee": ci(fee, "isTaxFee", default=""),
                    "ARFee": ci(fee, "isARFee", default=""),
                    "TimeTrackingFee": ci(
                        fee, "isTimeTrackingFee", default=""
                    ),
                    "Condition": json_text(ci(fee, "condition")),
                    "Expression": json_text(ci(fee, "expression")),
                    "FeeInputs": json_text(
                        ci(fee, "feeInputs", default=[])
                    ),
                    "FeeSetupID": clean(ci(setup, "feeSetupID")),
                    "StartDate": clean(ci(setup, "startDate")),
                    "EndDate": clean(ci(setup, "endDate")),
                    "Amount": ci(setup, "amount", default=""),
                    "ComputationValue": ci(
                        setup, "computationValue", default=""
                    ),
                    "ComputationValueName": clean(
                        ci(setup, "computationValueName")
                    ),
                    "Minimum": ci(setup, "minimum", default=""),
                    "Maximum": ci(setup, "maximum", default=""),
                    "RoundingType": ci(
                        setup, "roundingType", default=""
                    ),
                    "RoundingValue": ci(
                        setup, "roundingValue", default=""
                    ),
                    "AdjustableCalculations": json_text(
                        ci(setup, "feeAdjustableCalcs", default=[])
                    ),
                }
            )
    return rows


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def template_request_groups(candidates, call_per_inspection_type=False):
    groups = {}
    for item in candidates:
        type_row = inspection_type_row(item)
        fee_template_id = type_row["FeeTemplateID"]
        if call_per_inspection_type or not fee_template_id:
            key = ("inspection-type", type_row["InspectionTypeID"])
        else:
            key = ("fee-template", fee_template_id)
        groups.setdefault(key, []).append(item)
    return list(groups.values())


def main():
    args = parse_args()
    if args.max_template_requests < 0:
        raise ValueError("--max-template-requests cannot be negative.")
    if args.request_delay < 0:
        raise ValueError("--request-delay cannot be negative.")
    env_file = args.env_file
    if not env_file.is_absolute():
        env_file = PROJECT_ROOT / env_file
    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    inventory_csv = output_dir / "inspection_type_inventory.csv"
    fee_csv = output_dir / "inspection_fee_map.csv"
    raw_json = output_dir / "inspection_fee_templates.json"
    error_json = output_dir / "inspection_fee_errors.json"
    username, password = load_api_credentials(env_file)

    inventory = []
    fee_rows = []
    raw_templates = []
    errors = []

    with EnerGovWebApiClient.from_credentials(
        username,
        password,
        environment=args.environment,
    ) as client:
        inspection_types = get_inspection_types(client)
        inventory = [inspection_type_row(item) for item in inspection_types]
        candidates = [
            item
            for item in inspection_types
            if args.include_types_without_template
            or clean(ci(item, "feeTemplateID", "feeTemplateId"))
        ]
        request_groups = template_request_groups(
            candidates,
            args.call_per_inspection_type,
        )
        print(f"Found {len(inspection_types):,} inspection types.")
        print(f"Types with templates selected: {len(candidates):,}")
        print(f"Template endpoint calls planned: {len(request_groups):,}")

        if args.inventory_only:
            request_groups = []
            print("Inventory-only mode: no template calls will be made.")
        elif len(request_groups) > args.max_template_requests:
            raise RuntimeError(
                f"Refusing {len(request_groups):,} template calls because the "
                f"safety cap is {args.max_template_requests:,}. Use "
                "--max-template-requests only after reviewing the inventory."
            )

        for number, group in enumerate(request_groups, start=1):
            representative = group[0]
            type_row = inspection_type_row(representative)
            try:
                template = get_template(client, type_row["InspectionTypeID"])
                if not args.skip_raw_json:
                    raw_templates.append(
                        {
                            "RequestedInspectionTypeID": type_row[
                                "InspectionTypeID"
                            ],
                            "FeeTemplateID": type_row["FeeTemplateID"],
                            "InspectionTypes": [
                                inspection_type_row(item) for item in group
                            ],
                            "Template": template,
                        }
                    )
                for item in group:
                    fee_rows.extend(flatten_template(item, template))
            except Exception as error:
                errors.append(
                    {
                        "InspectionTypeID": type_row["InspectionTypeID"],
                        "InspectionType": type_row["InspectionType"],
                        "FeeTemplateID": type_row["FeeTemplateID"],
                        "AffectedInspectionTypeCount": len(group),
                        "Stage": "setup-template",
                        "Error": str(error),
                    }
                )

            if number % 25 == 0:
                print(
                    f"Processed {number:,}/{len(request_groups):,} "
                    "template calls..."
                )
            time.sleep(args.request_delay)

    inventory_fields = list(inspection_type_row({}))
    write_csv(inventory_csv, inventory, inventory_fields)
    if fee_rows:
        write_csv(fee_csv, fee_rows, list(fee_rows[0]))
    else:
        fee_csv.write_text("", encoding="utf-8-sig")

    if not args.skip_raw_json:
        raw_json.write_text(
            json.dumps(raw_templates, indent=2, default=str),
            encoding="utf-8",
        )
    error_json.write_text(
        json.dumps(errors, indent=2, default=str),
        encoding="utf-8",
    )

    distinct_assignments = {
        row["FeeTemplateFeeID"]
        for row in fee_rows
        if row["FeeTemplateFeeID"]
    }
    reinspection_assignments = {
        row["FeeTemplateFeeID"]
        for row in fee_rows
        if row["FeeTemplateFeeID"]
        and row["IsReinspectionFee"] is True
    }
    print()
    print(f"Fee setup rows:                  {len(fee_rows):,}")
    print(f"Distinct fee assignments:       {len(distinct_assignments):,}")
    print(f"Flagged reinspection fees:       {len(reinspection_assignments):,}")
    print(f"Errors:                          {len(errors):,}")
    print(f"Environment:                     {args.environment}")
    print(f"Inspection types:                {inventory_csv}")
    print(f"Fee map:                         {fee_csv}")
    if not args.skip_raw_json:
        print(f"Raw templates:                    {raw_json}")
    print(f"Errors:                          {error_json}")


if __name__ == "__main__":
    main()
