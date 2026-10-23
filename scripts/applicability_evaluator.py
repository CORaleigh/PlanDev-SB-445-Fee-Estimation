from __future__ import annotations


SUPPORTED_EQUALITY_OPERATOR_ID = 1
SUPPORTED_NOT_EQUAL_OPERATOR_ID = 2
SUPPORTED_GREATER_THAN_OPERATOR_ID = 3
SUPPORTED_BLANK_OPERATOR_ID = 7
SUPPORTED_PRESENT_OPERATOR_ID = 8
WWP_FEE_NAMES = {
    "Working without a Permit (Electrical)",
    "Working without a Permit (Mechanical)",
    "Working without a Permit (Plumbing)",
    "Working without a Permit (Fire)",
}


class ApplicabilityBlocked(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _normalize_scalar(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip()
    lowered = text.casefold()
    if lowered in {"true", "yes", "y", "1"}:
        return True
    if lowered in {"false", "no", "n", "0"}:
        return False
    return lowered


def _is_present(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _numeric_condition_value(value, label):
    if isinstance(value, bool) or value is None:
        raise ApplicabilityBlocked(
            "INVALID_NUMERIC_CONDITION_VALUE",
            f"{label} must be numeric.",
        )
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ApplicabilityBlocked(
            "INVALID_NUMERIC_CONDITION_VALUE",
            f"{label} must be numeric.",
        ) from exc


def evaluate_condition(condition, field_values):
    operator_id = condition.get("operatorID")
    try:
        operator_id = int(operator_id)
    except (TypeError, ValueError) as exc:
        raise ApplicabilityBlocked(
            "UNSUPPORTED_CONDITION_OPERATOR",
            f"Condition operator {operator_id!r} is not supported.",
        ) from exc

    if operator_id not in {
        SUPPORTED_EQUALITY_OPERATOR_ID,
        SUPPORTED_NOT_EQUAL_OPERATOR_ID,
        SUPPORTED_GREATER_THAN_OPERATOR_ID,
        SUPPORTED_BLANK_OPERATOR_ID,
        SUPPORTED_PRESENT_OPERATOR_ID,
    }:
        raise ApplicabilityBlocked(
            "UNSUPPORTED_CONDITION_OPERATOR",
            f"Condition operator {operator_id!r} is not validated.",
        )

    field_key = str(condition.get("operandValue") or "").strip()
    if not field_key:
        raise ApplicabilityBlocked(
            "MISSING_CONDITION_FIELD",
            "Condition does not identify an operand field.",
        )
    if field_key not in field_values:
        raise ApplicabilityBlocked(
            "MISSING_CONDITION_INPUT",
            f"Condition input {field_key!r} was not supplied.",
        )

    if operator_id == SUPPORTED_PRESENT_OPERATOR_ID:
        return _is_present(field_values[field_key])
    if operator_id == SUPPORTED_BLANK_OPERATOR_ID:
        return not _is_present(field_values[field_key])

    expression = condition.get("compareExpression") or {}
    if "value" not in expression:
        raise ApplicabilityBlocked(
            "MISSING_CONDITION_VALUE",
            "Condition comparison value is absent.",
        )

    if operator_id == SUPPORTED_GREATER_THAN_OPERATOR_ID:
        if field_values[field_key] is None:
            return False
        left = _numeric_condition_value(
            field_values[field_key],
            f"Condition input {field_key!r}",
        )
        right = _numeric_condition_value(
            expression.get("value"),
            "Condition comparison value",
        )
        return left > right

    left = _normalize_scalar(field_values[field_key])
    right = _normalize_scalar(expression.get("value"))
    if operator_id == SUPPORTED_NOT_EQUAL_OPERATOR_ID:
        return left != right
    return left == right


def evaluate_condition_group(group, field_values):
    values = [
        evaluate_condition(condition, field_values)
        for condition in group.get("childConditions") or []
    ]
    values.extend(
        evaluate_condition_group(child_group, field_values)
        for child_group in group.get("childConditionGroups") or []
    )

    if not values:
        raise ApplicabilityBlocked(
            "EMPTY_CONDITION_GROUP",
            "Condition group contains no evaluable conditions.",
        )

    if bool(group.get("isOrGroup")):
        return any(values)
    return all(values)


def evaluate_expression_condition(condition_document, field_values):
    if str(condition_document.get("kind") or "") != "expression":
        raise ApplicabilityBlocked(
            "UNSUPPORTED_CONDITION_KIND",
            f"Condition kind {condition_document.get('kind')!r} is not supported.",
        )
    source_rule = condition_document.get("sourceRule")
    if not isinstance(source_rule, dict):
        raise ApplicabilityBlocked(
            "MISSING_CONDITION_RULE",
            "Expression condition does not preserve a source rule.",
        )
    return evaluate_condition_group(source_rule, field_values)


def fee_dependency_ids(fee):
    rule = (fee.get("calculation") or {}).get("rule") or {}
    return [
        str(item.get("inputKey") or "")
        for item in rule.get("inputs") or []
        if item.get("source") == "fee" and item.get("inputKey")
    ]


def resolve_fee_applicability(
    fees,
    field_values=None,
    *,
    include_event_fees=False,
):
    """Resolve supported permit-stage applicability and fail closed otherwise.

    Returns (mapping, unresolved). The mapping contains feeTemplateFeeId -> bool
    for every conditional/event-contingent fee with an assignment id.

    Supported conditions:
      * expression conditions using validated equality operatorID=1;
      * authoritative operatorID=2 inequality checks ("NotEqual");
      * authoritative operatorID=3 numeric checks ("GreaterThan");
      * authoritative operatorID=7 blank checks ("IsBlank");
      * authoritative operatorID=8 presence checks ("IsNotBlank");
      * AND/OR groups, including nested groups;
      * GUID/string and boolean comparison values;
      * event-contingent fees excluded from initial permit estimates;
      * Working without a Permit fees additionally require their fee-derived
        discipline/base dependency to be applicable.

    Unsupported child-template or other condition kinds are mapped false and
    reported in unresolved so they cannot enter the numeric subtotal silently.
    """
    field_values = dict(field_values or {})
    mapping = {}
    unresolved = []
    by_id = {
        str(fee.get("feeTemplateFeeId")): fee
        for fee in fees
        if fee.get("feeTemplateFeeId")
    }

    for fee_id, fee in by_id.items():
        section = str(fee.get("section") or "")
        status = str((fee.get("applicability") or {}).get("status") or "")
        conditions = list(
            (fee.get("applicability") or {}).get("conditions") or []
        )
        requires = (
            section in {"conditional", "event-contingent"}
            or status in {"conditional", "event-contingent"}
            or (
                (section == "manual-review" or status == "manual-review")
                and bool(conditions)
            )
        )
        if not requires:
            continue

        if section == "event-contingent" or status == "event-contingent":
            mapping[fee_id] = bool(include_event_fees)
            continue

        if not conditions:
            mapping[fee_id] = False
            unresolved.append(
                {
                    "feeTemplateFeeId": fee_id,
                    "name": str(fee.get("name") or ""),
                    "code": "MISSING_APPLICABILITY_CONDITION",
                    "message": "Conditional fee has no preserved condition.",
                }
            )
            continue

        expression_conditions = [
            item for item in conditions if str(item.get("kind") or "") == "expression"
        ]
        unsupported_conditions = [
            item for item in conditions if str(item.get("kind") or "") != "expression"
        ]

        if unsupported_conditions:
            mapping[fee_id] = False
            unresolved.append(
                {
                    "feeTemplateFeeId": fee_id,
                    "name": str(fee.get("name") or ""),
                    "code": "UNSUPPORTED_APPLICABILITY_CONDITION",
                    "message": (
                        "Conditional fee includes unsupported applicability kinds: "
                        + ", ".join(
                            sorted(
                                {
                                    str(item.get("kind") or "unknown")
                                    for item in unsupported_conditions
                                }
                            )
                        )
                    ),
                }
            )
            continue

        try:
            mapping[fee_id] = all(
                evaluate_expression_condition(item, field_values)
                for item in expression_conditions
            )
        except ApplicabilityBlocked as exc:
            mapping[fee_id] = False
            unresolved.append(
                {
                    "feeTemplateFeeId": fee_id,
                    "name": str(fee.get("name") or ""),
                    "code": exc.code,
                    "message": exc.message,
                }
            )

    # The validated Working without a Permit calculations are discipline/base
    # fee percentages. Do not publish a zero-dollar WWP line when the linked
    # discipline/base fee itself is not applicable.
    for fee_id, fee in by_id.items():
        if str(fee.get("name") or "") not in WWP_FEE_NAMES:
            continue
        if not mapping.get(fee_id):
            continue
        dependencies = fee_dependency_ids(fee)
        if len(dependencies) != 1:
            continue
        dependency_id = dependencies[0]
        dependency_fee = by_id.get(dependency_id)
        if dependency_fee is None:
            continue
        dependency_section = str(dependency_fee.get("section") or "")
        dependency_status = str(
            (dependency_fee.get("applicability") or {}).get("status") or ""
        )
        if dependency_section == "conditional" or dependency_status == "conditional":
            if not mapping.get(dependency_id, False):
                mapping[fee_id] = False

    return mapping, unresolved
