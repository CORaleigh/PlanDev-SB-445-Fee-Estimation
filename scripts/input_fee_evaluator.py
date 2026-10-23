from __future__ import annotations

import math
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP

from formula_fee_calculation import (
    FormulaCalculationUnsupported,
    calculate_formula_amount,
)
from tiered_fee_calculation import (
    TieredCalculationUnsupported,
    calculate_tiered_amount,
)


DUPLICATE_FEE_INPUT_CODE = "DUPLICATE_FEE_INPUT_SEMANTICS_UNVALIDATED"
MULTI_INPUT_CODE = "MULTI_INPUT_SEMANTICS_UNVALIDATED"
APPLICABILITY_CODE = "APPLICABILITY_UNRESOLVED"
MISSING_INPUT_CODE = "INPUT_VALUE_MISSING"
UNSUPPORTED_INPUT_CODE = "UNSUPPORTED_INPUT_SOURCE"
UNSUPPORTED_ROUNDING_CODE = "UNSUPPORTED_ROUNDING"
UNSUPPORTED_MODE_CODE = "UNSUPPORTED_CALCULATION_MODE"
TIERED_CALCULATION_CODE = "TIERED_CALCULATION_UNVALIDATED"
FORMULA_CALCULATION_CODE = "FORMULA_CALCULATION_UNVALIDATED"
CYCLE_CODE = "FEE_DEPENDENCY_CYCLE"

SYSTEM_INPUT_KEYS = {"SquareFeet", "ValuationValue"}
VALIDATED_FIXED_INPUT_ASSIGNMENTS = {
    "e8663ef4-417e-4335-a08d-1c898f80c66d",
    "37186620-c5a5-4994-bfc1-d8809c7365fe",
    "5ced9d47-bab0-47eb-984f-b26e61df75e1",
    "78039fd1-5457-4d6c-9083-94f0f5c44f99",
    "5d4584fa-5cd9-47ec-88f8-ff64b570dc7d",
    "8df25059-9059-42fc-98bd-5aaa80b5899c",
    "701a154e-6acd-4df0-8c90-17c3693c1c36",
    "86c181d9-36ad-42fe-bff8-6a831d052c8f",
    "0d9e4154-3d96-4fb3-9954-177657a7b293",
    "259a3260-26ec-4bde-bac3-3569aaadb964",
    "5ad6126d-0019-492a-aa81-0b0454f2b0b0",
}

VALIDATED_PERCENTAGE_INPUT_FEE_NAMES = {
    "misc - technology surcharge",
    "working without a permit (electrical)",
    "working without a permit (fire)",
    "working without a permit (mechanical)",
    "working without a permit (plumbing)",
}

# Current EnerGov assignments whose "Total of Fees in Template" semantics were
# validated from CACOMPUTEDFEEXREF history. The tuple contains the exact fee
# assignments that EnerGov linked into the total for that surcharge. This is
# deliberately an assignment whitelist rather than a name heuristic so future
# or changed total-based rules fail closed until separately validated.
VALIDATED_TOTAL_BASED_TECHNOLOGY_COMPONENTS = {
    "dbeb60d1-9a93-4dee-b002-4fa9f96a6fa6": (
        "f09bbc1f-cd45-49f2-81a9-38e4434e08cf",
        "7210288d-2877-477f-9872-3af531dfe12b",
    ),
    "05bb7cf3-efb6-4f52-b03c-c2708b8abcd4": (
        "e9de72a1-8eb9-468a-8d36-5ded5df81899",
    ),
    "93db95ae-9126-47ad-9c86-db8fc8f9f5a7": (
        "5ac1d8c8-d9b0-471b-8393-e08d0694b17b",
        "184b063e-6269-4b69-a413-63ca02d5ced4",
    ),
    "221cfbbd-680e-4b17-b21c-f756824a9a9b": (
        "76ffd5e6-b8d7-4adf-9cff-8e8f44f53163",
        "fb55fc5a-9fb8-4b9b-9871-a3fd0b5d5128",
    ),
    "5755f667-7c79-4dee-be40-9baa6fff66fa": (
        "f722063f-2af1-4e4a-9e2f-13d5b8c46df1",
        "9c60786f-530c-40f7-843c-43fae5581ff9",
        "d45e15cf-e1c2-43bb-8ad0-b91c5eef2e95",
    ),
    "c439a987-8dbd-43e2-a068-f0aa2319c9d0": (
        "0f67e134-79b2-45e8-869f-8a92f923cca4",
        "a9e01e4d-4f53-415d-9d5c-78c9aad82028",
        "f941e923-e9c3-45f4-81da-de5de1b7ac54",
    ),
}


