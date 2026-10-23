from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


ORDER = (
    "requires-revalidation",
    "compatibility-review",
    "new-unknown",
    "safe-configuration-change",
)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compact_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return repr(value)


def summarize(report, limit=12):
    rows = report.get("changes") or []
    by_class = Counter(row.get("classification") for row in rows)
    reasons = defaultdict(Counter)
    fields = defaultdict(Counter)
    transitions = defaultdict(lambda: defaultdict(Counter))
    examples = defaultdict(list)
    fee_names = defaultdict(Counter)
    options = defaultdict(Counter)
    assignment_ids = defaultdict(set)

    for row in rows:
        cls = row.get("classification") or "unknown"
        reasons[cls][row.get("reason") or "(no reason)"] += 1
        fee_names[cls][row.get("feeName") or "(unnamed fee)"] += 1
        option = " / ".join(
            value
            for value in (
                row.get("permitType"),
                row.get("workClass"),
            )
            if value
        )
        options[cls][option or "(unknown option)"] += 1
        assignment = (
            row.get("newFeeTemplateFeeId")
            or row.get("oldFeeTemplateFeeId")
        )
        if assignment:
            assignment_ids[cls].add(str(assignment))

        for change in row.get("changes") or []:
            path = change.get("path") or "(unknown field)"
            fields[cls][path] += 1
            if path in {
                "classification",
                "estimateMode",
                "selectable",
                "requiresInput",
                "flags.active",
                "flags.manual",
                "flags.disabled",
                "calculation.pricingStatus",
            }:
                transition = (
                    f"{compact_value(change.get('old'))} -> "
                    f"{compact_value(change.get('new'))}"
                )
                transitions[cls][path][transition] += 1

        if len(examples[cls]) < 5:
            examples[cls].append(
                " / ".join(
                    value
                    for value in (
                        row.get("permitType"),
                        row.get("workClass"),
                        row.get("feeName"),
                    )
                    if value
                )
            )

    print(
        "Snapshot: "
        f"{report.get('oldAsOfDate')} -> {report.get('newAsOfDate')}"
    )
    print(f"Total material changes: {len(rows)}")

    for cls in ORDER:
        count = by_class.get(cls, 0)
        if not count:
            continue

        print(f"\n{cls}: {count}")
        print(
            "  Scope: "
            f"{len(assignment_ids[cls])} assignments / "
            f"{len(options[cls])} options / "
            f"{len(fee_names[cls])} fee names"
        )

        print("  Top reasons:")
        for reason, n in reasons[cls].most_common(limit):
            print(f"    {n:>4}  {reason}")

        if fields[cls]:
            print("  Top changed fields:")
            for field, n in fields[cls].most_common(limit):
                print(f"    {n:>4}  {field}")

        if transitions[cls]:
            print("  Key transitions:")
            for field in (
                "flags.disabled",
                "flags.manual",
                "flags.active",
                "classification",
                "estimateMode",
                "selectable",
                "requiresInput",
                "calculation.pricingStatus",
            ):
                values = transitions[cls].get(field)
                if not values:
                    continue
                for transition, n in values.most_common(8):
                    print(
                        f"    {field}: {n}  {transition}"
                    )

        print("  Top fee names:")
        for name, n in fee_names[cls].most_common(8):
            print(f"    {n:>4}  {name}")

        print("  Top options:")
        for option, n in options[cls].most_common(8):
            print(f"    {n:>4}  {option}")

        if examples[cls]:
            print("  Examples:")
            for example in examples[cls]:
                print(f"    - {example}")


def assignment_key(row):
    return str(
        row.get("newFeeTemplateFeeId")
        or row.get("oldFeeTemplateFeeId")
        or "(no assignment id)"
    )


def assignment_details(report):
    groups = defaultdict(list)
    for row in report.get("changes") or []:
        groups[(row.get("classification"), assignment_key(row))].append(row)

    print("\nASSIGNMENT-LEVEL DETAIL")
    for (classification, assignment), rows in sorted(
        groups.items(),
        key=lambda item: (
            ORDER.index(item[0][0]) if item[0][0] in ORDER else 99,
            item[0][1],
        ),
    ):
        representative = rows[0]
        option_count = len(
            {
                (row.get("permitType"), row.get("workClass"))
                for row in rows
            }
        )
        changed = defaultdict(Counter)
        for row in rows:
            for change in row.get("changes") or []:
                path = change.get("path") or "(unknown field)"
                transition = (
                    f"{compact_value(change.get('old'))} -> "
                    f"{compact_value(change.get('new'))}"
                )
                changed[path][transition] += 1

        print(
            f"\n{classification} | {assignment} | "
            f"{representative.get('feeName')} | "
            f"{len(rows)} rows / {option_count} options"
        )

        preferred = (
            "flags.disabled",
            "flags.manual",
            "flags.active",
            "calculation.pricingStatus",
            "classification",
            "estimateMode",
            "selectable",
            "requiresInput",
            "calculation.inputs",
            "calculation.amount",
            "calculation.computationValue",
            "calculation.startDate",
            "calculation.endDate",
        )
        seen = set()
        for path in preferred:
            if path not in changed:
                continue
            seen.add(path)
            for transition, count in changed[path].most_common(5):
                print(f"  {path} ({count}): {transition}")

        for path in sorted(set(changed) - seen):
            values = changed[path]
            transition, count = values.most_common(1)[0]
            suffix = (
                f" (+{len(values) - 1} more transition(s))"
                if len(values) > 1
                else ""
            )
            print(f"  {path} ({count}): {transition}{suffix}")


def main():
    parser = argparse.ArgumentParser(
        description="Print a compact summary of an estimator refresh change report."
    )
    parser.add_argument("report")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument(
        "--assignment-details",
        action="store_true",
        help="Also print one compact block per unique fee assignment.",
    )
    args = parser.parse_args()

    report = load(args.report)
    summarize(report, args.limit)
    if args.assignment_details:
        assignment_details(report)


if __name__ == "__main__":
    main()
