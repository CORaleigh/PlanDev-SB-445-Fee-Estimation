from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


class FormulaCalculationUnsupported(ValueError):
    pass


PUMP_STATION_FIXED_FORMULA_ID = "1484297c-4e7f-0dab-46c4-f72ef0863990"

TREE_CONSERVATION_FORMULAS = {
    "17133e4e-ef4e-4ed9-af59-6c4810bfcba1": (
        "c64859af-2ace-4e44-9945-35a850bc4f16",
        "67708793-e39f-4507-92ae-037db95f2b1b",
    ),
    "96e352d9-3d82-40de-89b0-977689700ed2": (
        "26ed2cac-f107-4086-835a-4d901630c717",
        "5f0135ba-2a9c-415f-b8a6-c3a3972e1276",
    ),
}


def _expression_type(rule):
    expression = rule.get("expression")
    if not isinstance(expression, dict) or not expression:
        return None
    value = expression.get("expressionType")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise FormulaCalculationUnsupported(
            f"Formula expressionType {value!r} is not valid."
        ) from exc


def _numeric(value, label):
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise FormulaCalculationUnsupported(
            f"{label} is not numeric."
        ) from exc


def _round_cents(value):
    return float(
        Decimal(str(value)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )


def _calculate_pump_station_fixed(fee, rule):
    fee_id = str(fee.get("feeTemplateFeeId") or "")
    if fee_id != PUMP_STATION_FIXED_FORMULA_ID:
        return None

    expression_type = _expression_type(rule)
    inputs = list(rule.get("inputs") or [])
    if expression_type != 7 or inputs:
        raise FormulaCalculationUnsupported(
            "Validated Pump Station formula requires expressionType 7 and zero inputs."
        )

    configured_amount = _numeric(
        rule.get("amount"),
        "Pump Station configured amount",
    )
    return {
        "amount": configured_amount,
        "operation": "validated-fixed-formula",
        "expressionType": expression_type,
        "configuredRate": configured_amount,
        "validatedFixedBehavior": True,
    }


def _calculate_tree_conservation(fee, dependency_values, by_id, rule):
    fee_id = str(fee.get("feeTemplateFeeId") or "")
    expected_dependencies = TREE_CONSERVATION_FORMULAS.get(fee_id)
    if expected_dependencies is None:
        return None

    expression_type = _expression_type(rule)
    if expression_type != 5:
        raise FormulaCalculationUnsupported(
            f"Tree Conservation formula expressionType {expression_type!r} "
            "does not match the validated expressionType 5 behavior."
        )

    inputs = list(rule.get("inputs") or [])
    if len(inputs) != 2 or any(item.get("source") != "fee" for item in inputs):
        raise FormulaCalculationUnsupported(
            "Validated Tree Conservation formulas require exactly two fee-derived inputs."
        )

    dependency_ids = tuple(
        str(item.get("inputKey") or "").strip()
        for item in inputs
    )
    if set(dependency_ids) != set(expected_dependencies):
        raise FormulaCalculationUnsupported(
            "Tree Conservation formula dependencies do not match the validated assignments."
        )
    if any(dep_id not in by_id for dep_id in expected_dependencies):
        raise FormulaCalculationUnsupported(
            "Validated Tree Conservation dependencies are not present in this option."
        )
    if any(dep_id not in dependency_values for dep_id in expected_dependencies):
        raise FormulaCalculationUnsupported(
            "Validated Tree Conservation dependencies were not fully evaluated."
        )

    first_id, second_id = expected_dependencies
    first_value = _numeric(
        dependency_values[first_id],
        "Tree Conservation dependency value",
    )
    second_value = _numeric(
        dependency_values[second_id],
        "Tree Conservation dependency value",
    )

    # The validated expression rounds the smaller acreage term to hundredths
    # before applying the configured dollar-per-acre rate.
    base_value = _round_cents(min(first_value, second_value))
    configured_rate = _numeric(
        rule.get("amount"),
        "Tree Conservation configured amount",
    )

    return {
        "amount": base_value * configured_rate,
        "operation": "minimum-cent-rounded-times-rate",
        "expressionType": expression_type,
        "baseValue": base_value,
        "configuredRate": configured_rate,
        "dependencyValues": {
            first_id: first_value,
            second_id: second_value,
        },
    }


def calculate_formula_amount(fee, dependency_values, by_id):
    """Calculate only formula families validated against native EnerGov behavior.

    Supported families:
      * ROW expressionType=3: hidden rate multiplied by Occupancy Global Days.
      * Two exact Tree Conservation expressionType=5 assignments: the smaller
        of two hidden acreage terms, rounded to hundredths, multiplied by the
        visible fee's configured amount.
      * One exact Pump Station expressionType=7 assignment whose native
        configuration is Fixed, has no inputs, and uses the configured amount
        directly.

    Normal fee minimum/maximum/rounding remains the caller's responsibility.
    All other formula shapes fail closed.
    """
    calculation = fee.get("calculation") or {}
    rule = calculation.get("rule") or {}

    pump_result = _calculate_pump_station_fixed(fee, rule)
    if pump_result is not None:
        return pump_result

    tree_result = _calculate_tree_conservation(
        fee,
        dependency_values,
        by_id,
        rule,
    )
    if tree_result is not None:
        return tree_result

    inputs = list(rule.get("inputs") or [])
    if len(inputs) != 2 or any(item.get("source") != "fee" for item in inputs):
        raise FormulaCalculationUnsupported(
            "Validated ROW formulas require exactly two fee-derived inputs."
        )

    fee_name = str(fee.get("name") or "")
    if not fee_name.casefold().startswith("row -"):
        raise FormulaCalculationUnsupported(
            "Only validated ROW, exact Tree Conservation, and exact Pump Station formula families are supported."
        )

    dependency_ids = [str(item.get("inputKey") or "") for item in inputs]
    dependencies = [by_id.get(dep_id) or {} for dep_id in dependency_ids]
    dependency_names = [
        str(dep.get("name") or item.get("label") or "")
        for dep, item in zip(dependencies, inputs)
    ]

    rate_indexes = [
        index
        for index, name in enumerate(dependency_names)
        if "rate (hidden)" in name.casefold()
    ]
    days_indexes = [
        index
        for index, name in enumerate(dependency_names)
        if "occupancy global days" in name.casefold()
    ]
    if len(rate_indexes) != 1 or len(days_indexes) != 1:
        raise FormulaCalculationUnsupported(
            "ROW formula dependencies do not match the validated rate × days shape."
        )

    expression_type = _expression_type(rule)
    if expression_type is not None and expression_type != 3:
        raise FormulaCalculationUnsupported(
            f"ROW formula expressionType {expression_type} is not validated."
        )

    rate_id = dependency_ids[rate_indexes[0]]
    days_id = dependency_ids[days_indexes[0]]
    if rate_id not in dependency_values or days_id not in dependency_values:
        raise FormulaCalculationUnsupported(
            "Validated ROW formula dependencies were not fully evaluated."
        )

    rate = float(dependency_values[rate_id])
    days = float(dependency_values[days_id])
    return {
        "amount": rate * days,
        "operation": "multiply",
        "expressionType": expression_type,
        "rateFeeTemplateFeeId": rate_id,
        "daysFeeTemplateFeeId": days_id,
        "rateValue": rate,
        "daysValue": days,
    }