# Some technology-surcharge assignments expose many fee inputs in current
# configuration, but native CACOMPUTEDFEEXREF history establishes a narrower
# effective component set. Keep these assignment-specific so unrelated
# multi-input surcharges continue to fail closed.
VALIDATED_FEE_BASED_TECHNOLOGY_COMPONENTS = {
    "0f671a9d-554d-433c-a0fe-010732422b56": (
        "2348ff00-df4b-481e-94cb-310537f91cbf",
        "794bbf57-c1a6-41da-90fc-6d0d7bfed3cb",
    ),
    "d92a2242-5b24-4624-8950-6a030a150951": (
        "f2ebbd08-bb1a-47ca-9193-eba43c39d17d",
    ),
}

# Exact downstream fee assignments where FY27 assessed INPUTVALUE history proves
# EnerGov rounds the linked fee value to cents before applying the consumer
# rule. Keep this assignment-specific rather than changing dependency semantics
# globally.
VALIDATED_CENT_ROUNDED_FEE_DEPENDENCIES = {
    "f2ebbd08-bb1a-47ca-9193-eba43c39d17d": (
        "7728caad-06b5-438f-88b3-e637cdec2b97",
    ),
}


class EvaluationBlocked(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _rule_inputs(fee):
    calculation = fee.get("calculation") or {}
    rule = calculation.get("rule") or {}
    return list(rule.get("inputs") or [])


def _validated_total_dependencies(fee, by_id):
    fee_id = str(fee.get("feeTemplateFeeId") or "")
    component_ids = VALIDATED_TOTAL_BASED_TECHNOLOGY_COMPONENTS.get(fee_id)
    if component_ids is None:
        return None
    if any(component_id not in by_id for component_id in component_ids):
        return None
    return list(component_ids)


def _validated_fee_based_dependencies(fee, by_id):
    fee_id = str(fee.get("feeTemplateFeeId") or "")
    component_ids = VALIDATED_FEE_BASED_TECHNOLOGY_COMPONENTS.get(fee_id)
    if component_ids is None:
        return None
    if any(component_id not in by_id for component_id in component_ids):
        return None
    return list(component_ids)


def _fee_dependencies(fee, by_id):
    validated_fee_components = _validated_fee_based_dependencies(fee, by_id)
    if validated_fee_components is not None:
        return validated_fee_components

    result = []
    for item in _rule_inputs(fee):
        source = item.get("source")
        if source == "fee":
            key = str(item.get("inputKey") or "").strip()
            if key in by_id and key not in result:
                result.append(key)
            continue
        if source == "total":
            component_ids = _validated_total_dependencies(fee, by_id)
            if component_ids is not None:
                for component_id in component_ids:
                    if component_id not in result:
                        result.append(component_id)
    return result


def build_dependency_graph(fees):
    by_id = {
        str(fee.get("feeTemplateFeeId")): fee
        for fee in fees
        if fee.get("feeTemplateFeeId")
    }
    dependencies = {
        fee_id: _fee_dependencies(fee, by_id)
        for fee_id, fee in by_id.items()
    }
    consumers = defaultdict(list)
    for consumer_id, dependency_ids in dependencies.items():
        for dependency_id in dependency_ids:
            if consumer_id not in consumers[dependency_id]:
                consumers[dependency_id].append(consumer_id)
    return by_id, dependencies, dict(consumers)


def validate_acyclic(fees):
    by_id, dependencies, _ = build_dependency_graph(fees)
    state = {}

    def visit(fee_id, path):
        marker = state.get(fee_id)
        if marker == "done":
            return
        if marker == "visiting":
            cycle = " -> ".join(path + [fee_id])
            raise EvaluationBlocked(
                CYCLE_CODE,
                f"Fee dependency cycle detected: {cycle}",
            )
        state[fee_id] = "visiting"
        for dependency_id in dependencies.get(fee_id, []):
            if dependency_id in by_id:
                visit(dependency_id, path + [fee_id])
        state[fee_id] = "done"

    for fee_id in by_id:
        visit(fee_id, [])
    return True


def _requires_applicability_resolution(fee):
    section = fee.get("section")
    if section in {
        "conditional",
        "event-contingent",
        "manual-review",
        "inactive",
    }:
        return True
    applicability = fee.get("applicability") or {}
    if applicability.get("status") in {
        "conditional",
        "event-contingent",
        "manual-review",
        "inactive",
    }:
        return True
    conditions = fee.get("conditions") or []
    return bool(conditions)


def _round_amount(amount, rule):
    rounding_type = rule.get("roundingType")
    rounding_value = _number(rule.get("roundingValue"))

    if rounding_type is None:
        return amount

    try:
        rounding_type = int(rounding_type)
    except (TypeError, ValueError):
        raise EvaluationBlocked(
            UNSUPPORTED_ROUNDING_CODE,
            f"Unsupported rounding type {rounding_type!r}.",
        )

    if rounding_type == 1 and (rounding_value is None or rounding_value == 0):
        return amount

    if rounding_type == 4 and rounding_value == 1:
        return float(
            Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

    if rounding_type == 2 and rounding_value == 1:
        return float(math.ceil(amount))

    raise EvaluationBlocked(
        UNSUPPORTED_ROUNDING_CODE,
        f"Rounding type {rounding_type}, value {rounding_value} is not validated.",
    )


def _apply_bounds(amount, rule):
    minimum = _number(rule.get("minimum"))
    maximum = _number(rule.get("maximum"))

    if minimum is not None and minimum > 0:
        amount = max(amount, minimum)
    if maximum is not None and maximum > 0:
        amount = min(amount, maximum)
    return amount


def _validated_multi_input_sum(fee, rule_inputs):
    if not rule_inputs:
        return False
    if any(item.get("source") != "fee" for item in rule_inputs):
        return False
    name = str(fee.get("name") or "").casefold()
    return "technology surcharge" in name


def _fee_dependency_value(consumer_id, dependency_id, amount):
    value = float(amount or 0)
    rounded_dependencies = VALIDATED_CENT_ROUNDED_FEE_DEPENDENCIES.get(
        str(consumer_id),
        (),
    )
    if str(dependency_id) in rounded_dependencies:
        return float(
            Decimal(str(value)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )
    return value


def _effective_input_rate(fee, rule):
    """Return the validated multiplier for an input-based fee.

    EnerGov stores percentage-based input fees as percentage values rather than
    decimal multipliers. Historical assessment validation currently supports
    this interpretation for MISC - Technology Surcharge and the exact Working
    without a Permit discipline names listed in
    VALIDATED_PERCENTAGE_INPUT_FEE_NAMES. Other input-fee amounts remain
    ordinary multipliers unless separately validated.
    """
    configured_rate = _number(rule.get("amount"))
    if configured_rate is None:
        return None

    name = str(fee.get("name") or "").strip().casefold()
    if name in VALIDATED_PERCENTAGE_INPUT_FEE_NAMES:
        return configured_rate / 100.0
    return configured_rate


def evaluate_fee_graph(
    fees,
    target_fee_ids,
    user_inputs=None,
    case_inputs=None,
    applicability=None,
    inactive_fee_ids=None,
):
    """Evaluate supported fixed, input, tiered, and validated formula fee nodes.

    user_inputs contains explicit/custom-field values supplied for the estimate.
    case_inputs contains built-in permit values such as SquareFeet and
    ValuationValue.

    Applicability is deliberately separate from calculation. Pass a mapping of
    feeTemplateFeeId -> bool for conditional/event-contingent nodes that have
    already been resolved by an external applicability step. Unresolved
    applicability fails closed.
    """
    user_inputs = dict(user_inputs or {})
    case_inputs = dict(case_inputs or {})
    applicability = dict(applicability or {})
    inactive_fee_ids = {
        str(value)
        for value in (inactive_fee_ids or [])
        if str(value)
    }
    by_id, _, _ = build_dependency_graph(fees)
    validate_acyclic(fees)
    cache = {}
    visiting = set()

    def blocked(fee_id, code, message, dependencies=None):
        result = {
            "feeTemplateFeeId": fee_id,
            "status": "blocked",
            "amount": None,
            "validationCodes": [code],
            "message": message,
            "dependencies": list(dependencies or []),
        }
        cache[fee_id] = result
        return result

    def resolve_non_fee_input(fee_id, item):
        source = item.get("source")
        key = str(item.get("inputKey") or "").strip()

        if source == "custom-field":
            if key not in user_inputs:
                return None, blocked(
                    fee_id,
                    MISSING_INPUT_CODE,
                    f"Required project input {key!r} was not supplied.",
                )
            value = _number(user_inputs[key])
            if value is None:
                return None, blocked(
                    fee_id,
                    MISSING_INPUT_CODE,
                    f"Project input {key!r} is not numeric.",
                )
            return value, None

        # Current normalization emits "system". Existing r3 catalogs use
        # "other" for these two known EnerGov built-in permit values.
        if source == "system" or (
            source == "other" and key in SYSTEM_INPUT_KEYS
        ):
            if key not in case_inputs:
                return None, blocked(
                    fee_id,
                    MISSING_INPUT_CODE,
                    f"Required permit value {key!r} was not supplied.",
                )
            value = _number(case_inputs[key])
            if value is None:
                return None, blocked(
                    fee_id,
                    MISSING_INPUT_CODE,
                    f"Permit value {key!r} is not numeric.",
                )
            return value, None

        if source == "constant":
            value = _number(item.get("constant"))
            if value is None:
                return None, blocked(
                    fee_id,
                    UNSUPPORTED_INPUT_CODE,
                    "Constant input does not contain a numeric constant.",
                )
            return value, None

        return None, blocked(
            fee_id,
            UNSUPPORTED_INPUT_CODE,
            f"Input source {source!r} is not supported.",
        )

    def evaluate(fee_id):
        fee_id = str(fee_id)
        if fee_id in cache:
            return cache[fee_id]
        if fee_id not in by_id:
            return blocked(
                fee_id,
                UNSUPPORTED_INPUT_CODE,
                f"Referenced fee assignment {fee_id} is not present in this option.",
            )
        if fee_id in inactive_fee_ids:
            result = {
                "feeTemplateFeeId": fee_id,
                "status": "not-applicable",
                "amount": 0.0,
                "validationCodes": [],
                "message": None,
                "dependencies": [],
            }
            cache[fee_id] = result
            return result
        if fee_id in visiting:
            return blocked(
                fee_id,
                CYCLE_CODE,
                f"Fee dependency cycle detected at {fee_id}.",
            )

        fee = by_id[fee_id]
        visiting.add(fee_id)
        try:
            if _requires_applicability_resolution(fee):
                if fee_id not in applicability:
                    return blocked(
                        fee_id,
                        APPLICABILITY_CODE,
                        "Fee applicability has not been resolved.",
                    )
                if not applicability[fee_id]:
                    result = {
                        "feeTemplateFeeId": fee_id,
                        "status": "not-applicable",
                        "amount": 0.0,
                        "validationCodes": [],
                        "message": None,
                        "dependencies": [],
                    }
                    cache[fee_id] = result
                    return result

            if fee.get("estimationReview"):
                return blocked(fee_id, "ESTIMATION_REVIEW_REQUIRED", str(fee["estimationReview"]))

            calculation = fee.get("calculation") or {}
            mode = calculation.get("mode")

            if mode == "fixed":
                amount = _number(calculation.get("fixedAmount"))
                if amount is None:
                    return blocked(
                        fee_id,
                        UNSUPPORTED_MODE_CODE,
                        "Fixed fee does not contain a calculable fixedAmount.",
                    )
                result = {
                    "feeTemplateFeeId": fee_id,
                    "status": "calculated",
                    "amount": amount,
                    "validationCodes": [],
                    "message": None,
                    "dependencies": [],
                }
                cache[fee_id] = result
                return result

            if mode == "tiered":
                rule = calculation.get("rule") or {}
                rule_inputs = list(rule.get("inputs") or [])
                tiers = list(rule.get("tiers") or [])

                if len(rule_inputs) != 1:
                    return blocked(
                        fee_id,
                        TIERED_CALCULATION_CODE,
                        "Validated tiered calculations require exactly one input.",
                    )

                item = rule_inputs[0]
                source = item.get("source")
                key = str(item.get("inputKey") or "").strip()

                if source == "fee":
                    return blocked(
                        fee_id,
                        TIERED_CALCULATION_CODE,
                        "Fee-derived tier inputs have not been validated.",
                    )

                value, error = resolve_non_fee_input(fee_id, item)
                if error is not None:
                    return error

                try:
                    tier_result = calculate_tiered_amount(key, value, tiers)
                    amount = _apply_bounds(tier_result["amount"], rule)
                    amount = _round_amount(amount, rule)
                except TieredCalculationUnsupported as exc:
                    return blocked(
                        fee_id,
                        TIERED_CALCULATION_CODE,
                        str(exc),
                    )
                except EvaluationBlocked as exc:
                    return blocked(
                        fee_id,
                        exc.code,
                        exc.message,
                    )

                result = {
                    "feeTemplateFeeId": fee_id,
                    "status": "calculated",
                    "amount": amount,
                    "validationCodes": [],
                    "message": None,
                    "dependencies": [],
                    "inputSources": [source],
                    "baseValue": value,
                    "tierNumber": tier_result["tierNumber"],
                }
                cache[fee_id] = result
                return result

            if mode == "formula":
                rule = calculation.get("rule") or {}
                rule_inputs = list(rule.get("inputs") or [])
                dependency_ids = [
                    str(item.get("inputKey") or "").strip()
                    for item in rule_inputs
                    if item.get("source") == "fee"
                ]
                dependency_values = {}

                for dependency_id in dependency_ids:
                    dependency = evaluate(dependency_id)
                    if dependency["status"] == "blocked":
                        return blocked(
                            fee_id,
                            dependency["validationCodes"][0],
                            f"Dependency {dependency_id} could not be evaluated: "
                            f"{dependency['message']}",
                            dependency_ids,
                        )
                    dependency_values[dependency_id] = float(
                        dependency["amount"] or 0
                    )

                try:
                    formula_result = calculate_formula_amount(
                        fee,
                        dependency_values,
                        by_id,
                    )
                    amount = _apply_bounds(formula_result["amount"], rule)
                    amount = _round_amount(amount, rule)
                except FormulaCalculationUnsupported as exc:
                    return blocked(
                        fee_id,
                        FORMULA_CALCULATION_CODE,
                        str(exc),
                        dependency_ids,
                    )
                except EvaluationBlocked as exc:
                    return blocked(
                        fee_id,
                        exc.code,
                        exc.message,
                        dependency_ids,
                    )

                result = {
                    "feeTemplateFeeId": fee_id,
                    "status": "calculated",
                    "amount": amount,
                    "validationCodes": [],
                    "message": None,
                    "dependencies": dependency_ids,
                    "operation": formula_result["operation"],
                    "expressionType": formula_result["expressionType"],
                }
                for metadata_key in (
                    "rateValue",
                    "daysValue",
                    "baseValue",
                    "configuredRate",
                    "dependencyValues",
                    "validatedFixedBehavior",
                ):
                    if metadata_key in formula_result:
                        result[metadata_key] = formula_result[metadata_key]
                cache[fee_id] = result
                return result

            if mode != "input":
                return blocked(
                    fee_id,
                    UNSUPPORTED_MODE_CODE,
                    f"Calculation mode {mode!r} is not supported by the input evaluator.",
                )

            rule = calculation.get("rule") or {}
            rule_inputs = list(rule.get("inputs") or [])

            if (
                fee_id in VALIDATED_FIXED_INPUT_ASSIGNMENTS
                and not rule_inputs
            ):
                amount = _number(rule.get("amount"))
                if amount is None:
                    return blocked(
                        fee_id,
                        UNSUPPORTED_MODE_CODE,
                        "Validated fixed-behavior assignment has no numeric configured amount.",
                    )
                result = {
                    "feeTemplateFeeId": fee_id,
                    "status": "calculated",
                    "amount": amount,
                    "validationCodes": [],
                    "message": None,
                    "dependencies": [],
                    "inputSources": [],
                    "baseValue": amount,
                    "configuredRate": amount,
                    "rate": None,
                    "validatedFixedBehavior": True,
                }
                cache[fee_id] = result
                return result

            fee_input_keys = [
                str(item.get("inputKey") or "").strip()
                for item in rule_inputs
                if item.get("source") == "fee"
            ]
            duplicates = sorted(
                key for key, count in Counter(fee_input_keys).items() if count > 1
            )
            if duplicates:
                return blocked(
                    fee_id,
                    DUPLICATE_FEE_INPUT_CODE,
                    "Duplicate fee-input records have unvalidated current EnerGov semantics: "
                    + ", ".join(duplicates),
                    duplicates,
                )

            values = []
            dependency_ids = []
            input_sources = []

            validated_fee_components = _validated_fee_based_dependencies(
                fee,
                by_id,
            )
            if validated_fee_components is not None:
                for component_id in validated_fee_components:
                    dependency_ids.append(component_id)
                    input_sources.append("fee")
                    dependency = evaluate(component_id)
                    if dependency["status"] == "blocked":
                        return blocked(
                            fee_id,
                            dependency["validationCodes"][0],
                            f"Dependency {component_id} could not be evaluated: "
                            f"{dependency['message']}",
                            dependency_ids,
                        )
                    values.append(
                        _fee_dependency_value(
                            fee_id,
                            component_id,
                            dependency["amount"],
                        )
                    )
            else:
                for item in rule_inputs:
                    source = item.get("source")
                    key = str(item.get("inputKey") or "").strip()
                    input_sources.append(source)

                    if source == "fee":
                        dependency_ids.append(key)
                        dependency = evaluate(key)
                        if dependency["status"] == "blocked":
                            return blocked(
                                fee_id,
                                dependency["validationCodes"][0],
                                f"Dependency {key} could not be evaluated: "
                                f"{dependency['message']}",
                                dependency_ids,
                            )
                        values.append(
                            _fee_dependency_value(
                                fee_id,
                                key,
                                dependency["amount"],
                            )
                        )
                        continue

                    if source == "total":
                        component_ids = _validated_total_dependencies(fee, by_id)
                        if component_ids is None:
                            return blocked(
                                fee_id,
                                UNSUPPORTED_INPUT_CODE,
                                "Total of Fees in Template semantics are not validated for "
                                f"fee assignment {fee_id}.",
                                dependency_ids,
                            )
                        total_value = 0.0
                        for component_id in component_ids:
                            if component_id not in dependency_ids:
                                dependency_ids.append(component_id)
                            dependency = evaluate(component_id)
                            if dependency["status"] == "blocked":
                                return blocked(
                                    fee_id,
                                    dependency["validationCodes"][0],
                                    f"Dependency {component_id} could not be evaluated: "
                                    f"{dependency['message']}",
                                    dependency_ids,
                                )
                            total_value += float(dependency["amount"] or 0)
                        values.append(total_value)
                        continue

                    value, error = resolve_non_fee_input(fee_id, item)
                    if error is not None:
                        return error
                    values.append(value)

            if not values:
                return blocked(
                    fee_id,
                    MISSING_INPUT_CODE,
                    "Input calculation contains no evaluable inputs.",
                )

            if len(values) == 1:
                base_value = values[0]
            elif _validated_multi_input_sum(fee, rule_inputs):
                base_value = sum(values)
            else:
                return blocked(
                    fee_id,
                    MULTI_INPUT_CODE,
                    "Multiple-input aggregation semantics have not been validated for this fee.",
                    dependency_ids,
                )

            configured_rate = _number(rule.get("amount"))
            rate = _effective_input_rate(fee, rule)
            if rate is None:
                return blocked(
                    fee_id,
                    UNSUPPORTED_MODE_CODE,
                    "Input calculation does not contain a numeric configured amount/rate.",
                    dependency_ids,
                )

            amount = base_value * rate
            amount = _apply_bounds(amount, rule)
            try:
                amount = _round_amount(amount, rule)
            except EvaluationBlocked as exc:
                return blocked(
                    fee_id,
                    exc.code,
                    exc.message,
                    dependency_ids,
                )

            result = {
                "feeTemplateFeeId": fee_id,
                "status": "calculated",
                "amount": amount,
                "validationCodes": [],
                "message": None,
                "dependencies": dependency_ids,
                "inputSources": input_sources,
                "baseValue": base_value,
                "configuredRate": configured_rate,
                "rate": rate,
            }
            cache[fee_id] = result
            return result
        finally:
            visiting.discard(fee_id)

    return {
        str(fee_id): evaluate(str(fee_id))
        for fee_id in target_fee_ids
    }
