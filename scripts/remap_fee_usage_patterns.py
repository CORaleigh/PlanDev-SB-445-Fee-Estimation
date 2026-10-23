from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_USAGE = "output/permit_fee_usage_patterns.json"
DEFAULT_TEMPLATES = "output-prod/permit_fee_templates_clean.json"
DEFAULT_OUTPUT = "output-prod/permit_fee_usage_patterns_remapped.json"
DEFAULT_AUDIT = "output-prod/permit_fee_configuration_match_audit.csv"


def text(value):
    value = str(value or "").strip()
    return value or None


def canonical(value):
    return " ".join(str(value or "").split()).casefold()


def load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def fee_summary(fee):
    return {
        "feeTemplateFeeId": fee.get("feeTemplateFeeId"),
        "name": fee.get("name"),
        "classification": fee.get("classification"),
        "estimateMode": fee.get("estimateMode"),
        "selectable": fee.get("selectable"),
        "canAutoEstimate": fee.get("canAutoEstimate"),
    }


def option_summary(option):
    return {
        "optionKey": option.get("key"),
        "permitTypeId": option.get("permitTypeId"),
        "permitType": option.get("permitType"),
        "workClassId": option.get("workClassId"),
        "workClass": option.get("workClass"),
        "feeTemplateId": option.get("feeTemplateId"),
        "feeTemplate": option.get("feeTemplate"),
    }


def build_template_indexes(document):
    option_index = defaultdict(list)
    fee_id_index = defaultdict(list)

    for option in document.get("permitOptions") or []:
        option["_feesById"] = {}
        option["_feesByName"] = defaultdict(list)
        for fee in option.get("fees") or []:
            fee_id = text(fee.get("feeTemplateFeeId"))
            if fee_id:
                option["_feesById"][fee_id] = fee
                fee_id_index[fee_id].append((option, fee))
            fee_name = canonical(fee.get("name"))
            if fee_name:
                option["_feesByName"][fee_name].append(fee)

        option_index[
            (
                canonical(option.get("permitType")),
                canonical(option.get("workClass")),
            )
        ].append(option)

    return option_index, fee_id_index


def load_inspection_fee_index(path):
    by_id = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            fee_id = text(row.get("FeeTemplateFeeID"))
            if not fee_id:
                continue
            entry = by_id.setdefault(
                fee_id,
                {
                    "feeTemplateFeeId": fee_id,
                    "names": set(),
                    "inspectionTypes": set(),
                    "sourceFeeTemplates": set(),
                    "apiReinspectionFlags": set(),
                },
            )
            for key, target in (
                ("FeeName", "names"),
                ("InspectionType", "inspectionTypes"),
                ("SourceFeeTemplate", "sourceFeeTemplates"),
                ("IsReinspectionFee", "apiReinspectionFlags"),
            ):
                value = text(row.get(key))
                if value:
                    entry[target].add(value)

    by_name = defaultdict(list)
    finalized = {}
    for fee_id, entry in by_id.items():
        result = {
            "feeTemplateFeeId": fee_id,
            "names": sorted(entry["names"]),
            "inspectionTypes": sorted(entry["inspectionTypes"]),
            "sourceFeeTemplates": sorted(entry["sourceFeeTemplates"]),
            "apiReinspectionFlags": sorted(entry["apiReinspectionFlags"]),
        }
        finalized[fee_id] = result
        for name in result["names"]:
            by_name[canonical(name)].append(result)
    return {"byId": finalized, "byName": by_name}


def match_inspection_fee(index, historical_fee):
    if index is None:
        return {
            "status": "Inspection snapshot not supplied",
            "matchCategory": "not-evaluated",
        }

    fee_id = text(historical_fee.get("feeTemplateFeeId"))
    fee_name = text(historical_fee.get("feeName"))
    if fee_id and fee_id in index["byId"]:
        return {
            "status": "Exact inspection FeeTemplateFeeID",
            "matchCategory": "exact-inspection-id",
            "matchMethod": "FeeTemplateFeeID",
            "configurationSource": "inspection-template",
            **index["byId"][fee_id],
        }

    candidates = index["byName"].get(canonical(fee_name), [])
    if len(candidates) == 1:
        return {
            "status": "Unique normalized inspection fee-name candidate",
            "matchCategory": "unique-inspection-name-candidate",
            "matchMethod": "Normalized fee name",
            "candidateCount": 1,
            "candidateAssignment": candidates[0],
        }
    if len(candidates) > 1:
        return {
            "status": "Ambiguous normalized inspection fee-name candidates",
            "matchCategory": "ambiguous-inspection-name",
            "matchMethod": "Normalized fee name",
            "candidateCount": len(candidates),
            "candidateAssignments": candidates,
        }
    return {
        "status": "Unresolved in current inspection snapshot",
        "matchCategory": "unresolved",
    }


