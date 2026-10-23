from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from applicability_evaluator import WWP_FEE_NAMES, resolve_fee_applicability
from input_fee_evaluator import evaluate_fee_graph


DEFAULT_CATALOG = "output-prod/catalog-2026-09-15-r5"
FIRST_SLICE_OPTION_KEY = (
    "c168065a-e04f-4462-a25e-0af619c3ab6c|"
    "00d02749-4ba8-44b0-9676-2dd564d2841c"
)
SECOND_SLICE_OPTION_KEY = (
    "d0bed304-f2f3-457c-8b29-acf70538b15b|"
    "00d02749-4ba8-44b0-9676-2dd564d2841c"
)
THIRD_SLICE_OPTION_KEY = (
    "d84f857e-1aa0-4ab1-8d70-65c91357f79b|"
    "51733e61-f714-4257-a343-e0b713d3532a"
)
DEFAULT_OPTION_KEY = FIRST_SLICE_OPTION_KEY
DEFAULT_HTML = "output-prod/prototype/changeout-baseline.html"
DEFAULT_JSON = "output-prod/prototype/changeout-baseline.json"
REVIEW_ONLY_SECTIONS = {"manual-review", "inactive"}
PROTOTYPE_FIELD_CHOICES = {
    "Ele_Contractor": {
        "yes": "af1c8caa-2d32-436c-a724-57aa073921f1",
        "no": "4cdf5136-cd0d-4269-8324-8a7138bda694",
    },
    "Mec_Contractor": {
        "yes": "ebec88f2-3633-48b7-b903-f5ecd8580d1b",
        "no": "97f2803d-6a47-4566-99b5-d6610aca3643",
    },
}

SECOND_SLICE_FIELD_CHOICES = {
    "Both_Mech_and_Ele": {
        "yes": "11506d22-fd36-40c3-b878-b8c8517ca7bb",
        # Two current configured GUIDs display as No. FY27 history for the
        # selected option uses this one, so the prototype uses the observed
        # value while the applicability rule itself continues to accept both.
        "no": "f91f9b5a-bcd3-4baa-bcec-2404a7199b88",
    },
}


def prototype_field_values(
    ele_contractor="yes",
    mec_contractor="yes",
    working_without_permit=False,
):
    ele = str(ele_contractor).strip().casefold()
    mec = str(mec_contractor).strip().casefold()
    if ele not in PROTOTYPE_FIELD_CHOICES["Ele_Contractor"]:
        raise ValueError("Ele_Contractor must be yes or no.")
    if mec not in PROTOTYPE_FIELD_CHOICES["Mec_Contractor"]:
        raise ValueError("Mec_Contractor must be yes or no.")
    return {
        "Ele_Contractor": PROTOTYPE_FIELD_CHOICES["Ele_Contractor"][ele],
        "Mec_Contractor": PROTOTYPE_FIELD_CHOICES["Mec_Contractor"][mec],
        "WorkingwithoutaPermit": bool(working_without_permit),
    }


def second_slice_field_values(
    both_mech_and_ele="yes",
    working_without_permit=False,
):
    both = str(both_mech_and_ele).strip().casefold()
    if both not in SECOND_SLICE_FIELD_CHOICES["Both_Mech_and_Ele"]:
        raise ValueError("Both_Mech_and_Ele must be yes or no.")
    return {
        "Both_Mech_and_Ele": SECOND_SLICE_FIELD_CHOICES[
            "Both_Mech_and_Ele"
        ][both],
        "WorkingwithoutaPermit": bool(working_without_permit),
    }


def third_slice_field_values(working_without_permit=False):
    return {
        "WorkingwithoutaPermit": bool(working_without_permit),
    }


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_option(catalog_dir: Path, option_key: str):
    manifest = load_json(catalog_dir / "manifest.json")
    for item in manifest.get("options") or []:
        if str(item.get("optionKey")) == str(option_key):
            return load_json(catalog_dir / item["path"])
    raise KeyError(f"Option key not found in catalog: {option_key}")


def is_explicit_later_event_fee(fee):
    name = " ".join(str(fee.get("name") or "").split()).casefold()
    later_event_phrases = (
        "voiding permit fee",
        "re-review",
        "rereview",
        "reinspection",
        "re-inspection",
        "extra inspect",
        "extra inspection",
        "field revision",
    )
    return (
        name.startswith("fr - ")
        or any(phrase in name for phrase in later_event_phrases)
    )


