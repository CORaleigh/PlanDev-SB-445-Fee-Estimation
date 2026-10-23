from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_JSON = "output/permit_fee_templates.json"
DEFAULT_AS_OF = "2026-09-15"


def parse_date(value):
    if not value:
        return None

    text = str(value).strip()

    if not text:
        return None

    # EnerGov dates may include time / timezone information.
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).date()
    except ValueError:
        pass

    # Fallback for common date-only representation.
    try:
        return datetime.strptime(
            text[:10],
            "%Y-%m-%d",
        ).date()
    except ValueError:
        return None


def nonempty(value):
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, dict)):
        return bool(value)

    return True


def is_true(value):
    if value is True:
        return True

    if isinstance(value, str):
        return value.strip().lower() == "true"

    return False


def choose_current_setup(setups, as_of):
    """
    Choose one pricing setup for the requested date.

    Priority:
      1. Most recent dated setup effective on as_of.
      2. Undated setup as fallback.
      3. None.
    """
    dated_effective = []
    undated = []

    for setup in setups or []:
        if not isinstance(setup, dict):
            continue

        start = parse_date(setup.get("startDate"))
        end = parse_date(setup.get("endDate"))

        if start is None and end is None:
            undated.append(setup)
            continue

        starts_ok = start is None or start <= as_of
        ends_ok = end is None or end >= as_of

        if starts_ok and ends_ok:
            dated_effective.append(
                (start or datetime.min.date(), setup)
            )

    if dated_effective:
        dated_effective.sort(
            key=lambda x: x[0],
            reverse=True,
        )
        return dated_effective[0][1], "Dated effective"

    if undated:
        return undated[0], "Undated fallback"

    return None, "No effective setup"


def has_variable_input(fee):
    inputs = fee.get("feeInputs") or []

    if not inputs:
        return False

    for item in inputs:
        if not isinstance(item, dict):
            continue

        # A fee input tied to something other than a pure fixed
        # constant suggests the amount depends on record/user data.
        if is_true(item.get("isCustomField")):
            return True

        if is_true(item.get("isTotalBased")):
            return True

        if is_true(item.get("isFeeBased")):
            return True

        if not is_true(item.get("isConstantBased")):
            return True

        calc_type = item.get("feeCalculationInputType")

        if nonempty(calc_type):
            return True

    return False


def has_tiered_calculation(setup):
    if not setup:
        return False

    rows = setup.get("feeAdjustableCalcs") or []

    if not rows:
        return False

    # Typical EnerGov adjustable rows contain lower/upper limits,
    # amount, additional amount/unit, etc.
    return any(
        isinstance(row, dict)
        and (
            nonempty(row.get("lowerLimit"))
            or nonempty(row.get("upperLimit"))
            or nonempty(row.get("additionalAmount"))
            or nonempty(row.get("additionalUnit"))
            or nonempty(row.get("over"))
        )
        for row in rows
    )


def classify_fee(fee, setup, pricing_status):
    """
    Assign one primary classification.

    Child-template status is handled separately because it is
    orthogonal to the fee calculation type.
    """

    if (
        is_true(fee.get("isDisable"))
        or fee.get("active") is False
    ):
        return "Disabled / inactive"

    if pricing_status == "No effective setup":
        return "No current setup"

    if is_true(fee.get("isManual")):
        return "Manual"

    expression = fee.get("expression")

    if nonempty(expression):
        return "Formula-based"

    if has_tiered_calculation(setup):
        return "Tiered"

    if has_variable_input(fee):
        return "Input / per-unit"

    # computationValueName is another strong signal that the
    # setup depends on a named calculation variable.
    if setup and nonempty(
        setup.get("computationValueName")
    ):
        return "Input / per-unit"

    # At this point there are no obvious variable calculation
    # structures. A configured amount behaves like a fixed fee.
    if setup and setup.get("amount") is not None:
        return "Fixed-dollar"

    return "Other / review"