def match_option(option_index, permit_type, work_class):
    matches = option_index.get(
        (canonical(permit_type), canonical(work_class)),
        [],
    )
    if not matches:
        return {
            "status": "Permit option not matched",
            "matchCategory": "unresolved",
        }, None
    if len(matches) > 1:
        return {
            "status": "Permit option match ambiguous",
            "matchCategory": "ambiguous-option",
            "candidateCount": len(matches),
            "candidateOptions": [option_summary(item) for item in matches],
        }, None

    option = matches[0]
    return {
        "status": "Matched",
        "matchCategory": "matched-option",
        **option_summary(option),
    }, option


def classify_fee(option, option_match, fee_id_index, historical_fee):
    fee_id = text(historical_fee.get("feeTemplateFeeId"))
    fee_name = text(historical_fee.get("feeName"))

    if option is None:
        return {
            "status": option_match["status"],
            "matchCategory": option_match["matchCategory"],
        }

    if fee_id and fee_id in option["_feesById"]:
        matched_fee = option["_feesById"][fee_id]
        return {
            "status": "Exact FeeTemplateFeeID",
            "matchCategory": "exact-id",
            "matchMethod": "FeeTemplateFeeID",
            **fee_summary(matched_fee),
        }

    other_locations = []
    if fee_id:
        for other_option, other_fee in fee_id_index.get(fee_id, []):
            other_locations.append(
                {
                    **option_summary(other_option),
                    **fee_summary(other_fee),
                }
            )
    if other_locations:
        return {
            "status": "FeeTemplateFeeID found under other permit option",
            "matchCategory": "other-permit-option",
            "matchMethod": "FeeTemplateFeeID outside matched option",
            "candidateCount": len(other_locations),
            "candidateAssignments": other_locations,
        }

    name_candidates = option["_feesByName"].get(canonical(fee_name), [])
    if len(name_candidates) == 1:
        return {
            "status": "Unique normalized fee-name candidate",
            "matchCategory": "unique-name-candidate",
            "matchMethod": "Normalized fee name within matched option",
            "candidateCount": 1,
            "candidateAssignment": fee_summary(name_candidates[0]),
        }
    if len(name_candidates) > 1:
        return {
            "status": "Ambiguous normalized fee-name candidates",
            "matchCategory": "ambiguous-name",
            "matchMethod": "Normalized fee name within matched option",
            "candidateCount": len(name_candidates),
            "candidateAssignments": [
                fee_summary(item) for item in name_candidates
            ],
        }

    return {
        "status": "Unresolved in current permit snapshot",
        "matchCategory": "unresolved",
    }


def candidate_assignments(match):
    if match.get("candidateAssignment"):
        return [match["candidateAssignment"]]
    return match.get("candidateAssignments") or []


def observed_period_range(fee):
    periods = [
        item.get("period")
        for item in fee.get("byYear") or []
        if item.get("period")
    ]
    return (
        min(periods) if periods else None,
        max(periods) if periods else None,
    )


