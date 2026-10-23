from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


DEFAULT_CSV = "data/permit_family_fee_detail.csv"
DEFAULT_COHORT_CSV = "data/permit_family_cohort.csv"
DEFAULT_TEMPLATES = "output/permit_fee_templates_clean.json"
DEFAULT_OUTPUT = "output/permit_fee_usage_patterns.json"

REQUIRED_COLUMNS = {
    "ParentPermitID",
    "ParentPermitType",
    "ParentWorkClass",
    "ParentAppliedDate",
    "FeeOwnerPermitID",
    "FeeOwnerRole",
    "RelationshipFamily",
    "FeeOwnerPermitType",
    "FeeOwnerWorkClass",
    "ComputedFeeID",
    "FeeTemplateFeeID",
    "FeeName",
    "ComputationType",
    "ComputedAmount",
}

COHORT_REQUIRED_COLUMNS = {
    "ParentPermitID",
    "ParentPermitType",
    "ParentWorkClass",
    "ParentAppliedDate",
    "FeeOwnerPermitID",
    "FeeOwnerRole",
    "RelationshipFamily",
    "FeeOwnerPermitType",
    "FeeOwnerWorkClass",
    "FeeOwnerHasActiveFee",
    "ActiveFeeInstances",
    "FamilyMemberCount",
    "RelatedPermitCount",
    "FamilyActiveFeeInstances",
    "FamilyHasActiveFee",
}


def text(value):
    value = str(value or "").strip()
    return value or None


def canonical(value):
    return " ".join(str(value or "").split()).casefold()


def decimal_value(value):
    try:
        return Decimal(str(value or "0").strip() or "0")
    except InvalidOperation:
        return Decimal("0")


