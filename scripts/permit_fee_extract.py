from __future__ import annotations

import argparse
import copy
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
    set_case_insensitive,
    unwrap_webapi_result,
    webapi_rows,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

PAGE_SIZE = 250
REQUEST_DELAY = 0.05


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Export permit fee-template configuration from the "
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
        "--skip-raw-json",
        action="store_true",
        help="Do not write the large raw template-response JSON file",
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


def get_all_permit_types(client):
    response = client.call(
        "GET",
        "/api/permittypes/defaultcriteria",
    )

    criteria = unwrap_webapi_result(response)

    if not isinstance(criteria, dict):
        raise RuntimeError(
            "Permit type default criteria did not return an object."
        )

    rows = []
    page = 1

    while True:
        payload = copy.deepcopy(criteria)

        set_case_insensitive(payload, "pageNumber", page)
        set_case_insensitive(payload, "pageSize", PAGE_SIZE)

        response = client.call(
            "POST",
            "/api/permittypes/search",
            payload,
        )

        page_rows, page_count = webapi_rows(
            response,
            f"Permit type search page {page}",
        )

        rows.extend(page_rows)

        if not page_rows:
            break

        if page_count and page >= page_count:
            break

        if not page_count and len(page_rows) < PAGE_SIZE:
            break

        page += 1

    return rows


def get_permit_type(client, permit_type_id):
    response = client.call(
        "GET",
        f"/api/permittypes/{permit_type_id}",
    )

    result = unwrap_webapi_result(response)

    if not isinstance(result, dict):
        raise RuntimeError(
            f"Permit type {permit_type_id} returned no object."
        )

    return result


def get_template(client, permit_type_id, work_class_id):
    response = client.call(
        "GET",
        "/api/permits/setup/template",
        query={
            "permitTypeId": permit_type_id,
            "permitWorkClassId": work_class_id,
        },
    )

    result = unwrap_webapi_result(response)

    if not isinstance(result, dict):
        raise RuntimeError(
            "Permit type/work class template returned no object."
        )

    return result


def flatten_fee(
    permit_type_id,
    permit_type_name,
    work_class_id,
    work_class_name,
    template,
):
    fee_template = ci(
        template,
        "feeTemplate",
        default={},
    ) or {}

    fee_template_id = clean(
        ci(fee_template, "feeTemplateID")
    )
    fee_template_name = clean(
        ci(fee_template, "feeTemplateName")
    )

    fees = as_list(
        ci(fee_template, "fees", default=[])
    )

    # Some templates may expose additional configured fees here.
    all_fees = as_list(
        ci(fee_template, "allFees", default=[])
    )

    # Preserve unique feeTemplateFeeIDs.
    combined = {}
    for fee in fees + all_fees:
        if not isinstance(fee, dict):
            continue

        key = (
            clean(ci(fee, "feeTemplateFeeID"))
            or clean(ci(fee, "feeID"))
            or clean(ci(fee, "feeName"))
        )

        combined[key] = fee

    output = []

    for fee in combined.values():
        setups = as_list(
            ci(fee, "feeSetups", default=[])
        )

        if not setups:
            setups = [None]

        for setup in setups:
            setup = setup or {}

            output.append(
                {
                    "PermitTypeID": permit_type_id,
                    "PermitType": permit_type_name,
                    "WorkClassID": work_class_id,
                    "WorkClass": work_class_name,

                    "FeeTemplateID": fee_template_id,
                    "FeeTemplate": fee_template_name,

                    "FeeTemplateFeeID": clean(
                        ci(fee, "feeTemplateFeeID")
                    ),
                    "FeeID": clean(
                        ci(fee, "feeID")
                    ),
                    "FeeName": clean(
                        ci(fee, "feeName")
                    ),
                    "FeeDescription": clean(
                        ci(fee, "feeDescription")
                    ),
                    "FeeTypeID": clean(
                        ci(fee, "feeTypeID")
                    ),
                    "FeeType": clean(
                        ci(fee, "feeType")
                    ),

                    "Active": ci(
                        fee, "active", default=""
                    ),
                    "Manual": ci(
                        fee, "isManual", default=""
                    ),
                    "Hidden": ci(
                        fee, "isHidden", default=""
                    ),
                    "Disabled": ci(
                        fee, "isDisable", default=""
                    ),
                    "OnlineFee": ci(
                        fee, "isOnlineFee", default=""
                    ),
                    "TaxFee": ci(
                        fee, "isTaxFee", default=""
                    ),
                    "ARFee": ci(
                        fee, "isARFee", default=""
                    ),
                    "TimeTrackingFee": ci(
                        fee, "isTimeTrackingFee", default=""
                    ),

                    "Condition": clean(
                        ci(fee, "condition")
                    ),
                    "Expression": clean(
                        ci(fee, "expression")
                    ),

                    "FeeSetupID": clean(
                        ci(setup, "feeSetupID")
                    ),
                    "StartDate": clean(
                        ci(setup, "startDate")
                    ),
                    "EndDate": clean(
                        ci(setup, "endDate")
                    ),
                    "Amount": ci(
                        setup, "amount", default=""
                    ),
                    "ComputationValue": ci(
                        setup,
                        "computationValue",
                        default="",
                    ),
                    "ComputationValueName": clean(
                        ci(
                            setup,
                            "computationValueName",
                        )
                    ),
                    "Minimum": ci(
                        setup, "minimum", default=""
                    ),
                    "Maximum": ci(
                        setup, "maximum", default=""
                    ),
                    "RoundingType": clean(
                        ci(setup, "roundingType")
                    ),
                    "RoundingValue": ci(
                        setup,
                        "roundingValue",
                        default="",
                    ),
                }
            )

    return output