def is_review_only_fee(fee):
    section = str(fee.get("section") or "")
    mode = str((fee.get("calculation") or {}).get("mode") or "")
    return section in REVIEW_ONLY_SECTIONS or mode == "manual"


def parse_input_values(items):
    values = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"Input must use KEY=VALUE syntax: {item!r}")
        key, raw_value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("Input key cannot be empty.")
        try:
            values[key] = float(raw_value.strip())
        except ValueError as exc:
            raise ValueError(f"Input value for {key!r} must be numeric.") from exc
    return values


def _fee_name_index(fees):
    result = {}
    for fee in fees:
        name = str(fee.get("name") or "")
        result.setdefault(name, []).append(fee)
    return result


def build_applicability(fees, include_fee_names=None):
    include_fee_names = list(include_fee_names or [])
    by_name = _fee_name_index(fees)
    applicability = {}

    for fee in fees:
        fee_id = fee.get("feeTemplateFeeId")
        if not fee_id:
            continue
        section = str(fee.get("section") or "")
        status = str((fee.get("applicability") or {}).get("status") or "")
        if section in {"conditional", "event-contingent"} or status in {
            "conditional",
            "event-contingent",
        }:
            applicability[str(fee_id)] = False

    for name in include_fee_names:
        matches = by_name.get(name) or []
        if not matches:
            raise KeyError(f"Fee name not found in selected option: {name}")
        if len(matches) != 1:
            raise ValueError(
                f"Fee name is not unique in selected option: {name}. "
                "Use a unique fee name for prototype scenarios."
            )
        fee_id = matches[0].get("feeTemplateFeeId")
        if not fee_id:
            raise ValueError(f"Fee has no assignment id: {name}")
        applicability[str(fee_id)] = True

    return applicability


def display_permit_type(name):
    value = " ".join(str(name or "Permit").split())
    known = {
        "Online Only PLR/ELR/MER Changeout - Combo":
            "Plumbing / Electrical / Mechanical Change-Out",
        "DSINSP - Online Only MENR/ELNR Changeout - Combo":
            "Mechanical / Electrical Change-Out",
        "DSINSP - Electrical (Non-Residential)":
            "Electrical Permit (Non-Residential)",
    }
    if value in known:
        return known[value]
    for prefix in ("DSINSP - ", "Online Only "):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.replace("Changeout", "Change-Out")


def display_work_class(name):
    value = " ".join(str(name or "").split())
    if value == "Change Out":
        return "Change-Out"
    return value


def display_fee_name(name):
    value = " ".join(str(name or "Fee").split())
    lower = value.casefold()

    if "technology surcharge" in lower:
        return "Technology Fee"

    if lower.startswith("working without a permit"):
        discipline = ""
        if "(" in value and ")" in value:
            discipline = value.rsplit("(", 1)[1].split(")", 1)[0].strip()
        if discipline:
            return (
                "Additional Fee for Work Started Before a Permit "
                f"({discipline})"
            )
        return "Additional Fee for Work Started Before a Permit"

    code_labels = {
        "[PLMR]": "Plumbing Permit Fee",
        "[ELR]": "Electrical Permit Fee",
        "[MER]": "Mechanical Permit Fee",
        "[MENR]": "Mechanical Permit Fee",
        "[ELNR]": "Electrical Permit Fee",
    }
    for code, label in code_labels.items():
        if code in value:
            if "change out" in lower:
                return label.replace(" Permit Fee", " Change-Out Permit Fee")
            return label

    if value.startswith("FR - "):
        subject = value[len("FR - "):]
        subject = subject.replace(" (Res/Non-Res)", "")
        subject = subject.replace(" (Non-Res)", "")
        return f"{subject} Field Revision Fee"

    if value.startswith("ELNR [After Hours Inspection Fee]"):
        return "After-Hours Electrical Inspection Fee"

    cleaned = value
    for prefix in (
        "DSINSP - ",
        "MISC - ",
        "DSENG - ",
        "ENG - ",
        "FIRE - ",
        "PRCR - ",
        "PU - ",
        "ZP - ",
    ):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]

    return cleaned


