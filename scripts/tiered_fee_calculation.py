from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP


ROW_LINEAR_INPUT_KEYS = {
    "TotalMajorLFStrLN",
    "TotalMinorStreetLaneLF",
    "TotalMajorPLLFSecondary",
    "TotalMinorPLLFSecondary",
    "TotalMajorSLLFSecondary",
    "TotalMinorSLLFSecondary",
    "TotalMajorLF",
    "TotalMinorLFROW",
    "TotalMISCMajorNSLF",
    "TotalMISCMinorNSLF",
    "TotalMajorLFParkLane",
    "TotalMinorLFParkLane",
    "TotalMajorSWAUXLF",
    "TotalMajorSWFullLF",
    "TotalMajorSWMainLF",
    "TotalMajorSWPartialLF",
    "TotalMinorSWAUXLF",
    "TotalMinorSWLF",
    "TotalMinorSWMainLF",
    "TotalMinorSWPartialLF",
    "TotalMajorFSLFSecondary",
    "TotalMinorFSLFSecondary",
    "TotalMMNonSpecSecondary",
    "TotalMMiNonSpecSecondary",
    "TotalMajAUXSWLFSecondary",
    "TotalMajorSWLFSecondary",
    "TotalMajMainSWLFSecondary",
    "TotalMajPartSWLFSecondary",
    "TotalMinorAUXLFSecondary",
    "TotalMinorSWLFSecondary",
    "TotalMinorMAINLFSecondary",
    "TotalMinorPARTLFSecondary",
}

SUPPORTED_TIER_INPUTS = {
    "FinalDisturbedAcres": "tenths",
    "LengthFireSprinkler": "ceiling",
    "NumberofStandpipes": "integer",
    "NumberAppliances": "bands",
    "NumberofFixtures": "bands",
    "ValuationValue": "full-value",
    **{key: "row-linear" for key in ROW_LINEAR_INPUT_KEYS},
}


class TieredCalculationUnsupported(ValueError):
    pass


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _select_tier(tiers, input_value):
    for index, tier in enumerate(tiers or [], start=1):
        lower = _number(tier.get("lowerLimit"))
        upper = _number(tier.get("upperLimit"))
        unlimited = bool(tier.get("unlimited"))
        lower = 0.0 if lower is None else lower
        if input_value < lower:
            continue
        if unlimited or upper is None or input_value <= upper:
            return index, tier
    return None, None


def _extra_units(input_key, input_value, over, additional_unit):
    behavior = SUPPORTED_TIER_INPUTS.get(input_key)

    if behavior == "full-value":
        if over != 0:
            raise TieredCalculationUnsupported(
                "Validated valuation tiers require an over value of zero."
            )
        return input_value / additional_unit

    raw_units = (input_value - over) / additional_unit
    if raw_units <= 0:
        return 0.0

    if behavior == "tenths":
        return float(
            Decimal(str(raw_units)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )
    if behavior == "ceiling":
        return float(math.ceil(raw_units))
    if behavior == "integer":
        if not float(input_value).is_integer():
            raise TieredCalculationUnsupported(
                f"Tier input {input_key!r} is expected to be an integer count."
            )
        return raw_units
    if behavior == "bands":
        raise TieredCalculationUnsupported(
            f"Tier input {input_key!r} has an unexpected additional-unit charge."
        )
    if behavior == "row-linear":
        if over != 150 or additional_unit != 1:
            raise TieredCalculationUnsupported(
                "Validated ROW linear-foot tiers require over=150 and additionalUnit=1."
            )
        return raw_units
    raise TieredCalculationUnsupported(
        f"Tier input {input_key!r} does not have validated additional-unit semantics."
    )


def calculate_tiered_amount(input_key, input_value, tiers):
    """Calculate one validated EnerGov tiered fee rule.

    Supported evidence-backed families:
    - FinalDisturbedAcres: additional units rounded half-up to one decimal;
    - LengthFireSprinkler: additional units rounded up to whole units;
    - NumberofStandpipes: integer-count additional units;
    - NumberAppliances / NumberofFixtures: fixed tier bands only;
    - ValuationValue: selected tier base plus its rate applied to the full
      permit valuation. Current validated valuation tiers use over=0;
    - ROW linear-foot inputs: fixed base through 150 feet, then the selected
      tier base plus the configured per-foot amount for raw feet above 150.
    """
    input_value = _number(input_value)
    if input_value is None:
        raise TieredCalculationUnsupported("Tier input is not numeric.")
    if input_key not in SUPPORTED_TIER_INPUTS:
        raise TieredCalculationUnsupported(
            f"Tier input {input_key!r} has not been validated."
        )

    tier_number, tier = _select_tier(tiers, input_value)
    if tier is None:
        raise TieredCalculationUnsupported(
            f"No configured tier contains input value {input_value}."
        )

    amount = _number(tier.get("amount"))
    if amount is None:
        raise TieredCalculationUnsupported("Selected tier has no numeric amount.")

    additional_amount = _number(tier.get("additionalAmount")) or 0.0
    additional_unit = _number(tier.get("additionalUnit")) or 0.0
    over = _number(tier.get("over")) or 0.0

    if additional_amount > 0:
        if additional_unit <= 0:
            raise TieredCalculationUnsupported(
                "Selected tier has an additional amount without a positive additional unit."
            )
        units = _extra_units(
            input_key,
            input_value,
            over,
            additional_unit,
        )
        amount += units * additional_amount

    return {
        "amount": float(amount),
        "tierNumber": tier_number,
        "inputValue": input_value,
    }