def audit_row(
    owner_level,
    parent,
    relation,
    fee,
    option_match,
    permit_match,
    inspection_match,
    match,
):
    overall = fee.get("overall") or {}
    candidates = candidate_assignments(match)
    first_year, last_year = observed_period_range(fee)
    fee_name = text(fee.get("feeName"))
    reinspection_candidate = (
        match.get("matchCategory")
        not in ("exact-id", "exact-inspection-id")
        and "reinspection" in canonical(fee_name)
    )

    candidate_ids = []
    candidate_names = []
    candidate_options = []
    for candidate in candidates:
        candidate_id = text(candidate.get("feeTemplateFeeId"))
        candidate_name = text(candidate.get("name")) or "; ".join(
            candidate.get("names") or []
        )
        if candidate_id and candidate_id not in candidate_ids:
            candidate_ids.append(candidate_id)
        if candidate_name and candidate_name not in candidate_names:
            candidate_names.append(candidate_name)
        candidate_option = " | ".join(
            part
            for part in (
                text(candidate.get("permitType")),
                text(candidate.get("workClass")),
            )
            if part
        )
        if candidate_option and candidate_option not in candidate_options:
            candidate_options.append(candidate_option)

    return {
        "OwnerLevel": owner_level,
        "ParentPermitType": parent.get("permitType"),
        "ParentWorkClass": parent.get("workClass"),
        "RelationshipFamily": (
            relation.get("relationshipFamily") if relation else None
        ),
        "FeeOwnerPermitType": (
            relation.get("permitType") if relation else parent.get("permitType")
        ),
        "FeeOwnerWorkClass": (
            relation.get("workClass") if relation else parent.get("workClass")
        ),
        "PermitOptionMatchStatus": option_match.get("status"),
        "MatchedOptionKey": option_match.get("optionKey"),
        "HistoricalFeeTemplateFeeID": fee.get("feeTemplateFeeId"),
        "HistoricalFeeName": fee_name,
        "HistoricalComputationType": fee.get("computationType"),
        "ConfigurationSource": match.get("configurationSource"),
        "MatchCategory": match.get("matchCategory"),
        "MatchStatus": match.get("status"),
        "PermitMatchCategory": permit_match.get("matchCategory"),
        "PermitMatchStatus": permit_match.get("status"),
        "InspectionMatchCategory": inspection_match.get("matchCategory"),
        "InspectionMatchStatus": inspection_match.get("status"),
        "MatchedInspectionTypes": "; ".join(
            inspection_match.get("inspectionTypes") or []
        ),
        "MatchedInspectionFeeTemplates": "; ".join(
            inspection_match.get("sourceFeeTemplates") or []
        ),
        "InspectionAPIReinspectionFlags": "; ".join(
            inspection_match.get("apiReinspectionFlags") or []
        ),
        "CandidateCount": match.get("candidateCount", len(candidates)),
        "CandidateFeeTemplateFeeIDs": "; ".join(candidate_ids),
        "CandidateFeeNames": "; ".join(candidate_names),
        "CandidatePermitOptions": "; ".join(candidate_options),
        "ReinspectionEndpointCandidate": reinspection_candidate,
        "FirstObservedYear": first_year,
        "LastObservedYear": last_year,
        "ParentFamiliesCharged": overall.get("parentFamiliesCharged"),
        "FeeOwnerPermitsCharged": overall.get("feeOwnerPermitsCharged"),
        "FeeInstances": overall.get("feeInstances"),
        "TotalComputedAmount": overall.get("totalComputedAmount"),
    }


def remap(document, templates, inspection_index=None):
    option_index, fee_id_index = build_template_indexes(templates)
    audit_rows = []
    pattern_counts = Counter()
    instance_counts = Counter()
    amount_totals = defaultdict(float)
    option_counts = Counter()
    reinspection_patterns = 0
    reinspection_instances = 0

    for parent in document.get("parentPermitPatterns") or []:
        parent_option_match, parent_option = match_option(
            option_index,
            parent.get("permitType"),
            parent.get("workClass"),
        )
        parent["configurationMatch"] = parent_option_match
        option_counts[parent_option_match["status"]] += 1

        groups = [("Parent", None, parent.get("parentFees") or [], parent_option_match, parent_option)]
        for relation in parent.get("relatedPermitPatterns") or []:
            relation_option_match, relation_option = match_option(
                option_index,
                relation.get("permitType"),
                relation.get("workClass"),
            )
            relation["configurationMatch"] = relation_option_match
            option_counts[relation_option_match["status"]] += 1
            groups.append(
                (
                    "Related permit",
                    relation,
                    relation.get("fees") or [],
                    relation_option_match,
                    relation_option,
                )
            )

        for owner_level, relation, fees, option_match, option in groups:
            for fee in fees:
                permit_match = classify_fee(
                    option,
                    option_match,
                    fee_id_index,
                    fee,
                )
                inspection_match = match_inspection_fee(
                    inspection_index,
                    fee,
                )
                if permit_match.get("matchCategory") == "exact-id":
                    permit_match = {
                        **permit_match,
                        "configurationSource": "permit-template",
                    }
                    match = permit_match
                elif (
                    inspection_match.get("matchCategory")
                    == "exact-inspection-id"
                ):
                    match = inspection_match
                else:
                    match = permit_match

                fee["permitConfigurationMatch"] = permit_match
                fee["inspectionConfigurationMatch"] = inspection_match
                fee["configurationMatch"] = match
                row = audit_row(
                    owner_level,
                    parent,
                    relation,
                    fee,
                    option_match,
                    permit_match,
                    inspection_match,
                    match,
                )
                audit_rows.append(row)

                category = match["matchCategory"]
                instances = int(row["FeeInstances"] or 0)
                amount = float(row["TotalComputedAmount"] or 0)
                pattern_counts[category] += 1
                instance_counts[category] += instances
                amount_totals[category] += amount
                if row["ReinspectionEndpointCandidate"]:
                    reinspection_patterns += 1
                    reinspection_instances += instances

    summary = document.setdefault("summary", {})
    summary["permitOptionMatchCounts"] = dict(sorted(option_counts.items()))
    summary["feeAssignmentMatchCounts"] = dict(sorted(pattern_counts.items()))
    summary["feeAssignmentMatchInstanceCounts"] = dict(
        sorted(instance_counts.items())
    )
    summary["feeAssignmentMatchComputedAmounts"] = {
        key: round(value, 4)
        for key, value in sorted(amount_totals.items())
    }
    summary["reinspectionEndpointCandidatePatterns"] = reinspection_patterns
    summary["reinspectionEndpointCandidateInstances"] = reinspection_instances

    source = document.setdefault("source", {})
    source["configurationRemappedFrom"] = source.get("feeTemplateJson")
    source["feeTemplateSchemaVersion"] = templates.get("schemaVersion")
    source["feeTemplateAsOfDate"] = templates.get("asOfDate")

    interpretation = document.setdefault("interpretation", {})
    interpretation["configurationMatching"] = (
        "Exact permit matches use FeeTemplateFeeID within the normalized "
        "permit type/work class option. Exact inspection matches use "
        "FeeTemplateFeeID from the current inspection-template extract. Name "
        "matches are review candidates only."
    )
    interpretation["reinspectionEndpointCandidates"] = (
        "Historical fee names containing 'reinspection' remain research "
        "candidates only when they lack exact permit and inspection template "
        "matches."
    )
    return audit_rows