def classify_estimate_status(
    blocked_fees,
    unresolved_applicability_fees,
    staff_review_fees,
    possible_additional_fees=None,
):
    reasons = []
    if staff_review_fees:
        reasons.append(
            {
                "code": "MANUAL_REVIEW_FEES",
                "label": "Some fees must be reviewed by staff before the estimate is complete.",
                "count": len(staff_review_fees),
            }
        )
    if unresolved_applicability_fees:
        reasons.append(
            {
                "code": "UNRESOLVED_APPLICABILITY",
                "label": "Staff needs more project information to decide whether some fees apply.",
                "count": len(unresolved_applicability_fees),
            }
        )
    if blocked_fees:
        reasons.append(
            {
                "code": "CALCULATION_BLOCKERS",
                "label": "Some fee amounts must be calculated or confirmed by staff.",
                "count": len(blocked_fees),
            }
        )

    if reasons:
        return {
            "code": "staff-review-required",
            "label": "Staff Review Needed",
            "requiresStaffReview": True,
            "reasons": reasons,
        }

    if possible_additional_fees:
        return {
            "code": "estimate-calculated-possible-additions",
            "label": "Estimate Calculated",
            "requiresStaffReview": False,
            "reasons": [],
        }

    return {
        "code": "automatic-estimate-complete",
        "label": "Estimate Ready",
        "requiresStaffReview": False,
        "reasons": [],
    }


def _normalized_fee_result_signature(report):
    items = []
    for bucket in (
        "regularPermitFees",
        "workingWithoutPermitAdditionalFees",
        "blockedFees",
        "unresolvedApplicabilityFees",
        "staffReviewFees",
        "possibleAdditionalFees",
    ):
        for item in report.get(bucket) or []:
            items.append(
                (
                    bucket,
                    str(item.get("feeTemplateFeeId") or ""),
                    str(item.get("name") or ""),
                    None if item.get("amount") is None else float(item["amount"]),
                    str(item.get("validationCode") or item.get("code") or ""),
                )
            )
    return tuple(sorted(items))


def compare_estimate_reports(previous_report, current_report):
    reasons = []
    revalidation_reasons = []

    previous_option = previous_report.get("option") or {}
    current_option = current_report.get("option") or {}
    if str(previous_option.get("optionKey") or "") != str(
        current_option.get("optionKey") or ""
    ):
        reasons.append(
            {
                "code": "PERMIT_OPTION_CHANGED",
                "label": "Permit type or work class changed.",
            }
        )

    previous_scenario = previous_report.get("scenario") or {}
    current_scenario = current_report.get("scenario") or {}
    if previous_scenario.get("inputValues") != current_scenario.get("inputValues"):
        reasons.append(
            {
                "code": "CALCULATION_INPUTS_CHANGED",
                "label": "One or more calculation inputs changed.",
            }
        )
    if previous_scenario.get("fieldValues") != current_scenario.get("fieldValues"):
        reasons.append(
            {
                "code": "APPLICABILITY_INPUTS_CHANGED",
                "label": "One or more applicability inputs changed.",
            }
        )

    if _normalized_fee_result_signature(previous_report) != (
        _normalized_fee_result_signature(current_report)
    ):
        reasons.append(
            {
                "code": "FEE_RESULTS_CHANGED",
                "label": "Calculated, blocked, unresolved, or staff-review fee results changed.",
            }
        )

    if bool(previous_report.get("requiresStaffReview")) != bool(
        current_report.get("requiresStaffReview")
    ):
        reasons.append(
            {
                "code": "ESTIMATE_STATUS_CHANGED",
                "label": "Estimate completion status changed.",
            }
        )

    previous_total = float(previous_report.get("estimatedTotal") or 0.0)
    current_total = float(current_report.get("estimatedTotal") or 0.0)
    if abs(previous_total - current_total) >= 0.005:
        reasons.append(
            {
                "code": "ESTIMATED_TOTAL_CHANGED",
                "label": "Known calculated total changed.",
                "previous": previous_total,
                "current": current_total,
            }
        )

    previous_snapshot = str(previous_report.get("configurationAsOfDate") or "")
    current_snapshot = str(current_report.get("configurationAsOfDate") or "")
    if previous_snapshot != current_snapshot:
        revalidation_reasons.append(
            {
                "code": "CONFIGURATION_SNAPSHOT_CHANGED",
                "label": "EnerGov configuration snapshot changed; re-evaluation is required.",
                "previous": previous_snapshot,
                "current": current_snapshot,
            }
        )

    return {
        "revisionRequired": bool(reasons),
        "materialChangeDetected": bool(reasons),
        "revisionReasons": reasons,
        "revalidationRequired": bool(revalidation_reasons),
        "revalidationReasons": revalidation_reasons,
    }