def main():
    args = parse_args()
    env_file = args.env_file
    if not env_file.is_absolute():
        env_file = PROJECT_ROOT / env_file

    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    output_csv = output_dir / "permit_fee_map.csv"
    output_json = output_dir / "permit_fee_templates.json"
    error_json = output_dir / "permit_fee_errors.json"

    username, password = load_api_credentials(
        env_file
    )

    rows = []
    raw_templates = []
    errors = []

    with EnerGovWebApiClient.from_credentials(
        username,
        password,
        environment=args.environment,
    ) as client:

        permit_types = get_all_permit_types(client)

        print(
            f"Found {len(permit_types):,} permit types."
        )

        combinations = []

        for number, item in enumerate(
            permit_types,
            start=1,
        ):
            permit_type_id = clean(
                ci(
                    item,
                    "permitTypeId",
                    "permitTypeID",
                )
            )

            permit_type_name = clean(
                ci(item, "name", "permitTypeName")
            )

            if not permit_type_id:
                continue

            try:
                detail = get_permit_type(
                    client,
                    permit_type_id,
                )
            except Exception as exc:
                errors.append(
                    {
                        "PermitTypeID": permit_type_id,
                        "PermitType": permit_type_name,
                        "Stage": "permit-type-detail",
                        "Error": str(exc),
                    }
                )
                continue

            permit_type_name = (
                clean(ci(detail, "name"))
                or permit_type_name
            )

            if ci(detail, "active", default=True) is False:
                continue

            work_classes = as_list(
                ci(
                    detail,
                    "workClassTemplates",
                    default=[],
                )
            )

            for wc in work_classes:
                if not isinstance(wc, dict):
                    continue

                if ci(
                    wc,
                    "isDeleted",
                    default=False,
                ):
                    continue

                work_class_id = clean(
                    ci(
                        wc,
                        "permitWorkClassID",
                    )
                )

                work_class_name = clean(
                    ci(
                        wc,
                        "workClassName",
                    )
                )

                if not work_class_id:
                    continue

                combinations.append(
                    (
                        permit_type_id,
                        permit_type_name,
                        work_class_id,
                        work_class_name,
                    )
                )

            if number % 25 == 0:
                print(
                    f"Loaded {number:,}/"
                    f"{len(permit_types):,} permit types..."
                )

            time.sleep(REQUEST_DELAY)

        print(
            f"Found {len(combinations):,} "
            "permit type/work class combinations."
        )

        for number, (
            permit_type_id,
            permit_type_name,
            work_class_id,
            work_class_name,
        ) in enumerate(combinations, start=1):

            try:
                template = get_template(
                    client,
                    permit_type_id,
                    work_class_id,
                )

                if not args.skip_raw_json:
                    raw_templates.append(
                        {
                            "PermitTypeID": permit_type_id,
                            "PermitType": permit_type_name,
                            "WorkClassID": work_class_id,
                            "WorkClass": work_class_name,
                            "Template": template,
                        }
                    )

                rows.extend(
                    flatten_fee(
                        permit_type_id,
                        permit_type_name,
                        work_class_id,
                        work_class_name,
                        template,
                    )
                )

            except Exception as exc:
                errors.append(
                    {
                        "PermitTypeID": permit_type_id,
                        "PermitType": permit_type_name,
                        "WorkClassID": work_class_id,
                        "WorkClass": work_class_name,
                        "Stage": "setup-template",
                        "Error": str(exc),
                    }
                )

            if number % 25 == 0:
                print(
                    f"Processed {number:,}/"
                    f"{len(combinations):,} combinations..."
                )

            time.sleep(REQUEST_DELAY)

    if rows:
        fieldnames = list(rows[0])

        with output_csv.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
            )
            writer.writeheader()
            writer.writerows(rows)

    if not args.skip_raw_json:
        output_json.write_text(
            json.dumps(
                raw_templates,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    error_json.write_text(
        json.dumps(
            errors,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Fee rows: {len(rows):,}")
    print(f"Errors:   {len(errors):,}")
    print(f"Environment: {args.environment}")
    print(f"CSV:         {output_csv}")
    if not args.skip_raw_json:
        print(f"Raw JSON:    {output_json}")
    print(f"Errors:      {error_json}")


if __name__ == "__main__":
    main()