def walk_templates(
    permit_record,
    node,
    as_of,
    rows,
    depth=0,
    path=None,
):
    path = list(path or [])

    template_name = (
        node.get("feeTemplateName")
        or "(unnamed template)"
    )

    path.append(template_name)

    root_template = (
        permit_record.get("Template", {})
        .get("feeTemplate", {})
    )

    for fee in node.get("fees") or []:
        if not isinstance(fee, dict):
            continue

        setup, pricing_status = choose_current_setup(
            fee.get("feeSetups") or [],
            as_of,
        )

        classification = classify_fee(
            fee,
            setup,
            pricing_status,
        )

        rows.append(
            {
                "permit_type": permit_record.get(
                    "PermitType", ""
                ),
                "work_class": permit_record.get(
                    "WorkClass", ""
                ),
                "root_template": root_template.get(
                    "feeTemplateName", ""
                ),
                "source_template": template_name,
                "template_depth": depth,
                "template_relationship": (
                    "Direct"
                    if depth == 0
                    else "Child"
                ),
                "template_path": " > ".join(path),
                "fee_template_fee_id": fee.get(
                    "feeTemplateFeeID", ""
                ),
                "fee_id": fee.get("feeID", ""),
                "fee_name": fee.get("feeName", ""),
                "fee_type": fee.get("feeType", ""),
                "classification": classification,
                "pricing_status": pricing_status,
                "amount": (
                    setup.get("amount")
                    if setup
                    else None
                ),
                "minimum": (
                    setup.get("minimum")
                    if setup
                    else None
                ),
                "maximum": (
                    setup.get("maximum")
                    if setup
                    else None
                ),
                "start_date": (
                    setup.get("startDate")
                    if setup
                    else None
                ),
                "end_date": (
                    setup.get("endDate")
                    if setup
                    else None
                ),
                "manual": fee.get("isManual"),
                "disabled": fee.get("isDisable"),
                "active": fee.get("active"),
                "expression": fee.get(
                    "expression"
                ),
                "fee_inputs": len(
                    fee.get("feeInputs") or []
                ),
                "adjustable_rows": len(
                    (
                        setup.get(
                            "feeAdjustableCalcs"
                        )
                        if setup
                        else []
                    )
                    or []
                ),
            }
        )

    for child in node.get("childTemplates") or []:
        if not isinstance(child, dict):
            continue

        walk_templates(
            permit_record=permit_record,
            node=child,
            as_of=as_of,
            rows=rows,
            depth=depth + 1,
            path=path,
        )


def print_counter(title, counter, total=None):
    print()
    print(title)
    print("-" * len(title))

    if not counter:
        print("(none)")
        return

    width = max(len(str(k)) for k in counter)

    for key, count in sorted(
        counter.items(),
        key=lambda x: (-x[1], str(x[0])),
    ):
        if total:
            pct = count / total * 100
            print(
                f"{str(key):<{width}}  "
                f"{count:>7,}  "
                f"{pct:>6.1f}%"
            )
        else:
            print(
                f"{str(key):<{width}}  "
                f"{count:>7,}"
            )


