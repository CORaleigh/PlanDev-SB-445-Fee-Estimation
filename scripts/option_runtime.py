from __future__ import annotations

from collections import OrderedDict

from prototype_estimator import build_report_model


EQUALITY_OPERATOR_ID = 1
NOT_EQUAL_OPERATOR_ID = 2
GREATER_THAN_OPERATOR_ID = 3
BLANK_OPERATOR_ID = 7
PRESENT_OPERATOR_ID = 8
SUPPORTED_OPERATOR_IDS = {
    EQUALITY_OPERATOR_ID,
    NOT_EQUAL_OPERATOR_ID,
    GREATER_THAN_OPERATOR_ID,
    BLANK_OPERATOR_ID,
    PRESENT_OPERATOR_ID,
}
NON_USER_INPUT_SOURCES = {"fee", "total", "constant"}
SYSTEM_APPLICABILITY_FIELDS = {
    (
        "EnerGovBusiness.PermitManagement.Permit",
        "ApplicationDate",
    ): "configuration-as-of-date",
    (
        "EnerGovBusiness.PermitManagement.Permit",
        "PermitTypeID",
    ): "selected-permit-type-id",
    (
        "EnerGovBusiness.PermitManagement.Permit",
        "PermitWorkClassID",
    ): "selected-work-class-id",
}
REVIEW_ONLY_SECTIONS = {
    "manual-review",
    "event-contingent",
    "inactive",
}


def _option(document):
    return dict(document.get("option") or {})


def _system_field_policy(condition):
    key = (
        str(condition.get("className") or "").strip(),
        str(condition.get("operandValue") or "").strip(),
    )
    return SYSTEM_APPLICABILITY_FIELDS.get(key)


def _configuration_application_datetime(document):
    as_of = str(
        (document.get("configuration") or {}).get("asOfDate") or ""
    ).strip()
    if not as_of:
        return None
    if len(as_of) == 10 and as_of[4] == "-" and as_of[7] == "-":
        return as_of + "T00:00:00"
    return as_of


def _system_applicability_values(document):
    values = {}
    application_date = _configuration_application_datetime(document)
    if application_date is not None:
        values["ApplicationDate"] = application_date

    option = _option(document)
    permit_type_id = str(option.get("permitTypeId") or "").strip()
    work_class_id = str(option.get("workClassId") or "").strip()
    if permit_type_id:
        values["PermitTypeID"] = permit_type_id
    if work_class_id:
        values["PermitWorkClassID"] = work_class_id
    return values


def _field_entry(fields, key, *, label=None, source=None):
    key = str(key or "").strip()
    if not key:
        return None
    entry = fields.get(key)
    if entry is None:
        entry = {
            "key": key,
            "label": str(label or key),
            "source": source or "unknown",
            "valueType": "unknown",
            "usages": [],
            "requiredForFeeIds": [],
            "choices": [],
        }
        fields[key] = entry
    else:
        if label and (
            not entry.get("label")
            or entry.get("label") == entry.get("key")
        ):
            entry["label"] = str(label)
        if source and entry.get("source") in {None, "", "unknown"}:
            entry["source"] = source
    return entry


def _append_unique(items, value):
    if value not in items:
        items.append(value)


def _merge_value_type(current, expected):
    if current == "unknown":
        return expected
    if current == expected:
        return current
    if current == "nullable-number" and expected in {"number", "presence"}:
        return "nullable-number"
    if expected == "nullable-number" and current in {"number", "presence"}:
        return "nullable-number"
    if {current, expected} == {"number", "presence"}:
        return "nullable-number"
    return "mixed"


def _register_calculation_inputs(document, fields):
    """
    Register user/system values used by fee calculations.

    Fee-, total-, and constant-derived values are graph dependencies and never
    become user controls.
    """
    configured = {
        str(item.get("key") or item.get("inputKey") or ""): item
        for item in document.get("requiredInputs") or []
        if item.get("key") or item.get("inputKey")
    }

    for fee in document.get("fees") or []:
        if str(fee.get("section") or "") in REVIEW_ONLY_SECTIONS:
            continue
        fee_id = str(fee.get("feeTemplateFeeId") or "")
        rule = (fee.get("calculation") or {}).get("rule") or {}
        for item in rule.get("inputs") or []:
            source = str(item.get("source") or "unknown")
            if source in NON_USER_INPUT_SOURCES:
                continue
            key = str(item.get("inputKey") or "").strip()
            if not key:
                continue

            configured_item = configured.get(key) or {}
            label = (
                configured_item.get("label")
                or item.get("label")
                or key
            )
            source = (
                configured_item.get("source")
                or source
            )
            entry = _field_entry(
                fields,
                key,
                label=label,
                source=source,
            )
            _append_unique(entry["usages"], "calculation")
            if fee_id:
                _append_unique(entry["requiredForFeeIds"], fee_id)

            # The existing validated input evaluator accepts numeric values for
            # custom/system calculation inputs. Do not infer richer types here.
            entry["valueType"] = _merge_value_type(
                entry["valueType"],
                "number",
            )
            if entry["valueType"] == "nullable-number":
                entry["choices"] = []