def build_report_model(
    document,
    include_fee_names=None,
    input_values=None,
    field_values=None,
    inactive_fee_ids=None,
):
    fees = list(document.get("fees") or [])
    input_values = dict(input_values or {})
    field_values = None if field_values is None else dict(field_values)
    inactive_fee_ids = {
        str(value)
        for value in (inactive_fee_ids or [])
        if str(value)
    }
    evaluation_inactive_fee_ids = inactive_fee_ids | {
        str(fee.get("feeTemplateFeeId") or "")
        for fee in fees
        if fee.get("feeTemplateFeeId")
        and is_explicit_later_event_fee(fee)
    }

    if field_values is None:
        applicability = build_applicability(fees, include_fee_names)
        unresolved_applicability = []
        unresolved_ids = set()
        applicability_mode = "manual-scenario"
    else:
        applicability, unresolved_applicability = resolve_fee_applicability(
            fees,
            field_values,
        )
        fee_by_id = {
            str(fee.get("feeTemplateFeeId") or ""): fee
            for fee in fees
            if fee.get("feeTemplateFeeId")
        }
        unresolved_ids = {
            str(item.get("feeTemplateFeeId") or "")
            for item in unresolved_applicability
            if item.get("feeTemplateFeeId")
        }
        unresolved_applicability = [
            {
                **item,
                "displayName": display_fee_name(item.get("name")),
            }
            for item in unresolved_applicability
            if (
                not is_review_only_fee(
                    fee_by_id.get(
                        str(item.get("feeTemplateFeeId") or ""),
                        {},
                    )
                )
                and not is_explicit_later_event_fee(
                    fee_by_id.get(
                        str(item.get("feeTemplateFeeId") or ""),
                        {},
                    )
                )
            )
        ]
        applicability_mode = "validated-condition-rules"
        # Keep the exact-name include switch as a diagnostic override.
        overrides = build_applicability(fees, include_fee_names)
        for fee_id, applies in overrides.items():
            if applies:
                applicability[fee_id] = True

    for fee_id in evaluation_inactive_fee_ids:
        applicability[fee_id] = False
    unresolved_ids = {
        fee_id for fee_id in unresolved_ids
        if fee_id not in evaluation_inactive_fee_ids
    }
    unresolved_applicability = [
        item
        for item in unresolved_applicability
        if str(item.get("feeTemplateFeeId") or "")
        not in evaluation_inactive_fee_ids
    ]

    candidate_fees = [fee for fee in fees if not is_review_only_fee(fee)]
    target_ids = [
        str(fee["feeTemplateFeeId"])
        for fee in candidate_fees
        if fee.get("feeTemplateFeeId")
    ]
    results = evaluate_fee_graph(
        fees,
        target_ids,
        user_inputs=input_values,
        case_inputs=input_values,
        applicability=applicability,
        inactive_fee_ids=evaluation_inactive_fee_ids,
    )

    calculated = []
    excluded = []
    possible_additional = []
    blocked = []
    staff_review = []
    inactive = []

    for fee in fees:
        row = {
            "feeTemplateFeeId": fee.get("feeTemplateFeeId"),
            "name": str(fee.get("name") or "Unnamed fee"),
            "displayName": display_fee_name(
                str(fee.get("name") or "Unnamed fee")
            ),
            "description": fee.get("description"),
            "feeTypeId": fee.get("feeTypeId"),
            "feeType": fee.get("feeType"),
            "section": str(fee.get("section") or "unknown"),
            "mode": str((fee.get("calculation") or {}).get("mode") or "unknown"),
            "assessmentRole": str(
                fee.get("assessmentRole") or "standalone-or-assessed"
            ),
            "occurrence": dict(fee.get("occurrence") or {}),
            "repeat": dict(fee.get("repeat") or {}),
        }
        if row["section"] == "inactive":
            inactive.append(row)
            continue

        fee_id = str(fee.get("feeTemplateFeeId") or "")
        if fee_id in inactive_fee_ids:
            excluded.append(row)
            continue

        applicability_status = str(
            (fee.get("applicability") or {}).get("status") or ""
        )
        if is_review_only_fee(fee):
            if (
                row["section"] == "event-contingent"
                or applicability_status == "event-contingent"
                or is_explicit_later_event_fee(fee)
            ):
                excluded.append(row)
                continue

            conditions = list(
                (fee.get("applicability") or {}).get("conditions") or []
            )
            applies = applicability.get(fee_id) if fee_id else None

            if conditions:
                if fee_id in unresolved_ids:
                    possible_additional.append(row)
                elif applies is True:
                    staff_review.append(row)
                elif applies is False:
                    excluded.append(row)
                else:
                    possible_additional.append(row)
                continue

            if (
                fee_id
                and applies is True
                and str(fee.get("name") or "") in included
            ):
                staff_review.append(row)
                continue

            possible_additional.append(row)
            continue

        if not fee_id:
            blocked.append(
                {
                    **row,
                    "validationCode": "MISSING_FEE_ASSIGNMENT_ID",
                    "message": "Fee has no assignment id and cannot be evaluated.",
                }
            )
            continue

        result = results[fee_id]
        status = result.get("status")
        if status == "calculated":
            calculated.append(
                {
                    **row,
                    "amount": float(result.get("amount") or 0.0),
                    "dependencies": list(result.get("dependencies") or []),
                }
            )
        elif status == "not-applicable":
            excluded.append(row)
        else:
            codes = result.get("validationCodes") or ["UNKNOWN_BLOCK"]
            blocked.append(
                {
                    **row,
                    "validationCode": str(codes[0]),
                    "message": str(result.get("message") or "Unable to calculate safely."),
                }
            )

    dependency_only_fees = [
        item
        for item in calculated
        if item.get("assessmentRole") == "dependency-only"
    ]
    assessable_calculated = [
        item
        for item in calculated
        if item.get("assessmentRole") != "dependency-only"
    ]
    working_without_permit_additional_fees = [
        item
        for item in assessable_calculated
        if item["name"] in WWP_FEE_NAMES
    ]
    regular_permit_fees = [
        item
        for item in assessable_calculated
        if item["name"] not in WWP_FEE_NAMES
    ]
    regular_permit_fees_subtotal = round(
        sum(item["amount"] for item in regular_permit_fees),
        2,
    )
    working_without_permit_additional_fee_subtotal = round(
        sum(
            item["amount"]
            for item in working_without_permit_additional_fees
        ),
        2,
    )
    subtotal = round(
        regular_permit_fees_subtotal
        + working_without_permit_additional_fee_subtotal,
        2,
    )
    option = document.get("option") or {}
    configuration = document.get("configuration") or {}
    included = list(include_fee_names or [])
    assumptions = [
        "Manual-review fees are never included in the calculated subtotal.",
        "Inactive fees are excluded from the estimate.",
        "Working without a Permit is configured as a double permit fee. Its additional amount is listed separately.",
        "Validated Working without a Permit discipline fees interpret configured 100 as 100 percent of the linked base fee.",
    ]
    if dependency_only_fees:
        assumptions.insert(
            0,
            "Validated hidden calculation dependencies are not assessed as separate permit charges.",
        )
    if option.get("optionKey") == THIRD_SLICE_OPTION_KEY:
        assumptions.insert(
            0,
            "Explicit EnerGov fee adjustments are staff overrides and are not predicted by the automatic estimator.",
        )
    if applicability_mode == "validated-condition-rules":
        assumptions.insert(
            0,
            "Permit-stage conditions use the supported EnerGov rules for the selected option.",
        )
        assumptions.insert(
            1,
            "Inspection event fees are excluded from the initial permit estimate.",
        )
        assumptions.insert(
            2,
            "Unsupported applicability rules fail closed and are listed separately.",
        )
    else:
        assumptions.insert(
            0,
            "Conditional and event-contingent fees are treated as not applicable unless explicitly included in the scenario.",
        )

    estimate_status = classify_estimate_status(
        blocked,
        unresolved_applicability,
        staff_review,
        possible_additional,
    )

    return {
        "catalogVersion": document.get("catalogVersion"),
        "configurationAsOfDate": configuration.get("asOfDate"),
        "option": {
            "optionKey": option.get("optionKey"),
            "permitType": option.get("permitType"),
            "workClass": option.get("workClass"),
            "displayPermitType": display_permit_type(
                option.get("permitType")
            ),
            "displayWorkClass": display_work_class(
                option.get("workClass")
            ),
        },
        "scenario": {
            "applicabilityMode": applicability_mode,
            "includedConditionalOrEventFees": included,
            "fieldValues": field_values or {},
            "inputValues": input_values,
            "assumptions": assumptions,
        },
        "regularPermitFeesSubtotal": regular_permit_fees_subtotal,
        "workingWithoutPermitAdditionalFeeSubtotal": (
            working_without_permit_additional_fee_subtotal
        ),
        "subtotal": subtotal,
        "estimatedTotal": subtotal,
        "estimateStatus": estimate_status["code"],
        "estimateStatusLabel": estimate_status["label"],
        "requiresStaffReview": estimate_status["requiresStaffReview"],
        "staffReviewReasons": estimate_status["reasons"],
        "regularPermitFees": regular_permit_fees,
        "workingWithoutPermitAdditionalFees": (
            working_without_permit_additional_fees
        ),
        "calculatedFees": calculated,
        "dependencyOnlyFees": dependency_only_fees,
        "excludedFees": excluded,
        "possibleAdditionalFees": possible_additional,
        "blockedFees": blocked,
        "unresolvedApplicabilityFees": unresolved_applicability,
        "staffReviewFees": staff_review,
        "inactiveFees": inactive,
        "disclosures": list(document.get("disclosures") or []),
    }