def write_audit(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("No historical fee patterns were found.")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Remap an existing historical fee-usage JSON to a current clean "
            "permit-template snapshot without rescanning the historical CSV."
        )
    )
    parser.add_argument("usage_json", nargs="?", default=DEFAULT_USAGE)
    parser.add_argument("--templates-json", default=DEFAULT_TEMPLATES)
    parser.add_argument(
        "--inspection-fee-map",
        help=(
            "Optional inspection_fee_map.csv used for exact inspection "
            "FeeTemplateFeeID matching."
        ),
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--audit-csv", default=DEFAULT_AUDIT)
    return parser.parse_args()


def main():
    args = parse_args()
    usage_path = Path(args.usage_json)
    templates_path = Path(args.templates_json)
    output_path = Path(args.output)
    audit_path = Path(args.audit_csv)
    inspection_path = (
        Path(args.inspection_fee_map) if args.inspection_fee_map else None
    )

    document = load_json(usage_path)
    source_schema_version = document.get("schemaVersion")
    templates = load_json(templates_path)
    inspection_index = (
        load_inspection_fee_index(inspection_path)
        if inspection_path
        else None
    )
    audit_rows = remap(document, templates, inspection_index)
    if str(source_schema_version or "").startswith("2."):
        document["schemaVersion"] = "2.1" if inspection_path else "2.0"
    else:
        document["schemaVersion"] = "1.2" if inspection_path else "1.1"
    document["generatedAtUtc"] = datetime.now(timezone.utc).isoformat()
    document["source"]["feeTemplateJson"] = templates_path.name
    document["source"]["feeTemplateJsonPath"] = str(templates_path)
    if inspection_path:
        document["source"]["inspectionFeeMap"] = inspection_path.name
        document["source"]["inspectionFeeMapPath"] = str(inspection_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    write_audit(audit_path, audit_rows)

    summary = document["summary"]
    print(f"Historical fee patterns: {len(audit_rows):,}")
    print(
        "Match patterns:        "
        + json.dumps(summary["feeAssignmentMatchCounts"], sort_keys=True)
    )
    print(
        "Matched instances:     "
        + json.dumps(
            summary["feeAssignmentMatchInstanceCounts"],
            sort_keys=True,
        )
    )
    print(
        "Unresolved reinspection: "
        f"{summary['reinspectionEndpointCandidatePatterns']:,} patterns / "
        f"{summary['reinspectionEndpointCandidateInstances']:,} instances"
    )
    print(f"Remapped JSON:         {output_path}")
    print(f"Match audit CSV:       {audit_path}")


if __name__ == "__main__":
    main()