def _condition_leaves(group):
    for condition in group.get("childConditions") or []:
        yield condition
    for child in group.get("childConditionGroups") or []:
        yield from _condition_leaves(child)


def _choice(value, label=None):
    return {
        "value": value,
        "label": str(label if label not in (None, "") else value),
    }


def _boolean_choices():
    return [
        {"value": True, "label": "Yes"},
        {"value": False, "label": "No"},
    ]


def _blocker_severity(section):
    if str(section or "") in REVIEW_ONLY_SECTIONS:
        return "review-only"
    return "blocking"


def _blocker_context(fee):
    section = str(fee.get("section") or "")
    return {
        "feeTemplateFeeId": str(fee.get("feeTemplateFeeId") or ""),
        "feeName": str(fee.get("name") or ""),
        "section": section,
        "sourceTemplate": str(fee.get("sourceTemplate") or ""),
        "templateRelationship": str(
            fee.get("templateRelationship") or ""
        ),
        "severity": _blocker_severity(section),
    }


def _register_applicability_inputs(document, fields, blockers):
    for fee in document.get("fees") or []:
        fee_id = str(fee.get("feeTemplateFeeId") or "")
        section = str(fee.get("section") or "")
        review_only = section in REVIEW_ONLY_SECTIONS
        for condition_document in (
            (fee.get("applicability") or {}).get("conditions") or []
        ):
            kind = str(condition_document.get("kind") or "")
            if kind != "expression":
                blockers.append(
                    {
                        **_blocker_context(fee),
                        "conditionKind": kind or "unknown",
                        "code": "UNSUPPORTED_APPLICABILITY_CONDITION",
                        "message": (
                            "Option contains an applicability condition that "
                            f"cannot be compiled into generic inputs: {kind or 'unknown'}."
                        ),
                    }
                )
                continue

            source_rule = condition_document.get("sourceRule")
            if not isinstance(source_rule, dict):
                blockers.append(
                    {
                        **_blocker_context(fee),
                        "conditionKind": "expression",
                        "code": "MISSING_CONDITION_RULE",
                        "message": "Expression condition has no source rule.",
                    }
                )
                continue

            for condition in _condition_leaves(source_rule):
                operator_id = condition.get("operatorID")
                try:
                    operator_id = int(operator_id)
                except (TypeError, ValueError):
                    operator_id = None
                if operator_id not in SUPPORTED_OPERATOR_IDS:
                    blockers.append(
                        {
                            **_blocker_context(fee),
                            "conditionKind": "expression",
                            "operatorID": operator_id,
                            "operandValue": condition.get("operandValue"),
                            "operandFriendlyName": condition.get(
                                "operandFriendlyName"
                            ),
                            "comparisonValue": (
                                condition.get("compareExpression") or {}
                            ).get("value"),
                            "code": "UNSUPPORTED_CONDITION_OPERATOR",
                            "message": (
                                "Generic input compilation does not support "
                                "this condition operator."
                            ),
                        }
                    )
                    continue

                key = str(condition.get("operandValue") or "").strip()
                expression = condition.get("compareExpression") or {}
                if not key or (
                    operator_id in {
                        EQUALITY_OPERATOR_ID,
                        NOT_EQUAL_OPERATOR_ID,
                        GREATER_THAN_OPERATOR_ID,
                    }
                    and "value" not in expression
                ):
                    blockers.append(
                        {
                            **_blocker_context(fee),
                            "conditionKind": "expression",
                            "operatorID": operator_id,
                            "operandValue": condition.get("operandValue"),
                            "code": "INCOMPLETE_CONDITION",
                            "message": (
                                "Applicability condition is missing a field "
                                "or comparison value."
                            ),
                        }
                    )
                    continue

                expected = expression.get("value")
                if review_only:
                    continue

                system_policy = _system_field_policy(condition)
                if system_policy is not None:
                    if key not in _system_applicability_values(document):
                        blockers.append(
                            {
                                **_blocker_context(fee),
                                "conditionKind": "expression",
                                "operatorID": operator_id,
                                "operandValue": key,
                                "code": "MISSING_SYSTEM_APPLICABILITY_VALUE",
                                "message": (
                                    "A system-resolved applicability field "
                                    "has no value in the catalog context."
                                ),
                            }
                        )
                    continue

                entry = _field_entry(
                    fields,
                    key,
                    label=condition.get("operandFriendlyName") or key,
                    source="applicability",
                )
                if condition.get("operandTypeID") == 3:
                    entry["isCustomField"] = True
                _append_unique(entry["usages"], "applicability")
                if fee_id:
                    _append_unique(entry["requiredForFeeIds"], fee_id)

                if operator_id in {
                    BLANK_OPERATOR_ID,
                    PRESENT_OPERATOR_ID,
                }:
                    expected_type = "presence"
                    choices = _boolean_choices()
                elif operator_id == GREATER_THAN_OPERATOR_ID:
                    expected_type = "number"
                    choices = []
                else:
                    expected_type = (
                        "boolean" if isinstance(expected, bool) else "choice"
                    )
                    choices = (
                        _boolean_choices()
                        if isinstance(expected, bool)
                        else [_choice(expected, expression.get("label"))]
                    )

                entry["valueType"] = _merge_value_type(
                    entry["valueType"],
                    expected_type,
                )

                if entry["valueType"] == "nullable-number":
                    entry["choices"] = []
                else:
                    for choice in choices:
                        if choice not in entry["choices"]:
                            entry["choices"].append(choice)