def _repeat_display(item):
    repeat = item.get("repeat") or {}
    if not repeat.get("repeatable"):
        return ""
    if repeat.get("basis") != "event":
        return ""
    trigger = str(repeat.get("trigger") or "").strip()
    if trigger == "Additional reinspection event":
        return "Another reinspection may result in another fee."
    return trigger


def _money(value):
    return f"${float(value):,.2f}"


def _table_rows(items, amount=False, note=False):
    rows = []
    for item in items:
        cells = [
            html.escape(
                str(item.get("displayName") or item.get("name") or "Fee")
            )
        ]
        if amount:
            cells.append(html.escape(_money(item.get("amount") or 0)))
        if note:
            cells.append(html.escape(_repeat_display(item)))
        rows.append(
            "<tr>"
            + "".join(f"<td>{cell}</td>" for cell in cells)
            + "</tr>"
        )
    return "\n".join(rows)


def _display_name(item):
    return str(
        item.get("displayName")
        or display_fee_name(item.get("name"))
        or "Fee"
    )


def _review_reason(item):
    if item.get("validationCode"):
        return "Staff must confirm or calculate this fee amount."
    if item.get("code"):
        return "Staff needs more project information to determine whether this fee applies."
    return "This fee is determined or confirmed by staff."


def _review_display_items(report, omitted_names=None):
    omitted_names = set(omitted_names or [])
    grouped = {}

    def add(items, reason):
        for item in items or []:
            name = _display_name(item)
            if name in omitted_names:
                continue
            grouped.setdefault(name, set()).add(reason)

    add(
        report.get("staffReviewFees"),
        "This fee is determined or confirmed by staff.",
    )
    add(
        report.get("unresolvedApplicabilityFees"),
        "Staff needs more project information to determine whether this fee applies.",
    )
    add(
        report.get("blockedFees"),
        "Staff must confirm or calculate this fee amount.",
    )

    output = []
    for name in sorted(grouped, key=str.casefold):
        reasons = grouped[name]
        if len(reasons) > 1:
            reason = (
                "Staff must determine whether this fee applies and confirm "
                "the amount."
            )
        else:
            reason = next(iter(reasons))
        output.append({"displayName": name, "reason": reason})
    return output


