from __future__ import annotations

import re
from collections import defaultdict


EXPLICIT_HIDDEN_RE = re.compile(
    r"(?:\(|\[)\s*hidden\s*(?:\)|\])|\bhidden\b",
    flags=re.IGNORECASE,
)

def _fee_id(fee):
    return str(fee.get("feeTemplateFeeId") or "").strip()


def _rule_inputs(fee):
    return list(
        ((fee.get("calculation") or {}).get("rule") or {}).get(
            "inputs"
        )
        or []
    )


def dependency_reference_index(fees):
    existing_ids = {
        _fee_id(fee)
        for fee in fees or []
        if _fee_id(fee)
    }
    referenced_by = defaultdict(set)

    for fee in fees or []:
        parent_id = _fee_id(fee)
        if not parent_id:
            continue
        for item in _rule_inputs(fee):
            if str(item.get("source") or "").strip().casefold() != "fee":
                continue
            dependency_id = str(
                item.get("inputKey") or item.get("key") or ""
            ).strip()
            if dependency_id and dependency_id in existing_ids:
                referenced_by[dependency_id].add(parent_id)

    return {
        fee_id: sorted(parent_ids)
        for fee_id, parent_ids in referenced_by.items()
    }


def _observed_directly(fee):
    occurrence = fee.get("occurrence") or {}
    numerator = occurrence.get("numerator")
    try:
        return int(numerator or 0) > 0
    except (TypeError, ValueError):
        return False


def _historical_evidence(fee, historical_denominator):
    occurrence = fee.get("occurrence") or {}
    if _observed_directly(fee):
        return "observed-direct-assessment"

    try:
        denominator = int(historical_denominator or 0)
    except (TypeError, ValueError):
        denominator = 0

    if denominator > 0:
        return "no-direct-assessment-observed"
    return "unavailable"


def _int_or_zero(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _direct_assessment_evidence(
    fee,
    historical_denominator,
    imported_evidence=None,
):
    historical_state = _historical_evidence(
        fee,
        historical_denominator,
    )
    historical_denominator = _int_or_zero(historical_denominator)
    imported_evidence = dict(imported_evidence or {})

    imported_denominator = _int_or_zero(
        imported_evidence.get("historicalDenominator")
        or imported_evidence.get("denominator")
        or imported_evidence.get("MaturePermitRows")
    )
    imported_rows = _int_or_zero(
        imported_evidence.get("activeDirectFeeRows")
        or imported_evidence.get("ActiveDirectFeeRows")
        or imported_evidence.get("casesDirectlyCharged")
        or imported_evidence.get("CasesDirectlyCharged")
    )

    if imported_denominator > 0:
        imported_state = (
            "observed-direct-assessment"
            if imported_rows > 0
            else "no-direct-assessment-observed"
        )
        # Never let an imported zero silently override existing positive
        # direct-assessment evidence. Conflicts fail on the assessed side.
        if historical_state == "observed-direct-assessment":
            state = "observed-direct-assessment"
            source = (
                "historical-usage+direct-assessment-import"
                if imported_state == historical_state
                else "conflicting-direct-assessment-evidence"
            )
        else:
            state = imported_state
            source = "direct-assessment-import"
        return state, imported_denominator, source

    return (
        historical_state,
        historical_denominator,
        "historical-usage" if historical_denominator > 0 else "unavailable",
    )


def classify_assessment_roles(
    fees,
    historical_denominator=0,
    assessment_evidence=None,
):
    references = dependency_reference_index(fees)
    roles = {}

    for fee in fees or []:
        fee_id = _fee_id(fee)
        if not fee_id:
            continue

        referenced_by = references.get(fee_id, [])
        is_dependency = bool(referenced_by)
        hidden_marker = bool(
            EXPLICIT_HIDDEN_RE.search(str(fee.get("name") or ""))
        )
        direct_evidence, evidence_denominator, evidence_source = (
            _direct_assessment_evidence(
                fee,
                historical_denominator,
                (assessment_evidence or {}).get(fee_id),
            )
        )

        imported = (assessment_evidence or {}).get(fee_id) or {}
        validated_basis = str(
            imported.get("validatedDependencyOnlyBasis") or ""
        ).strip() or None

        if (
            is_dependency
            and direct_evidence == "observed-direct-assessment"
        ):
            role = "dependency-and-assessed"
            basis = "calculation dependency + direct historical assessment"
        elif is_dependency and validated_basis:
            role = "dependency-only"
            basis = validated_basis
        elif (
            is_dependency
            and hidden_marker
            and direct_evidence == "no-direct-assessment-observed"
        ):
            role = "dependency-only"
            basis = (
                "explicit Hidden marker + calculation dependency + "
                "no direct historical assessment"
            )
        elif is_dependency:
            role = "dependency-unresolved"
            basis = (
                "calculation dependency without sufficient evidence for "
                "dependency-only treatment"
            )
        else:
            role = "standalone-or-assessed"
            basis = "not referenced as a fee calculation dependency"

        roles[fee_id] = {
            "role": role,
            "isCalculationDependency": is_dependency,
            "referencedByFeeIds": referenced_by,
            "explicitHiddenMarker": hidden_marker,
            "directAssessmentEvidence": direct_evidence,
            "historicalDenominator": evidence_denominator,
            "directAssessmentEvidenceSource": evidence_source,
            "basis": basis,
        }

    return roles


def annotate_assessment_roles(
    fees,
    historical_denominator=0,
    assessment_evidence=None,
):
    roles = classify_assessment_roles(
        fees,
        historical_denominator=historical_denominator,
        assessment_evidence=assessment_evidence,
    )
    for fee in fees or []:
        fee_id = _fee_id(fee)
        evidence = roles.get(fee_id)
        if evidence is None:
            fee["assessmentRole"] = "standalone-or-assessed"
            fee["assessmentEvidence"] = {
                "role": "standalone-or-assessed",
                "isCalculationDependency": False,
                "referencedByFeeIds": [],
                "explicitHiddenMarker": False,
                "directAssessmentEvidence": "unavailable",
                "historicalDenominator": int(
                    historical_denominator or 0
                ),
                "directAssessmentEvidenceSource": (
                    "historical-usage"
                    if int(historical_denominator or 0) > 0
                    else "unavailable"
                ),
                "basis": "fee has no stable assignment id",
            }
            continue
        fee["assessmentRole"] = evidence["role"]
        fee["assessmentEvidence"] = evidence
    return fees