def _apply_choice_context(document, fields):
    context = document.get("choiceContext")
    if not isinstance(context, dict):
        return
    referenced = {}
    for fee in document.get("fees") or []:
        for condition_document in (fee.get("applicability") or {}).get("conditions") or []:
            for condition in _condition_leaves(condition_document.get("sourceRule") or {}):
                expression = condition.get("compareExpression") or {}
                if "value" in expression:
                    key = str(condition.get("operandValue") or "").strip()
                    referenced.setdefault(key, set()).add(str(expression["value"]).strip().casefold())
    for entry in fields.values():
        if entry["valueType"] != "choice" or not (
            entry.get("isCustomField") or entry.get("source") == "custom-field"
        ):
            continue
        matches = [
            field for field in context.get("fields") or []
            if field.get("key") == entry["key"] and field.get("active") is True
        ]
        entry["choiceContextStatus"] = "unavailable"
        entry["choices"] = []
        if not context.get("layoutId") or len(matches) != 1:
            continue
        field = matches[0]
        if not field.get("fieldId") or field.get("multiple") is not False:
            continue
        entry["choices"] = [
            choice for choice in field.get("choices") or []
            if str(choice.get("label") or "").strip()
            or str(choice.get("value")).strip().casefold() in referenced.get(entry["key"], set())
        ]
        entry["choiceContextStatus"] = "available" if entry["choices"] else "unavailable"
        entry["label"] = field.get("label") or entry["label"]


def _validate_choice_value(field, value):
    status = field.get("choiceContextStatus")
    if status is None:
        return
    allowed = {str(item.get("value")).strip().casefold() for item in field.get("choices") or []}
    if status != "available" or str(value).strip().casefold() not in allowed:
        raise ValueError(f"{field['key']} is not an available choice in the selected form.")


def compile_option_schema(document):
    """
    Compile a catalog option into generic runtime input metadata.

    This is intentionally permit-agnostic. No permit type, work class, fee
    name, or assignment ID is special-cased here.
    """
    fields = OrderedDict()
    blockers = []
    _register_calculation_inputs(document, fields)
    _register_applicability_inputs(document, fields, blockers)
    _apply_choice_context(document, fields)

    for entry in fields.values():
        entry["usages"] = sorted(entry["usages"])
        entry["requiredForFeeIds"] = sorted(entry["requiredForFeeIds"])
        entry["choices"] = sorted(
            entry["choices"],
            key=lambda item: str(item.get("label") or ""),
        )

    option = _option(document)
    blocking = [
        item for item in blockers
        if item.get("severity") == "blocking"
    ]
    review_only = [
        item for item in blockers
        if item.get("severity") == "review-only"
    ]
    return {
        "optionKey": option.get("optionKey"),
        "permitType": option.get("permitType"),
        "workClass": option.get("workClass"),
        "fields": list(fields.values()),
        "compileBlockers": blockers,
        "blockingCompileBlockers": blocking,
        "reviewCompileDiagnostics": review_only,
        "runtimeSchemaReady": not blocking,
    }