def _review_rows(items):
    return "\n".join(
        "<tr>"
        f"<td>{html.escape(item['displayName'])}</td>"
        f"<td>{html.escape(item['reason'])}</td>"
        "</tr>"
        for item in items
    )


def render_html(report):
    option = report["option"]
    included_fees = list(report["regularPermitFees"]) + list(
        report["workingWithoutPermitAdditionalFees"]
    )
    show_included_notes = any(
        _repeat_display(item)
        for item in included_fees
    )
    included_rows = _table_rows(
        included_fees,
        amount=True,
        note=show_included_notes,
    )
    included_names = {
        _display_name(item)
        for item in included_fees
    }
    review_items = _review_display_items(
        report,
        omitted_names=included_names,
    )
    review_rows = _review_rows(review_items)
    review_count = len(review_items)
    review_names = {
        item["displayName"]
        for item in review_items
    }
    possible_names = sorted(
        {
            _display_name(item)
            for item in report.get("possibleAdditionalFees") or []
            if _display_name(item) not in included_names
            and _display_name(item) not in review_names
        },
        key=str.casefold,
    )
    possible_name_set = set(possible_names)
    excluded_names = sorted(
        {
            _display_name(item)
            for item in report.get("excludedFees") or []
            if _display_name(item) not in included_names
            and _display_name(item) not in review_names
            and _display_name(item) not in possible_name_set
        },
        key=str.casefold,
    )
    excluded_rows = "\n".join(
        f"<tr><td>{html.escape(name)}</td></tr>"
        for name in excluded_names
    )

    if review_count:
        status_html = (
            "<strong>Staff Review Needed.</strong> "
            f"The total below includes the fees this tool can calculate, "
            f"but staff must review {review_count} item"
            + ("" if review_count == 1 else "s")
            + " before the estimate is complete."
        )
    elif possible_names:
        status_html = (
            "<strong>Estimate Calculated.</strong> The total below includes "
            "the fees this tool can determine from the information entered. "
            "Other manually assessed fees may apply and are listed below."
        )
    else:
        status_html = (
            "<strong>Estimate Ready.</strong> Based on the information "
            "entered, the initial permit fees shown below can be calculated."
        )

    review_section = ""
    if review_count:
        review_section = f"""
<h2>Items Staff Still Need to Review</h2>
<p>These items are <strong>not included</strong> in the estimated total unless
an amount is shown elsewhere.</p>
<table>
<thead><tr><th>Fee or Item</th><th>What Needs Review</th></tr></thead>
<tbody>{review_rows}</tbody>
</table>
"""

    possible_section = ""
    if possible_names:
        possible_rows = "\n".join(
            f"<tr><td>{html.escape(name)}</td></tr>"
            for name in possible_names
        )
        possible_section = f"""
<h2>Other Fees That May Apply</h2>
<p>These fees are assessed manually and are not included in the estimated
total. They may apply depending on the project and its scope.</p>
<table>
<thead><tr><th>Fee</th></tr></thead>
<tbody>{possible_rows}</tbody>
</table>
"""

    extra_charge = ""
    if report["workingWithoutPermitAdditionalFeeSubtotal"] > 0:
        extra_charge = (
            "<p><strong>Includes additional fee for work started before a "
            "permit:</strong> "
            + _money(report["workingWithoutPermitAdditionalFeeSubtotal"])
            + "</p>"
        )

    other_fee_section = (
        "<p>These fees are not included based on the current project "
        "information, or because they apply only if a later event occurs.</p>"
        "<table><thead><tr><th>Fee</th></tr></thead>"
        f"<tbody>{excluded_rows}</tbody></table>"
        if excluded_rows
        else "<p>No other fees are excluded from this estimate.</p>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Permit Fee Estimate</title>
<style>
:root {{ font-family: Arial, sans-serif; color: #202124; background: #fff; }}
body {{ margin: 0; }}
main {{ max-width: 960px; margin: 0 auto; padding: 2rem 1rem 4rem; }}
h1 {{ margin-bottom: .25rem; }}
h2 {{ margin-top: 2rem; margin-bottom: .5rem; }}
.subtitle {{ color: #5f6368; margin-top: 0; }}
.total {{ margin: 1.25rem 0; padding: 1rem 1.25rem; background: #f2f5f7;
  border-radius: 6px; font-size: 1.6rem; font-weight: 700; }}
.notice {{ border-left: 4px solid #5f6368; background: #f7f8f9;
  padding: .9rem 1rem; margin: 1.25rem 0; }}
table {{ width: 100%; border-collapse: collapse; margin: .75rem 0 1.5rem; }}
th, td {{ border-bottom: 1px solid #d9dde1; padding: .65rem;
  text-align: left; vertical-align: top; }}
th {{ background: #f7f8f9; }}
td:nth-child(2) {{ white-space: nowrap; }}
details {{ margin-top: 2rem; border-top: 1px solid #d9dde1; padding-top: 1rem; }}
.meta {{ color: #5f6368; font-size: .92rem; }}
</style>
</head>
<body>
<main>
<h1>Permit Fee Estimate</h1>
<p class="subtitle"><strong>{html.escape(str(option.get('displayPermitType') or option['permitType']))}</strong>
&middot; {html.escape(str(option.get('displayWorkClass') or option['workClass']))}</p>

<div class="notice">{status_html}</div>

<div class="total">Estimated Initial Permit Fees: {_money(report['estimatedTotal'])}</div>
{extra_charge}

<h2>Fees Included in This Estimate</h2>
<table>
<thead><tr><th>Fee</th><th>Amount</th>{'<th>Notes</th>' if show_included_notes else ''}</tr></thead>
<tbody>{included_rows}</tbody>
</table>

{review_section}
{possible_section}

<p class="meta">This estimate covers the initial permit fees that can be
determined from the information provided. Later charges, such as reinspection
fees, are not included unless they actually occur.</p>

<details>
<summary>More Details</summary>
<h3>Other Fees Not Included in This Estimate</h3>
{other_fee_section}
<p class="meta">Fee schedule date: {html.escape(str(report.get('configurationAsOfDate') or 'unknown'))}.</p>
</details>
</main>
</body>
</html>
"""


def write_report(report, html_path: Path, json_path: Path):
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(report), encoding="utf-8")
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Render the first fail-closed estimator prototype report."
    )
    parser.add_argument("--catalog-dir", default=DEFAULT_CATALOG)
    parser.add_argument("--option-key", default=DEFAULT_OPTION_KEY)
    parser.add_argument(
        "--include-fee",
        action="append",
        default=[],
        help="Exact unique conditional/event fee name to include. Repeat as needed.",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Numeric project input in KEY=VALUE form. Repeat as needed.",
    )
    parser.add_argument(
        "--ele-contractor",
        choices=("yes", "no"),
        default="yes",
        help="Current EnerGov Ele_Contractor picklist value for the prototype.",
    )
    parser.add_argument(
        "--mec-contractor",
        choices=("yes", "no"),
        default="yes",
        help="Current EnerGov Mec_Contractor picklist value for the prototype.",
    )
    parser.add_argument(
        "--working-without-permit",
        choices=("yes", "no"),
        default="no",
        help="Current EnerGov WorkingwithoutaPermit value for the prototype.",
    )
    parser.add_argument(
        "--both-mech-and-ele",
        choices=("yes", "no"),
        default="yes",
        help=(
            "Current EnerGov Both_Mech_and_Ele choice for the second "
            "prototype slice."
        ),
    )
    parser.add_argument(
        "--number-appliances",
        type=float,
        help="NumberAppliances value required by the second prototype slice.",
    )
    parser.add_argument(
        "--valuation-value",
        type=float,
        help="Permit valuation required by the third prototype slice.",
    )
    parser.add_argument("--html-output", default=DEFAULT_HTML)
    parser.add_argument("--json-output", default=DEFAULT_JSON)
    args = parser.parse_args()

    document = load_option(Path(args.catalog_dir), args.option_key)
    input_values = parse_input_values(args.input)

    if args.option_key == SECOND_SLICE_OPTION_KEY:
        if args.number_appliances is None:
            parser.error(
                "--number-appliances is required for the second prototype slice"
            )
        input_values["NumberAppliances"] = args.number_appliances
        field_values = second_slice_field_values(
            args.both_mech_and_ele,
            args.working_without_permit == "yes",
        )
    elif args.option_key == THIRD_SLICE_OPTION_KEY:
        if args.valuation_value is None:
            parser.error(
                "--valuation-value is required for the third prototype slice"
            )
        if args.valuation_value < 0:
            parser.error("--valuation-value cannot be negative")
        input_values["ValuationValue"] = args.valuation_value
        field_values = third_slice_field_values(
            args.working_without_permit == "yes",
        )
    else:
        field_values = prototype_field_values(
            args.ele_contractor,
            args.mec_contractor,
            args.working_without_permit == "yes",
        )

    report = build_report_model(
        document,
        include_fee_names=args.include_fee,
        input_values=input_values,
        field_values=field_values,
    )
    html_path = Path(args.html_output)
    json_path = Path(args.json_output)
    write_report(report, html_path, json_path)

    review_count = (
        len(report["staffReviewFees"])
        + len(report["blockedFees"])
        + len(report.get("unresolvedApplicabilityFees") or [])
    )
    print("PERMIT FEE ESTIMATE")
    print(
        "Permit: "
        f"{report['option']['displayPermitType']} / "
        f"{report['option']['displayWorkClass']}"
    )
    print(
        "Estimated initial permit fees: "
        f"{_money(report['estimatedTotal'])}"
    )
    print(f"Status: {report['estimateStatusLabel']}")
    print(
        "Fees included: "
        f"{len(report['regularPermitFees']) + len(report['workingWithoutPermitAdditionalFees'])}"
    )
    print(f"Items staff still needs to review: {review_count}")
    print(f"HTML report: {html_path}")
    print(f"JSON data: {json_path}")


if __name__ == "__main__":
    main()