def print_examples(rows, classification, n=5):
    matches = [
        row
        for row in rows
        if row["classification"] == classification
    ]

    if not matches:
        return

    print()
    print(f"Examples: {classification}")
    print("-" * (10 + len(classification)))

    seen = set()

    shown = 0

    for row in matches:
        key = (
            row["permit_type"],
            row["work_class"],
            row["fee_name"],
            row["source_template"],
        )

        if key in seen:
            continue

        seen.add(key)

        amount = row["amount"]

        if isinstance(amount, (int, float)):
            amount_text = f"${amount:,.2f}"
        else:
            amount_text = str(amount or "")

        relationship = row[
            "template_relationship"
        ]

        print(
            f"- {row['permit_type']} | "
            f"{row['work_class']} | "
            f"{row['fee_name']} | "
            f"{amount_text} | "
            f"{relationship}"
        )

        shown += 1

        if shown >= n:
            break


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Analyze configured EnerGov permit fees "
            "from permit_fee_templates.json."
        )
    )

    parser.add_argument(
        "json_file",
        nargs="?",
        default=DEFAULT_JSON,
        help=(
            "Path to permit_fee_templates.json "
            f"(default: {DEFAULT_JSON})"
        ),
    )

    parser.add_argument(
        "--as-of",
        default=DEFAULT_AS_OF,
        help=(
            "Date used to select the effective fee "
            f"setup (default: {DEFAULT_AS_OF})"
        ),
    )

    parser.add_argument(
        "--examples",
        type=int,
        default=5,
        help=(
            "Number of examples printed for each "
            "classification (default: 5)"
        ),
    )

    args = parser.parse_args()

    json_path = Path(args.json_file)

    if not json_path.exists():
        raise FileNotFoundError(
            f"Could not find {json_path}"
        )

    as_of = datetime.strptime(
        args.as_of,
        "%Y-%m-%d",
    ).date()

    with json_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        data = json.load(handle)

    rows = []
    no_fee_template = []

    for record in data:
        template = record.get("Template") or {}
        root = template.get("feeTemplate")

        if not isinstance(root, dict) or not root:
            no_fee_template.append(
                (
                    record.get("PermitType", ""),
                    record.get("WorkClass", ""),
                )
            )
            continue

        walk_templates(
            permit_record=record,
            node=root,
            as_of=as_of,
            rows=rows,
        )

    print("=" * 78)
    print("ENERGOV PERMIT FEE CONFIGURATION")
    print("=" * 78)

    print(f"As of:                     {as_of}")
    print(
        f"Permit type/work classes:  "
        f"{len(data):,}"
    )
    print(
        f"With fee template:          "
        f"{len(data) - len(no_fee_template):,}"
    )
    print(
        f"Without fee template:       "
        f"{len(no_fee_template):,}"
    )
    print(
        f"Configured fee assignments: "
        f"{len(rows):,}"
    )

    classification_counts = Counter(
        row["classification"]
        for row in rows
    )

    relationship_counts = Counter(
        row["template_relationship"]
        for row in rows
    )

    pricing_counts = Counter(
        row["pricing_status"]
        for row in rows
    )

    print_counter(
        "Primary fee classification",
        classification_counts,
        total=len(rows),
    )

    print_counter(
        "Template relationship",
        relationship_counts,
        total=len(rows),
    )

    print_counter(
        "Pricing setup status",
        pricing_counts,
        total=len(rows),
    )

    # Classification x direct/child
    print()
    print("Classification by template relationship")
    print("---------------------------------------")

    cross = defaultdict(
        lambda: Counter()
    )

    for row in rows:
        cross[row["classification"]][
            row["template_relationship"]
        ] += 1

    heading_width = max(
        len(x)
        for x in cross
    )

    print(
        f"{'Classification':<{heading_width}}  "
        f"{'Direct':>8}  "
        f"{'Child':>8}  "
        f"{'Total':>8}"
    )

    for classification in sorted(
        cross,
        key=lambda x: -sum(
            cross[x].values()
        ),
    ):
        direct = cross[
            classification
        ]["Direct"]

        child = cross[
            classification
        ]["Child"]

        print(
            f"{classification:<{heading_width}}  "
            f"{direct:>8,}  "
            f"{child:>8,}  "
            f"{direct + child:>8,}"
        )

    # Counts by permit type/work class.
    combo_counts = Counter(
        (
            row["permit_type"],
            row["work_class"],
        )
        for row in rows
    )

    print()
    print("Permit type/work class combinations with most configured fees")
    print("-----------------------------------------------------------")

    for (
        permit_type,
        work_class,
    ), count in combo_counts.most_common(20):
        print(
            f"{count:>5,}  "
            f"{permit_type} | "
            f"{work_class}"
        )

    if no_fee_template:
        print()
        print("Permit type/work class combinations with no fee template")
        print("--------------------------------------------------------")

        for permit_type, work_class in no_fee_template:
            print(
                f"- {permit_type} | "
                f"{work_class}"
            )

    for classification in (
        "Fixed-dollar",
        "Input / per-unit",
        "Tiered",
        "Formula-based",
        "Manual",
        "Disabled / inactive",
        "No current setup",
        "Other / review",
    ):
        print_examples(
            rows,
            classification,
            n=args.examples,
        )


if __name__ == "__main__":
    main()