def coerce_option_values(schema, raw_values=None):
    """
    Coerce UI/form values using the compiled generic field contract.

    Blank strings are omitted so missing inputs continue to fail closed except
    for nullable-number fields, where an explicit blank is preserved as None
    for IsBlank/IsNotBlank applicability. Boolean fields accept true/false,
    yes/no, and 1/0. Numeric fields are converted to int/float without
    permit-specific logic.
    """
    raw_values = dict(raw_values or {})
    fields = {
        str(item.get("key") or ""): item
        for item in schema.get("fields") or []
        if item.get("key")
    }

    unknown = sorted(set(raw_values) - set(fields))
    if unknown:
        raise KeyError(
            "Unknown option input(s): " + ", ".join(unknown)
        )

    values = {}
    for key, raw in raw_values.items():
        field = fields[key]
        value_type = str(field.get("valueType") or "unknown")

        if raw is None:
            if value_type == "nullable-number":
                values[key] = None
            continue

        if isinstance(raw, str):
            text = raw.strip()
            if text == "":
                if value_type == "nullable-number":
                    values[key] = None
                continue
        else:
            text = raw

        if value_type in {"number", "nullable-number"}:
            if isinstance(raw, bool):
                raise ValueError(f"{key} must be numeric.")
            try:
                number = float(text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be numeric.") from exc
            values[key] = int(number) if number.is_integer() else number
            continue

        if value_type in {"boolean", "presence"}:
            if isinstance(raw, bool):
                values[key] = raw
                continue
            lowered = str(text).casefold()
            if lowered in {"true", "yes", "1"}:
                values[key] = True
                continue
            if lowered in {"false", "no", "0"}:
                values[key] = False
                continue
            raise ValueError(f"{key} must be Yes or No.")

        _validate_choice_value(field, raw)
        values[key] = raw

    return values


def split_option_values(schema, values=None, *, strict=True):
    """
    Split one flat option-value mapping into calculation and applicability maps.

    A field used by both layers is copied to both maps. Unknown keys fail
    closed by default so a misspelled field cannot silently change an estimate.
    """
    values = dict(values or {})
    field_by_key = {
        str(field.get("key") or ""): field
        for field in schema.get("fields") or []
        if field.get("key")
    }

    if strict:
        unknown = sorted(set(values) - set(field_by_key))
        if unknown:
            raise KeyError(
                "Unknown option input(s): " + ", ".join(unknown)
            )

    calculation_values = {}
    applicability_values = {}
    for key, value in values.items():
        field = field_by_key.get(key)
        if field is None:
            continue
        _validate_choice_value(field, value)
        usages = set(field.get("usages") or [])
        if "calculation" in usages:
            calculation_values[key] = value
        if "applicability" in usages:
            applicability_values[key] = value

    return calculation_values, applicability_values


def evaluate_option(
    document,
    values=None,
    *,
    include_fee_names=None,
    inactive_fee_ids=None,
    strict=True,
):
    """Evaluate an option and return its input schema and estimate report.

    Unsupported calculations and conditions remain subject to staff review.
    """
    schema = compile_option_schema(document)
    calculation_values, applicability_values = split_option_values(
        schema,
        values,
        strict=strict,
    )
    system_applicability_values = _system_applicability_values(document)
    resolved_applicability_values = {
        **system_applicability_values,
        **applicability_values,
    }
    report = build_report_model(
        document,
        include_fee_names=include_fee_names,
        input_values=calculation_values,
        field_values=resolved_applicability_values,
        inactive_fee_ids=inactive_fee_ids,
    )
    return {
        "schema": schema,
        "values": dict(values or {}),
        "calculationValues": calculation_values,
        "applicabilityValues": applicability_values,
        "systemApplicabilityValues": system_applicability_values,
        "resolvedApplicabilityValues": resolved_applicability_values,
        "inactiveFeeIds": sorted(
            str(value)
            for value in (inactive_fee_ids or [])
            if str(value)
        ),
        "report": report,
    }