def integer_value(value):
    raw = str(value or "").strip().casefold()
    if raw in ("true", "yes"):
        return 1
    if raw in ("false", "no", ""):
        return 0
    try:
        return int(Decimal(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Expected an integer or Boolean value, got {value!r}.")


def stable_id_digest(value):
    return int.from_bytes(
        hashlib.sha256(value.encode("utf-8")).digest(),
        byteorder="big",
    )


def period_values(value, period_grain):
    raw = text(value)
    if not raw or len(raw) < 7:
        return {}
    result = {}
    if period_grain in ("year", "both"):
        result["year"] = raw[:4]
    if period_grain in ("month", "both"):
        result["month"] = raw[:7]
    return result


def fee_key(row):
    template_fee_id = text(row["FeeTemplateFeeID"])
    if template_fee_id:
        return ("FeeTemplateFeeID", template_fee_id)
    return (
        "Fallback",
        canonical(row["FeeName"]),
        canonical(row["ComputationType"]),
    )


def new_volume():
    return {
        "families": 0,
        "owners": 0,
        "instances": 0,
        "amount": Decimal("0"),
    }


def add_volume(target, families, owners, instances, amount):
    target["families"] += families
    target["owners"] += owners
    target["instances"] += instances
    target["amount"] += amount


def new_fee_stats(template_fee_id=None):
    return {
        "feeTemplateFeeId": template_fee_id,
        "names": Counter(),
        "computationTypes": Counter(),
        "overall": new_volume(),
        "periods": {
            "year": defaultdict(new_volume),
            "month": defaultdict(new_volume),
        },
    }


def new_related_stats(relationship_family, permit_type, work_class):
    return {
        "relationshipFamily": relationship_family,
        "permitType": permit_type,
        "workClass": work_class,
        "families": 0,
        "permits": 0,
        "familiesWithActiveFee": 0,
        "permitsWithActiveFee": 0,
        "periods": {
            "year": defaultdict(
                lambda: {
                    "families": 0,
                    "permits": 0,
                    "familiesWithActiveFee": 0,
                    "permitsWithActiveFee": 0,
                }
            ),
            "month": defaultdict(
                lambda: {
                    "families": 0,
                    "permits": 0,
                    "familiesWithActiveFee": 0,
                    "permitsWithActiveFee": 0,
                }
            ),
        },
        "fees": {},
    }


def new_parent_stats(permit_type, work_class):
    return {
        "permitType": permit_type,
        "workClass": work_class,
        "families": 0,
        "firstAppliedDate": None,
        "lastAppliedDate": None,
        "periods": {
            "year": Counter(),
            "month": Counter(),
        },
        "parentFees": {},
        "relatedPermits": {},
    }


def load_template_index(path):
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)

    index = defaultdict(list)
    for option in document.get("permitOptions") or []:
        option_summary = {
            "key": option.get("key"),
            "permitTypeId": option.get("permitTypeId"),
            "workClassId": option.get("workClassId"),
            "feeTemplateId": option.get("feeTemplateId"),
            "feeTemplate": option.get("feeTemplate"),
            "fees": {},
        }
        for fee in option.get("fees") or []:
            template_fee_id = text(fee.get("feeTemplateFeeId"))
            if not template_fee_id:
                continue
            option_summary["fees"][template_fee_id] = {
                "name": fee.get("name"),
                "classification": fee.get("classification"),
                "estimateMode": fee.get("estimateMode"),
                "selectable": fee.get("selectable"),
                "canAutoEstimate": fee.get("canAutoEstimate"),
            }
        index[
            (
                canonical(option.get("permitType")),
                canonical(option.get("workClass")),
            )
        ].append(option_summary)
    return index, document.get("asOfDate"), document.get("schemaVersion")


def option_match(template_index, permit_type, work_class):
    matches = template_index.get(
        (canonical(permit_type), canonical(work_class)),
        [],
    )
    if not matches:
        return {"status": "Unmatched"}, None
    if len(matches) > 1:
        return {"status": "Ambiguous", "matchCount": len(matches)}, None
    option = matches[0]
    return (
        {
            "status": "Matched",
            "optionKey": option.get("key"),
            "permitTypeId": option.get("permitTypeId"),
            "workClassId": option.get("workClassId"),
            "feeTemplateId": option.get("feeTemplateId"),
            "feeTemplate": option.get("feeTemplate"),
        },
        option,
    )


def configuration_fee_match(option, template_fee_id):
    if option is None:
        return {"status": "Permit option not matched"}
    if not template_fee_id:
        return {"status": "Missing FeeTemplateFeeID"}
    fee = option["fees"].get(template_fee_id)
    if fee is None:
        return {"status": "Fee assignment not in template snapshot"}
    return {"status": "Matched", **fee}


def new_family(row, period_grain):
    return {
        "parentId": text(row["ParentPermitID"]),
        "permitType": text(row["ParentPermitType"]),
        "workClass": text(row["ParentWorkClass"]),
        "appliedDate": text(row["ParentAppliedDate"]),
        "periods": period_values(row["ParentAppliedDate"], period_grain),
        "computedFeeIds": set(),
        "parentFees": {},
        "relatedOwners": defaultdict(set),
        "relatedFees": {},
    }


def new_family_fee(template_fee_id):
    return {
        "feeTemplateFeeId": template_fee_id,
        "names": Counter(),
        "computationTypes": Counter(),
        "owners": set(),
        "instances": 0,
        "amount": Decimal("0"),
    }


def add_family_row(family, row, diagnostics):
    computed_fee_id = text(row["ComputedFeeID"])
    if computed_fee_id and computed_fee_id in family["computedFeeIds"]:
        diagnostics["duplicateRowsWithinFamily"] += 1
        return
    if computed_fee_id:
        family["computedFeeIds"].add(computed_fee_id)

    role = text(row["FeeOwnerRole"])
    key = fee_key(row)
    template_fee_id = text(row["FeeTemplateFeeID"])
    owner_id = text(row["FeeOwnerPermitID"])
    amount = decimal_value(row["ComputedAmount"])

    if role == "Parent":
        fee = family["parentFees"].setdefault(
            key,
            new_family_fee(template_fee_id),
        )
    elif role == "Child":
        related_key = (
            canonical(row["RelationshipFamily"]),
            canonical(row["FeeOwnerPermitType"]),
            canonical(row["FeeOwnerWorkClass"]),
        )
        related_labels = (
            text(row["RelationshipFamily"]),
            text(row["FeeOwnerPermitType"]),
            text(row["FeeOwnerWorkClass"]),
        )
        related_owners = family["relatedOwners"][(
            related_key,
            related_labels,
        )]
        if owner_id:
            related_owners.add(owner_id)
        fee = family["relatedFees"].setdefault(
            (related_key, related_labels, key),
            new_family_fee(template_fee_id),
        )
    else:
        diagnostics["unknownFeeOwnerRoles"][role or "(blank)"] += 1
        return

    if owner_id:
        fee["owners"].add(owner_id)
    fee_name = text(row["FeeName"])
    computation_type = text(row["ComputationType"])
    if fee_name:
        fee["names"][fee_name] += 1
    if computation_type:
        fee["computationTypes"][computation_type] += 1
    fee["instances"] += 1
    fee["amount"] += amount


def update_fee_stats(target, family_fee, periods):
    target["names"].update(family_fee["names"])
    target["computationTypes"].update(
        family_fee["computationTypes"]
    )
    owners = len(family_fee["owners"])
    add_volume(
        target["overall"],
        families=1,
        owners=owners,
        instances=family_fee["instances"],
        amount=family_fee["amount"],
    )
    for grain, period in periods.items():
        add_volume(
            target["periods"][grain][period],
            families=1,
            owners=owners,
            instances=family_fee["instances"],
            amount=family_fee["amount"],
        )


def flush_family(family, parents):
    parent_key = (
        canonical(family["permitType"]),
        canonical(family["workClass"]),
    )
    parent = parents.setdefault(
        parent_key,
        new_parent_stats(family["permitType"], family["workClass"]),
    )
    parent["families"] += 1
    applied_date = family["appliedDate"]
    if applied_date:
        date_only = applied_date[:10]
        if (
            parent["firstAppliedDate"] is None
            or date_only < parent["firstAppliedDate"]
        ):
            parent["firstAppliedDate"] = date_only
        if (
            parent["lastAppliedDate"] is None
            or date_only > parent["lastAppliedDate"]
        ):
            parent["lastAppliedDate"] = date_only
    for grain, period in family["periods"].items():
        parent["periods"][grain][period] += 1

    for key, family_fee in family["parentFees"].items():
        fee = parent["parentFees"].setdefault(
            key,
            new_fee_stats(family_fee["feeTemplateFeeId"]),
        )
        update_fee_stats(fee, family_fee, family["periods"])

    related_lookup = {}
    for (related_key, labels), owners in family["relatedOwners"].items():
        relation = parent["relatedPermits"].setdefault(
            related_key,
            new_related_stats(*labels),
        )
        related_lookup[related_key] = relation
        relation["families"] += 1
        relation["permits"] += len(owners)
        for grain, period in family["periods"].items():
            values = relation["periods"][grain][period]
            values["families"] += 1
            values["permits"] += len(owners)

    for (related_key, labels, key), family_fee in family[
        "relatedFees"
    ].items():
        relation = related_lookup.get(related_key)
        if relation is None:
            relation = parent["relatedPermits"].setdefault(
                related_key,
                new_related_stats(*labels),
            )
        fee = relation["fees"].setdefault(
            key,
            new_fee_stats(family_fee["feeTemplateFeeId"]),
        )
        update_fee_stats(fee, family_fee, family["periods"])


def configure_csv_field_limit():
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


def read_history(csv_path, period_grain):
    configure_csv_field_limit()
    parents = {}
    parent_digest = 0
    diagnostics = {
        "rowsRead": 0,
        "parentFamilies": 0,
        "duplicateRowsWithinFamily": 0,
        "unknownFeeOwnerRoles": Counter(),
        "missingParentAppliedDateFamilies": 0,
    }
    completed_parent_ids = set()
    current = None

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("Historical CSV is empty.") from exc
        positions = {name: index for index, name in enumerate(header)}
        missing = sorted(REQUIRED_COLUMNS - positions.keys())
        if missing:
            raise ValueError(
                "Historical CSV is missing columns: " + ", ".join(missing)
            )

        for values in reader:
            diagnostics["rowsRead"] += 1
            row = {name: values[positions[name]] for name in REQUIRED_COLUMNS}
            parent_id = text(row["ParentPermitID"])
            if not parent_id:
                raise ValueError(
                    f"CSV row {diagnostics['rowsRead'] + 1:,} has no "
                    "ParentPermitID."
                )

            if current is None:
                current = new_family(row, period_grain)
            elif parent_id != current["parentId"]:
                flush_family(current, parents)
                parent_digest ^= stable_id_digest(current["parentId"])
                diagnostics["parentFamilies"] += 1
                if not current["periods"]:
                    diagnostics["missingParentAppliedDateFamilies"] += 1
                completed_parent_ids.add(current["parentId"])
                if parent_id in completed_parent_ids:
                    raise ValueError(
                        "ParentPermitID rows are not contiguous. The mapper "
                        "requires the ORDER BY in permit_family_fee_detail.sql."
                    )
                current = new_family(row, period_grain)

            add_family_row(current, row, diagnostics)
            if diagnostics["rowsRead"] % 100000 == 0:
                print(
                    f"Read {diagnostics['rowsRead']:,} rows; "
                    f"completed {diagnostics['parentFamilies']:,} families..."
                )

    if current is not None:
        flush_family(current, parents)
        parent_digest ^= stable_id_digest(current["parentId"])
        diagnostics["parentFamilies"] += 1
        if not current["periods"]:
            diagnostics["missingParentAppliedDateFamilies"] += 1
    diagnostics["parentFamilyDigest"] = f"{parent_digest:064x}"
    return parents, diagnostics


def new_cohort_family(row, period_grain):
    return {
        "parentId": text(row["ParentPermitID"]),
        "permitType": text(row["ParentPermitType"]),
        "workClass": text(row["ParentWorkClass"]),
        "appliedDate": text(row["ParentAppliedDate"]),
        "periods": period_values(row["ParentAppliedDate"], period_grain),
        "members": set(),
        "parentRows": 0,
        "relatedOwners": defaultdict(set),
        "relatedActiveOwners": defaultdict(set),
        "expectedMemberCounts": set(),
        "expectedRelatedCounts": set(),
        "familyActiveFeeInstances": set(),
        "familyHasActiveFee": set(),
        "memberActiveFeeInstances": 0,
        "relatedWithoutActiveFee": 0,
    }


def add_cohort_row(family, row, row_number):
    parent_id = text(row["ParentPermitID"])
    owner_id = text(row["FeeOwnerPermitID"])
    if not owner_id:
        raise ValueError(f"Cohort CSV row {row_number:,} has no FeeOwnerPermitID.")
    if owner_id in family["members"]:
        raise ValueError(
            f"Cohort CSV repeats parent/member pair {parent_id}|{owner_id}."
        )
    family["members"].add(owner_id)

    role = text(row["FeeOwnerRole"])
    owner_instances = integer_value(row["ActiveFeeInstances"])
    owner_has_fee = integer_value(row["FeeOwnerHasActiveFee"])
    if (owner_instances > 0) != bool(owner_has_fee):
        raise ValueError(
            f"Cohort CSV has inconsistent fee diagnostics for "
            f"{parent_id}|{owner_id}."
        )

    if role == "Parent":
        family["parentRows"] += 1
        if owner_id != parent_id:
            raise ValueError(
                f"Cohort parent row {parent_id} uses owner {owner_id}."
            )
    elif role == "Child":
        related_key = (
            canonical(row["RelationshipFamily"]),
            canonical(row["FeeOwnerPermitType"]),
            canonical(row["FeeOwnerWorkClass"]),
        )
        related_labels = (
            text(row["RelationshipFamily"]),
            text(row["FeeOwnerPermitType"]),
            text(row["FeeOwnerWorkClass"]),
        )
        relation_identity = (related_key, related_labels)
        family["relatedOwners"][relation_identity].add(owner_id)
        if owner_has_fee:
            family["relatedActiveOwners"][relation_identity].add(owner_id)
        else:
            family["relatedWithoutActiveFee"] += 1
    else:
        raise ValueError(
            f"Cohort CSV row {row_number:,} has unknown FeeOwnerRole "
            f"{role!r}."
        )

    family["expectedMemberCounts"].add(
        integer_value(row["FamilyMemberCount"])
    )
    family["expectedRelatedCounts"].add(
        integer_value(row["RelatedPermitCount"])
    )
    family["familyActiveFeeInstances"].add(
        integer_value(row["FamilyActiveFeeInstances"])
    )
    family["familyHasActiveFee"].add(
        integer_value(row["FamilyHasActiveFee"])
    )
    family["memberActiveFeeInstances"] += owner_instances


def flush_cohort_family(family, parents, diagnostics, active_digest):
    parent_id = family["parentId"]
    member_count = len(family["members"])
    related_count = sum(
        len(owners) for owners in family["relatedOwners"].values()
    )
    active_instances = family["memberActiveFeeInstances"]
    expected_flag = int(active_instances > 0)

    if family["parentRows"] != 1:
        raise ValueError(
            f"Cohort family {parent_id} has {family['parentRows']} parent rows."
        )
    if family["expectedMemberCounts"] != {member_count}:
        raise ValueError(
            f"Cohort family {parent_id} has inconsistent FamilyMemberCount."
        )
    if family["expectedRelatedCounts"] != {related_count}:
        raise ValueError(
            f"Cohort family {parent_id} has inconsistent RelatedPermitCount."
        )
    if family["familyActiveFeeInstances"] != {active_instances}:
        raise ValueError(
            f"Cohort family {parent_id} has inconsistent active-fee counts."
        )
    if family["familyHasActiveFee"] != {expected_flag}:
        raise ValueError(
            f"Cohort family {parent_id} has inconsistent FamilyHasActiveFee."
        )

    parent_key = (
        canonical(family["permitType"]),
        canonical(family["workClass"]),
    )
    parent = parents.setdefault(
        parent_key,
        new_parent_stats(family["permitType"], family["workClass"]),
    )
    parent["families"] += 1
    applied_date = family["appliedDate"]
    if applied_date:
        date_only = applied_date[:10]
        if (
            parent["firstAppliedDate"] is None
            or date_only < parent["firstAppliedDate"]
        ):
            parent["firstAppliedDate"] = date_only
        if (
            parent["lastAppliedDate"] is None
            or date_only > parent["lastAppliedDate"]
        ):
            parent["lastAppliedDate"] = date_only
    else:
        diagnostics["missingParentAppliedDateFamilies"] += 1
    for grain, period in family["periods"].items():
        parent["periods"][grain][period] += 1

    for (related_key, labels), owners in family["relatedOwners"].items():
        relation = parent["relatedPermits"].setdefault(
            related_key,
            new_related_stats(*labels),
        )
        active_owners = family["relatedActiveOwners"].get(
            (related_key, labels),
            set(),
        )
        relation["families"] += 1
        relation["permits"] += len(owners)
        if active_owners:
            relation["familiesWithActiveFee"] += 1
            relation["permitsWithActiveFee"] += len(active_owners)
        for grain, period in family["periods"].items():
            values = relation["periods"][grain][period]
            values["families"] += 1
            values["permits"] += len(owners)
            if active_owners:
                values["familiesWithActiveFee"] += 1
                values["permitsWithActiveFee"] += len(active_owners)

    diagnostics["parentFamilies"] += 1
    diagnostics["relatedPermits"] += related_count
    diagnostics["relatedPermitsWithoutActiveFees"] += family[
        "relatedWithoutActiveFee"
    ]
    diagnostics["activeFeeInstances"] += active_instances
    if expected_flag:
        diagnostics["activeFeeParentFamilies"] += 1
        active_digest["value"] ^= stable_id_digest(parent_id)
    else:
        diagnostics["zeroFeeParentFamilies"] += 1


def read_cohort(csv_path, period_grain):
    configure_csv_field_limit()
    parents = {}
    active_digest = {"value": 0}
    diagnostics = {
        "rowsRead": 0,
        "parentFamilies": 0,
        "activeFeeParentFamilies": 0,
        "zeroFeeParentFamilies": 0,
        "relatedPermits": 0,
        "relatedPermitsWithoutActiveFees": 0,
        "activeFeeInstances": 0,
        "missingParentAppliedDateFamilies": 0,
    }
    completed_parent_ids = set()
    current = None

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("Cohort CSV is empty.") from exc
        positions = {name: index for index, name in enumerate(header)}
        missing = sorted(COHORT_REQUIRED_COLUMNS - positions.keys())
        if missing:
            raise ValueError(
                "Cohort CSV is missing columns: " + ", ".join(missing)
            )

        for values in reader:
            diagnostics["rowsRead"] += 1
            row = {
                name: values[positions[name]]
                for name in COHORT_REQUIRED_COLUMNS
            }
            parent_id = text(row["ParentPermitID"])
            if not parent_id:
                raise ValueError(
                    f"Cohort CSV row {diagnostics['rowsRead'] + 1:,} has no "
                    "ParentPermitID."
                )

            if current is None:
                current = new_cohort_family(row, period_grain)
            elif parent_id != current["parentId"]:
                flush_cohort_family(
                    current,
                    parents,
                    diagnostics,
                    active_digest,
                )
                completed_parent_ids.add(current["parentId"])
                if parent_id in completed_parent_ids:
                    raise ValueError(
                        "ParentPermitID rows are not contiguous. The cohort "
                        "must retain the query ORDER BY."
                    )
                current = new_cohort_family(row, period_grain)

            add_cohort_row(
                current,
                row,
                diagnostics["rowsRead"] + 1,
            )
            if diagnostics["rowsRead"] % 100000 == 0:
                print(
                    f"Read {diagnostics['rowsRead']:,} cohort rows; "
                    f"completed {diagnostics['parentFamilies']:,} families..."
                )

    if current is not None:
        flush_cohort_family(
            current,
            parents,
            diagnostics,
            active_digest,
        )
    diagnostics["activeParentFamilyDigest"] = (
        f"{active_digest['value']:064x}"
    )
    return parents, diagnostics


def merge_fee_history_into_cohort(cohort_parents, fee_parents):
    missing_parent_options = sorted(set(fee_parents) - set(cohort_parents))
    if missing_parent_options:
        raise ValueError(
            "Fee history contains permit options absent from the cohort: "
            + ", ".join(" / ".join(key) for key in missing_parent_options)
        )

    for parent_key, fee_parent in fee_parents.items():
        cohort_parent = cohort_parents[parent_key]
        cohort_parent["parentFees"] = fee_parent["parentFees"]
        for relation_key, fee_relation in fee_parent[
            "relatedPermits"
        ].items():
            cohort_relation = cohort_parent["relatedPermits"].get(relation_key)
            if cohort_relation is None:
                raise ValueError(
                    "Fee history contains a related-permit pattern absent "
                    "from the cohort for "
                    f"{fee_parent['permitType']} / "
                    f"{fee_parent['workClass']}."
                )
            cohort_relation["fees"] = fee_relation["fees"]
    return cohort_parents


def decimal_number(value, places="0.0001"):
    return float(value.quantize(Decimal(places)))


def preferred_label(counter):
    if not counter:
        return None, []
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    primary = ordered[0][0]
    variants = [
        {"value": value, "feeInstances": count}
        for value, count in ordered
        if value != primary
    ]
    return primary, variants


def volume_json(volume, parent_denominator, related_denominator=None):
    families = volume["families"]
    instances = volume["instances"]
    result = {
        "parentFamiliesCharged": families,
        "feeOwnerPermitsCharged": volume["owners"],
        "feeInstances": instances,
        "totalComputedAmount": decimal_number(volume["amount"]),
        "shareOfEligibleParentFamilies": (
            round(families / parent_denominator, 6)
            if parent_denominator
            else None
        ),
        "averageInstancesPerChargedFamily": (
            round(instances / families, 4) if families else None
        ),
        "averageComputedAmountPerInstance": (
            decimal_number(volume["amount"] / instances)
            if instances
            else None
        ),
    }
    if related_denominator is not None:
        result["shareOfFamiliesWithRelatedPermit"] = (
            round(families / related_denominator, 6)
            if related_denominator
            else None
        )
    return {key: value for key, value in result.items() if value is not None}


def period_fee_json(
    stats,
    grain,
    parent_periods,
    related_periods=None,
):
    output = []
    for period, volume in sorted(stats["periods"][grain].items()):
        related_denominator = None
        if related_periods is not None:
            related_denominator = related_periods.get(period, {}).get(
                "families",
                0,
            )
        output.append(
            {
                "period": period,
                **volume_json(
                    volume,
                    parent_periods.get(period, 0),
                    related_denominator,
                ),
            }
        )
    return output


def finalize_fee(
    stats,
    option,
    parent_denominator,
    parent_periods,
    period_grain,
    related_denominator=None,
    related_periods=None,
):
    fee_name, name_variants = preferred_label(stats["names"])
    computation_type, computation_variants = preferred_label(
        stats["computationTypes"]
    )
    result = {
        "feeTemplateFeeId": stats["feeTemplateFeeId"],
        "feeName": fee_name,
        "feeNameVariants": name_variants,
        "computationType": computation_type,
        "computationTypeVariants": computation_variants,
        "configurationMatch": configuration_fee_match(
            option,
            stats["feeTemplateFeeId"],
        ),
        "overall": volume_json(
            stats["overall"],
            parent_denominator,
            related_denominator,
        ),
    }
    if period_grain in ("year", "both"):
        result["byYear"] = period_fee_json(
            stats,
            "year",
            parent_periods["year"],
            related_periods["year"] if related_periods else None,
        )
    if period_grain in ("month", "both"):
        result["byMonth"] = period_fee_json(
            stats,
            "month",
            parent_periods["month"],
            related_periods["month"] if related_periods else None,
        )
    return result


def related_period_json(relation, grain, parent_periods):
    output = []
    for period, values in sorted(relation["periods"][grain].items()):
        parent_denominator = parent_periods.get(period, 0)
        output.append(
            {
                "period": period,
                "parentFamiliesWithRelatedPermit": values["families"],
                "relatedPermits": values["permits"],
                "parentFamiliesWithRelatedPermitAndActiveFee": values[
                    "familiesWithActiveFee"
                ],
                "relatedPermitsWithActiveFee": values[
                    "permitsWithActiveFee"
                ],
                "shareOfEligibleParentFamilies": (
                    round(values["families"] / parent_denominator, 6)
                    if parent_denominator
                    else None
                ),
                "shareOfEligibleParentFamiliesWithRelatedPermitAndActiveFee": (
                    round(
                        values["familiesWithActiveFee"] / parent_denominator,
                        6,
                    )
                    if parent_denominator
                    else None
                ),
                "shareOfRelatedPermitFamiliesWithActiveFee": (
                    round(
                        values["familiesWithActiveFee"] / values["families"],
                        6,
                    )
                    if values["families"]
                    else None
                ),
                "averageRelatedPermitsPerFamily": (
                    round(values["permits"] / values["families"], 4)
                    if values["families"]
                    else None
                ),
            }
        )
    return output


def finalize_related(
    relation,
    template_index,
    parent,
    period_grain,
):
    configuration_match, option = option_match(
        template_index,
        relation["permitType"],
        relation["workClass"],
    )
    result = {
        "relationshipFamily": relation["relationshipFamily"],
        "permitType": relation["permitType"],
        "workClass": relation["workClass"],
        "configurationMatch": configuration_match,
        "overall": {
            "parentFamiliesWithRelatedPermit": relation["families"],
            "relatedPermits": relation["permits"],
            "parentFamiliesWithRelatedPermitAndActiveFee": relation[
                "familiesWithActiveFee"
            ],
            "relatedPermitsWithActiveFee": relation["permitsWithActiveFee"],
            "shareOfEligibleParentFamilies": round(
                relation["families"] / parent["families"],
                6,
            ),
            "shareOfEligibleParentFamiliesWithRelatedPermitAndActiveFee": round(
                relation["familiesWithActiveFee"] / parent["families"],
                6,
            ),
            "shareOfRelatedPermitFamiliesWithActiveFee": round(
                relation["familiesWithActiveFee"] / relation["families"],
                6,
            ),
            "averageRelatedPermitsPerFamily": round(
                relation["permits"] / relation["families"],
                4,
            ),
        },
    }
    if period_grain in ("year", "both"):
        result["byYear"] = related_period_json(
            relation,
            "year",
            parent["periods"]["year"],
        )
    if period_grain in ("month", "both"):
        result["byMonth"] = related_period_json(
            relation,
            "month",
            parent["periods"]["month"],
        )

    fees = [
        finalize_fee(
            stats,
            option,
            parent["families"],
            parent["periods"],
            period_grain,
            related_denominator=relation["families"],
            related_periods=relation["periods"],
        )
        for stats in relation["fees"].values()
    ]
    fees.sort(
        key=lambda fee: (
            str(fee.get("feeName") or ""),
            str(fee.get("feeTemplateFeeId") or ""),
        )
    )
    result["fees"] = fees
    return result


def finalize_parent(parent, template_index, period_grain):
    configuration_match, option = option_match(
        template_index,
        parent["permitType"],
        parent["workClass"],
    )
    result = {
        "permitType": parent["permitType"],
        "workClass": parent["workClass"],
        "configurationMatch": configuration_match,
        "observed": {
            "parentFamilies": parent["families"],
            "firstParentAppliedDate": parent["firstAppliedDate"],
            "lastParentAppliedDate": parent["lastAppliedDate"],
        },
    }
    if period_grain in ("year", "both"):
        result["observed"]["byYear"] = [
            {"period": period, "parentFamilies": count}
            for period, count in sorted(parent["periods"]["year"].items())
        ]
    if period_grain in ("month", "both"):
        result["observed"]["byMonth"] = [
            {"period": period, "parentFamilies": count}
            for period, count in sorted(parent["periods"]["month"].items())
        ]

    parent_fees = [
        finalize_fee(
            stats,
            option,
            parent["families"],
            parent["periods"],
            period_grain,
        )
        for stats in parent["parentFees"].values()
    ]
    parent_fees.sort(
        key=lambda fee: (
            str(fee.get("feeName") or ""),
            str(fee.get("feeTemplateFeeId") or ""),
        )
    )
    result["parentFees"] = parent_fees

    related = [
        finalize_related(
            relation,
            template_index,
            parent,
            period_grain,
        )
        for relation in parent["relatedPermits"].values()
    ]
    related.sort(
        key=lambda item: (
            str(item.get("relationshipFamily") or ""),
            str(item.get("permitType") or ""),
            str(item.get("workClass") or ""),
        )
    )
    result["relatedPermitPatterns"] = related
    return result


def summarize_matches(parent_patterns):
    option_counts = Counter()
    fee_counts = Counter()
    related_patterns = 0
    fee_patterns = 0

    for parent in parent_patterns:
        option_counts[parent["configurationMatch"]["status"]] += 1
        for fee in parent.get("parentFees") or []:
            fee_counts[fee["configurationMatch"]["status"]] += 1
            fee_patterns += 1
        for relation in parent.get("relatedPermitPatterns") or []:
            related_patterns += 1
            option_counts[relation["configurationMatch"]["status"]] += 1
            for fee in relation.get("fees") or []:
                fee_counts[fee["configurationMatch"]["status"]] += 1
                fee_patterns += 1

    return {
        "parentPermitTypeWorkClassPatterns": len(parent_patterns),
        "relatedPermitTypeWorkClassPatterns": related_patterns,
        "historicalFeePatterns": fee_patterns,
        "permitOptionMatchCounts": dict(sorted(option_counts.items())),
        "feeAssignmentMatchCounts": dict(sorted(fee_counts.items())),
    }


def build_document(
    csv_path,
    cohort_path,
    template_path,
    period_grain,
):
    template_index, template_as_of, template_schema = load_template_index(
        template_path
    )
    fee_parents, fee_diagnostics = read_history(csv_path, period_grain)
    cohort_parents, cohort_diagnostics = read_cohort(
        cohort_path,
        period_grain,
    )
    fee_instances = (
        fee_diagnostics["rowsRead"]
        - fee_diagnostics["duplicateRowsWithinFamily"]
    )
    alignment_errors = []
    if fee_instances != cohort_diagnostics["activeFeeInstances"]:
        alignment_errors.append(
            "active fee instances "
            f"{fee_instances:,} != "
            f"{cohort_diagnostics['activeFeeInstances']:,}"
        )
    if (
        fee_diagnostics["parentFamilies"]
        != cohort_diagnostics["activeFeeParentFamilies"]
    ):
        alignment_errors.append(
            "active-fee parent families "
            f"{fee_diagnostics['parentFamilies']:,} != "
            f"{cohort_diagnostics['activeFeeParentFamilies']:,}"
        )
    if (
        fee_diagnostics["parentFamilyDigest"]
        != cohort_diagnostics["activeParentFamilyDigest"]
    ):
        alignment_errors.append("active-fee parent IDs do not match")
    if alignment_errors:
        raise ValueError(
            "Fee-detail and cohort snapshots are not aligned ("
            + "; ".join(alignment_errors)
            + "). Refresh both SQL extracts from the same database snapshot "
            "before calculating occurrence rates."
        )

    parents = merge_fee_history_into_cohort(
        cohort_parents,
        fee_parents,
    )
    parent_patterns = [
        finalize_parent(parent, template_index, period_grain)
        for parent in parents.values()
    ]
    parent_patterns.sort(
        key=lambda item: (
            str(item.get("permitType") or ""),
            str(item.get("workClass") or ""),
        )
    )
    match_summary = summarize_matches(parent_patterns)
    dates = [
        value
        for parent in parents.values()
        for value in (
            parent["firstAppliedDate"],
            parent["lastAppliedDate"],
        )
        if value
    ]

    return {
        "schemaVersion": "2.0",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "historicalFeeCsv": csv_path.name,
            "historicalCohortCsv": cohort_path.name,
            "feeTemplateJson": template_path.name,
            "feeTemplateSchemaVersion": template_schema,
            "feeTemplateAsOfDate": template_as_of,
            "periodBasis": "ParentAppliedDate",
            "periodGrain": period_grain,
            "historicalStartDate": min(dates) if dates else None,
            "historicalEndDate": max(dates) if dates else None,
            "historicalScope": (
                "All issued or finalized qualifying parent permits and their "
                "validated direct related permits from "
                "15_permit_family_cohort.sql, combined with active "
                "PMPERMITFEE computed-fee rows from "
                "permit_family_fee_detail.sql."
            ),
            "snapshotAlignment": (
                "Exact active-family IDs and active fee-instance counts match "
                "between the cohort and fee-detail extracts."
            ),
        },
        "interpretation": {
            "denominator": (
                "All eligible parent permit families in the cohort for the "
                "selected permit type, work class, and observation period, "
                "including families with no active fee or related permit."
            ),
            "parentFees": (
                "Fees owned by the selected parent permit itself."
            ),
            "relatedPermitPatterns": (
                "Separately created permits connected through validated "
                "direct workflow relationships. Related permits remain in "
                "the denominator even when they have no active fee. These "
                "are not API child fee templates."
            ),
            "configurationMatching": (
                "Permit options match on normalized permit type/work class "
                "names. Fee assignments match on FeeTemplateFeeID. Historical "
                "fees may not exist in the current as-of template snapshot."
            ),
            "usageVsRule": (
                "Historical frequency describes observed practice and does "
                "not establish whether a fee is legally or logically required."
            ),
        },
        "summary": {
            "historicalFeeRowsRead": fee_diagnostics["rowsRead"],
            "cohortRowsRead": cohort_diagnostics["rowsRead"],
            "eligibleParentFamilies": cohort_diagnostics["parentFamilies"],
            "activeFeeParentFamilies": cohort_diagnostics[
                "activeFeeParentFamilies"
            ],
            "zeroFeeParentFamilies": cohort_diagnostics[
                "zeroFeeParentFamilies"
            ],
            "relatedPermits": cohort_diagnostics["relatedPermits"],
            "relatedPermitsWithoutActiveFees": cohort_diagnostics[
                "relatedPermitsWithoutActiveFees"
            ],
            "activeFeeInstances": cohort_diagnostics["activeFeeInstances"],
            "duplicateRowsSkippedWithinFamily": fee_diagnostics[
                "duplicateRowsWithinFamily"
            ],
            "familiesMissingParentAppliedDate": cohort_diagnostics[
                "missingParentAppliedDateFamilies"
            ],
            "unknownFeeOwnerRoles": dict(
                sorted(fee_diagnostics["unknownFeeOwnerRoles"].items())
            ),
            **match_summary,
        },
        "parentPermitPatterns": parent_patterns,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Combine permit-family fee detail with an all-parent cohort to "
            "produce historical parent and related-permit fee patterns."
        )
    )
    parser.add_argument(
        "csv_file",
        nargs="?",
        default=DEFAULT_CSV,
        help=f"Historical family-fee CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--cohort-csv",
        default=DEFAULT_COHORT_CSV,
        help=(
            "All-parent family cohort CSV "
            f"(default: {DEFAULT_COHORT_CSV})"
        ),
    )
    parser.add_argument(
        "--templates-json",
        default=DEFAULT_TEMPLATES,
        help=f"Clean template JSON (default: {DEFAULT_TEMPLATES})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Usage-pattern JSON destination (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--period-grain",
        choices=("year", "month", "both"),
        default="both",
        help="Historical volume periods to include (default: both)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Write compact JSON instead of indented JSON.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    cohort_path = Path(args.cohort_csv)
    template_path = Path(args.templates_json)
    output_path = Path(args.output)
    if not csv_path.exists():
        raise FileNotFoundError(f"Could not find {csv_path}")
    if not cohort_path.exists():
        raise FileNotFoundError(f"Could not find {cohort_path}")
    if not template_path.exists():
        raise FileNotFoundError(f"Could not find {template_path}")
    if output_path.resolve() in {
        csv_path.resolve(),
        cohort_path.resolve(),
        template_path.resolve(),
    }:
        raise ValueError("Output must not overwrite an input file.")

    document = build_document(
        csv_path,
        cohort_path,
        template_path,
        args.period_grain,
    )
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
    print(f"Historical fee rows read:  {summary['historicalFeeRowsRead']:,}")
    print(f"Cohort rows read:          {summary['cohortRowsRead']:,}")
    print(f"Eligible parent families:  {summary['eligibleParentFamilies']:,}")
    print(f"Zero-fee parent families:  {summary['zeroFeeParentFamilies']:,}")
    print(f"Related permits:           {summary['relatedPermits']:,}")
    print(
        "Related permits, no fee:   "
        f"{summary['relatedPermitsWithoutActiveFees']:,}"
    )
    print(
        "Historical fee patterns:  "
        f"{summary['historicalFeePatterns']:,} total parent/related patterns"
    )
    print(
        "Permit option matches:     "
        + json.dumps(summary["permitOptionMatchCounts"], sort_keys=True)
    )
    print(
        "Fee assignment matches:    "
        + json.dumps(summary["feeAssignmentMatchCounts"], sort_keys=True)
    )
    print(
        "Duplicate family rows:     "
        f"{summary['duplicateRowsSkippedWithinFamily']:,}"
    )
    print(f"Usage JSON: {output_path}")


if __name__ == "__main__":
    main()
