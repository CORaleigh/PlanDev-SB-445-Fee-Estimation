from __future__ import annotations

import argparse
import json
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from formula_fee_calculation import (
    PUMP_STATION_FIXED_FORMULA_ID,
    TREE_CONSERVATION_FORMULAS,
)
from input_fee_evaluator import (
    VALIDATED_FIXED_INPUT_ASSIGNMENTS,
    VALIDATED_PERCENTAGE_INPUT_FEE_NAMES,
    VALIDATED_TOTAL_BASED_TECHNOLOGY_COMPONENTS,
)
from option_ui_schema import build_option_ui_schema
from tiered_fee_calculation import SUPPORTED_TIER_INPUTS


PUBLIC_SCHEMA_VERSION = "public-1.0"
DEFAULT_MINIMUM_SAMPLE_SIZE = 10
GUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

SYSTEM_FIELDS = {
    (
        "EnerGovBusiness.PermitManagement.Permit",
        "ApplicationDate",
    ): "configuration-date",
    (
        "EnerGovBusiness.PermitManagement.Permit",
        "PermitTypeID",
    ): "selected-permit-type",
    (
        "EnerGovBusiness.PermitManagement.Permit",
        "PermitWorkClassID",
    ): "selected-work-class",
}

WWP_FEE_NAMES = {
    "working without a permit (electrical)",
    "working without a permit (fire)",
    "working without a permit (mechanical)",
    "working without a permit (plumbing)",
}

LATER_EVENT_PHRASES = (
    "voiding permit fee",
    "re-review",
    "rereview",
    "reinspection",
    "re-inspection",
    "extra inspect",
    "extra inspection",
    "field revision",
)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _public_id(prefix, index, width=3):
    return f"{prefix}{index:0{width}d}"


def _finite_number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rounded_percent(rate):
    return int(
        Decimal(str(float(rate) * 100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _public_history(
    evidence,
    minimum_sample_size,
    *,
    rate_key="rate",
):
    evidence = evidence or {}
    status = str(evidence.get("status") or "")
    rate = _finite_number(evidence.get(rate_key))
    denominator = _finite_number(evidence.get("denominator"))

    if (
        status not in {"available", "insufficient-sample"}
        or rate is None
        or rate < 0
        or rate > 1
    ):
        return {"status": "unavailable"}

    if denominator is None or denominator < minimum_sample_size:
        return {
            "status": "limited-history",
            "hasOccurrences": rate > 0,
        }

    return {
        "status": "available",
        "percent": _rounded_percent(rate),
    }


def _is_later_event_fee(fee):
    name = " ".join(str(fee.get("name") or "").split()).casefold()
    return name.startswith("fr - ") or any(
        phrase in name for phrase in LATER_EVENT_PHRASES
    )


def _scalar_key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _public_choice_label(label, fallback_number):
    text = str(label or "").strip()
    if not text or GUID_PATTERN.fullmatch(text):
        return f"Option {fallback_number}"
    return text


class OptionProjection:
    def __init__(
        self,
        document,
        *,
        option_id,
        permit_type_id,
        work_class_id,
        minimum_sample_size,
    ):
        self.document = document
        self.option_id = option_id
        self.public_permit_type_id = permit_type_id
        self.public_work_class_id = work_class_id
        self.minimum_sample_size = minimum_sample_size
        self.ui = build_option_ui_schema(document)

        self.fees = list(document.get("fees") or [])
        self.fee_ids = {}
        self.fee_row_ids = {}
        self.unknown_fee_ids = {}
        for index, fee in enumerate(self.fees, start=1):
            public_id = _public_id("f", index, 4)
            self.fee_row_ids[id(fee)] = public_id
            internal = str(fee.get("feeTemplateFeeId") or "").strip()
            if internal:
                self.fee_ids[internal] = public_id

        controls = list(self.ui.get("controls") or [])
        self.input_ids = {
            str(control.get("key") or ""): _public_id("i", index, 3)
            for index, control in enumerate(controls, start=1)
            if str(control.get("key") or "")
        }
        self.controls_by_key = {
            str(control.get("key") or ""): control
            for control in controls
            if str(control.get("key") or "")
        }
        self.choice_maps = {}
        self.choice_labels = {}
        for control in controls:
            key = str(control.get("key") or "")
            for choice in control.get("choices") or []:
                self._ensure_choice(
                    key,
                    choice.get("value"),
                    choice.get("label"),
                )

    def _fee_id(self, value):
        return self.fee_ids.get(str(value or "").strip())

    def _fee_row_id(self, fee):
        return self.fee_row_ids[id(fee)]

    def _fee_reference_id(self, value):
        internal = str(value or "").strip()
        public = self.fee_ids.get(internal)
        if public:
            return public
        if internal not in self.unknown_fee_ids:
            self.unknown_fee_ids[internal] = _public_id(
                "u", len(self.unknown_fee_ids) + 1, 4
            )
        return self.unknown_fee_ids[internal]

    def _input_id(self, value):
        return self.input_ids.get(str(value or "").strip())

    def _ensure_choice(self, input_key, value, label=None):
        values = self.choice_maps.setdefault(input_key, {})
        labels = self.choice_labels.setdefault(input_key, {})
        scalar = _scalar_key(value)
        if scalar not in values:
            public_value = _public_id("c", len(values) + 1, 3)
            values[scalar] = public_value
            labels[scalar] = _public_choice_label(label, len(values))
        elif label and labels.get(scalar, "").startswith("Option "):
            labels[scalar] = _public_choice_label(label, len(values))
        return values[scalar]

    def _public_value(self, input_key, value, label=None):
        control = self.controls_by_key.get(input_key) or {}
        value_type = str(control.get("valueType") or "")
        if value_type == "choice" or GUID_PATTERN.fullmatch(str(value or "")):
            return self._ensure_choice(input_key, value, label)
        return value

    def _hidden_input_id(self, input_key):
        if input_key not in self.input_ids:
            self.input_ids[input_key] = _public_id(
                "x", len(self.input_ids) + 1, 3
            )
        return self.input_ids[input_key]

    def _sanitize_condition_leaf(self, condition):
        raw_key = str(condition.get("operandValue") or "").strip()
        system_field = SYSTEM_FIELDS.get(
            (
                str(condition.get("className") or "").strip(),
                raw_key,
            )
        )
        public_key = system_field or self._input_id(raw_key)
        if not public_key:
            public_key = self._hidden_input_id(raw_key or "missing")

        result = {
            "operatorID": condition.get("operatorID"),
            "operandValue": public_key,
        }
        if system_field:
            result["systemField"] = system_field

        expression = condition.get("compareExpression") or {}
        if "value" in expression:
            value = expression.get("value")
            if system_field == "selected-permit-type":
                value = self.public_permit_type_id
            elif system_field == "selected-work-class":
                value = self.public_work_class_id
            elif not system_field:
                value = self._public_value(
                    raw_key,
                    value,
                    expression.get("label"),
                )
            result["compareExpression"] = {"value": value}
        return result

    def _sanitize_condition_group(self, group):
        group = group or {}
        return {
            "isOrGroup": bool(group.get("isOrGroup")),
            "childConditions": [
                self._sanitize_condition_leaf(item)
                for item in group.get("childConditions") or []
            ],
            "childConditionGroups": [
                self._sanitize_condition_group(item)
                for item in group.get("childConditionGroups") or []
            ],
        }

    def _sanitize_applicability(self, fee):
        applicability = fee.get("applicability") or {}
        conditions = []
        for item in applicability.get("conditions") or []:
            kind = str(item.get("kind") or "")
            public_item = {"kind": kind}
            if kind == "expression" and isinstance(
                item.get("sourceRule"), dict
            ):
                public_item["sourceRule"] = self._sanitize_condition_group(
                    item["sourceRule"]
                )
            conditions.append(public_item)
        return {
            "status": str(applicability.get("status") or ""),
            "conditions": conditions,
        }

    def _formula_behavior(self, fee, raw_rule):
        raw_fee_id = str(fee.get("feeTemplateFeeId") or "")
        expression = raw_rule.get("expression") or {}
        expression_type = expression.get("expressionType")
        raw_inputs = list(raw_rule.get("inputs") or [])

        if raw_fee_id == PUMP_STATION_FIXED_FORMULA_ID:
            return {
                "kind": "fixed-formula",
                "dependencyIds": [],
                "expressionType": expression_type,
            }

        tree_dependencies = TREE_CONSERVATION_FORMULAS.get(raw_fee_id)
        if tree_dependencies:
            mapped = [self._fee_id(value) for value in tree_dependencies]
            if all(mapped):
                return {
                    "kind": "minimum-times-rate",
                    "dependencyIds": mapped,
                    "expressionType": expression_type,
                }

        if (
            str(fee.get("name") or "").casefold().startswith("row -")
            and len(raw_inputs) == 2
            and all(item.get("source") == "fee" for item in raw_inputs)
        ):
            dependency_ids = [
                self._fee_id(item.get("inputKey")) for item in raw_inputs
            ]
            by_internal_id = {
                str(item.get("feeTemplateFeeId") or ""): item
                for item in self.fees
            }
            names = [
                str(
                    (
                        by_internal_id.get(str(item.get("inputKey") or ""))
                        or {}
                    ).get("name")
                    or item.get("label")
                    or ""
                ).casefold()
                for item in raw_inputs
            ]
            rate_indexes = [
                index
                for index, name in enumerate(names)
                if "rate (hidden)" in name
            ]
            days_indexes = [
                index
                for index, name in enumerate(names)
                if "occupancy global days" in name
            ]
            if (
                all(dependency_ids)
                and len(rate_indexes) == 1
                and len(days_indexes) == 1
            ):
                return {
                    "kind": "multiply",
                    "dependencyIds": dependency_ids,
                    "rateId": dependency_ids[rate_indexes[0]],
                    "daysId": dependency_ids[days_indexes[0]],
                    "expressionType": expression_type,
                }
        return None

    def _public_behavior(self, fee, raw_rule):
        raw_fee_id = str(fee.get("feeTemplateFeeId") or "")
        raw_inputs = list(raw_rule.get("inputs") or [])
        behavior = {}

        if raw_fee_id in VALIDATED_FIXED_INPUT_ASSIGNMENTS:
            behavior["fixedInput"] = True

        total_dependencies = VALIDATED_TOTAL_BASED_TECHNOLOGY_COMPONENTS.get(
            raw_fee_id
        )
        if total_dependencies:
            mapped = [self._fee_id(value) for value in total_dependencies]
            if all(mapped):
                behavior["totalComponentIds"] = mapped

        name = str(fee.get("name") or "").strip().casefold()
        if name in VALIDATED_PERCENTAGE_INPUT_FEE_NAMES:
            behavior["rateScale"] = 0.01

        if str((fee.get("calculation") or {}).get("mode") or "") == "tiered":
            if raw_inputs:
                tier_behavior = SUPPORTED_TIER_INPUTS.get(
                    str(raw_inputs[0].get("inputKey") or "")
                )
                if tier_behavior:
                    behavior["tierInputBehavior"] = tier_behavior

        if (
            len(raw_inputs) > 1
            and all(item.get("source") == "fee" for item in raw_inputs)
            and "technology surcharge" in name
        ):
            behavior["aggregate"] = "sum"

        formula = self._formula_behavior(fee, raw_rule)
        if formula:
            behavior["formula"] = formula
        return behavior

    def _sanitize_rule_input(self, item):
        source = str(item.get("source") or "")
        raw_key = str(item.get("inputKey") or "").strip()
        if source == "fee":
            return {
                "source": "fee",
                "inputKey": self._fee_reference_id(raw_key),
            }
        if source == "total":
            return {"source": "total", "inputKey": "fee-total"}
        if source == "constant":
            return {
                "source": "constant",
                "inputKey": "constant",
                "constant": item.get("constant"),
            }
        public_key = self._input_id(raw_key)
        if public_key:
            return {"source": "input", "inputKey": public_key}
        return {"source": "unsupported", "inputKey": "unavailable-input"}

    def _sanitize_rule(self, fee):
        calculation = fee.get("calculation") or {}
        raw_rule = calculation.get("rule") or {}
        result = {}
        for key in (
            "amount",
            "minimum",
            "maximum",
            "roundingType",
            "roundingValue",
        ):
            if key in raw_rule:
                result[key] = raw_rule.get(key)

        result["inputs"] = [
            self._sanitize_rule_input(item)
            for item in raw_rule.get("inputs") or []
        ]

        tiers = []
        for tier in raw_rule.get("tiers") or []:
            tiers.append(
                {
                    key: tier.get(key)
                    for key in (
                        "lowerLimit",
                        "upperLimit",
                        "unlimited",
                        "amount",
                        "over",
                        "additionalUnit",
                        "additionalAmount",
                    )
                    if key in tier
                }
            )
        if tiers:
            result["tiers"] = tiers

        behavior = self._public_behavior(fee, raw_rule)
        if behavior:
            result["publicBehavior"] = behavior
        return result

    def _project_fee(self, fee):
        calculation = fee.get("calculation") or {}
        name = str(fee.get("name") or "Unnamed fee")
        name_key = name.strip().casefold()
        result = {
            "feeTemplateFeeId": self._fee_row_id(fee),
            "name": name,
            "section": str(fee.get("section") or "unknown"),
            "assessmentRole": str(
                fee.get("assessmentRole") or "standalone-or-assessed"
            ),
            "applicability": self._sanitize_applicability(fee),
            "calculation": {
                "mode": str(calculation.get("mode") or "unsupported"),
                "fixedAmount": calculation.get("fixedAmount"),
                "rule": self._sanitize_rule(fee),
            },
        }
        if fee.get("estimationReview"):
            result["estimationReview"] = str(fee["estimationReview"])
        if name_key in WWP_FEE_NAMES:
            result["category"] = "working-without-permit"
        if _is_later_event_fee(fee):
            result["disposition"] = "later-event"
        return result

    def _project_controls(self):
        raw_groups = list(
            (self.ui.get("presentation") or {}).get("groups") or []
        )
        group_ids = {
            str(group.get("key") or ""): _public_id("g", index, 3)
            for index, group in enumerate(raw_groups, start=1)
        }
        public_groups = []
        for group in raw_groups:
            raw_key = str(group.get("key") or "")
            public_groups.append(
                {
                    "key": group_ids[raw_key],
                    "label": str(group.get("label") or "Project details"),
                    "kind": str(group.get("kind") or "supplemental"),
                    "collapsedByDefault": bool(
                        group.get("collapsedByDefault")
                    ),
                    "controlKeys": [
                        self._input_id(value)
                        for value in group.get("controlKeys") or []
                        if self._input_id(value)
                    ],
                }
            )

        controls = []
        for control in self.ui.get("controls") or []:
            raw_key = str(control.get("key") or "")
            public_key = self._input_id(raw_key)
            if not public_key:
                continue
            display_label = str(
                control.get("displayLabel")
                or control.get("label")
                or "Project detail"
            )
            public_control = {
                "key": public_key,
                "label": display_label,
                "displayLabel": display_label,
                "valueType": str(control.get("valueType") or "unknown"),
                "controlType": str(
                    control.get("controlType") or "unsupported"
                ),
                "displayReady": bool(control.get("displayReady")),
                "usages": list(control.get("usages") or []),
                "requiredForFeeIds": [
                    self._fee_id(value)
                    for value in control.get("requiredForFeeIds") or []
                    if self._fee_id(value)
                ],
                "choices": [],
                "historicalPrevalence": _public_history(
                    control.get("historicalPrevalence"),
                    self.minimum_sample_size,
                    rate_key="activeRate",
                ),
                "presentationGroup": None,
            }

            if public_control["controlType"] == "select":
                if "choiceContextStatus" in control:
                    public_control["choiceContextStatus"] = control["choiceContextStatus"]
                for choice in control.get("choices") or []:
                    scalar = _scalar_key(choice.get("value"))
                    public_control["choices"].append(
                        {
                            "value": self.choice_maps[raw_key][scalar],
                            "label": self.choice_labels[raw_key][scalar],
                        }
                    )

            group = control.get("presentationGroup") or {}
            group_key = str(group.get("key") or "")
            if group_key in group_ids:
                public_control["presentationGroup"] = {
                    "key": group_ids[group_key],
                    "label": str(group.get("label") or "Project details"),
                    "kind": str(group.get("kind") or "supplemental"),
                    "collapsedByDefault": bool(
                        group.get("collapsedByDefault")
                    ),
                }
            controls.append(public_control)

        blockers = []
        for item in self.ui.get("blockingRuntimeDiagnostics") or []:
            blockers.append(
                {
                    "code": str(item.get("code") or "REVIEW_REQUIRED"),
                    "feeTemplateFeeId": self._fee_id(
                        item.get("feeTemplateFeeId")
                    ),
                    "feeName": str(item.get("feeName") or "Fee"),
                }
            )

        ui_blockers = []
        for item in self.ui.get("uiBlockers") or []:
            field_key = self._input_id(item.get("fieldKey"))
            if field_key:
                ui_blockers.append(
                    {
                        "code": str(item.get("code") or "INPUT_REVIEW"),
                        "fieldKey": field_key,
                    }
                )

        presentation = self.ui.get("presentation") or {}
        return {
            "controls": controls,
            "presentation": {
                "mode": str(presentation.get("mode") or "flat"),
                "groups": public_groups,
            },
            "uiBlockers": ui_blockers,
            "blockingRuntimeDiagnostics": blockers,
            "runtimeReady": bool(self.ui.get("runtimeReady")),
            "displayReady": bool(self.ui.get("displayReady")),
        }

    def _raw_history_available(self, evidence):
        evidence = evidence or {}
        rate = _finite_number(evidence.get("rate"))
        return (
            str(evidence.get("status") or "")
            in {"available", "insufficient-sample"}
            and rate is not None
            and 0 <= rate <= 1
        )

    def _project_related_permits(self):
        result = []
        for relation in self.document.get("relatedPermits") or []:
            active = relation.get("activeFeeOccurrence") or {}
            presence = relation.get("occurrence") or {}
            if self._raw_history_available(active):
                evidence = active
                basis = "active-fee"
            elif self._raw_history_available(presence):
                evidence = presence
                basis = "permit"
            else:
                continue

            public_fees = []
            for fee in relation.get("fees") or []:
                if str(fee.get("assessmentRole") or "") == "dependency-only":
                    continue
                if str(fee.get("section") or "") == "inactive":
                    continue
                fee_occurrence = fee.get("occurrence") or {}
                if not self._raw_history_available(fee_occurrence):
                    continue
                rate = _finite_number(fee_occurrence.get("rate"))
                if rate is None or rate <= 0:
                    continue
                public_fees.append(
                    {
                        "name": str(fee.get("name") or "Fee"),
                        "history": _public_history(
                            fee_occurrence,
                            self.minimum_sample_size,
                        ),
                    }
                )

            result.append(
                {
                    "permitType": str(relation.get("permitType") or ""),
                    "workClass": str(relation.get("workClass") or ""),
                    "relationshipFamily": str(
                        relation.get("relationshipFamily") or ""
                    ),
                    "historyBasis": basis,
                    "history": _public_history(
                        evidence,
                        self.minimum_sample_size,
                    ),
                    "fees": public_fees,
                }
            )
        return result

    def project(self):
        configuration = self.document.get("configuration") or {}
        historical_period = configuration.get("historicalPeriod") or {}
        option = self.document.get("option") or {}
        public_fees = [self._project_fee(fee) for fee in self.fees]
        return {
            "schemaVersion": PUBLIC_SCHEMA_VERSION,
            "catalogVersion": self.document.get("catalogVersion"),
            "configuration": {
                "asOfDate": configuration.get("asOfDate"),
                "historicalPeriod": {
                    "startDate": historical_period.get("startDate"),
                    "endDate": historical_period.get("endDate"),
                },
            },
            "option": {
                "optionKey": self.option_id,
                "permitTypeKey": self.public_permit_type_id,
                "permitType": str(option.get("permitType") or ""),
                "workClassKey": self.public_work_class_id,
                "workClass": str(option.get("workClass") or ""),
            },
            "ui": self._project_controls(),
            "fees": public_fees,
            "relatedPermits": self._project_related_permits(),
            "disclosures": [
                {"code": str(item.get("code") or "")}
                for item in self.document.get("disclosures") or []
                if str(item.get("code") or "")
            ],
        }


def project_catalog(
    catalog_dir,
    destination,
    *,
    minimum_sample_size=DEFAULT_MINIMUM_SAMPLE_SIZE,
):
    catalog_dir = Path(catalog_dir)
    destination = Path(destination)
    staging = destination.with_name(destination.name + ".tmp")
    source_root, target_root = catalog_dir.resolve(), destination.resolve()
    if source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise ValueError("Catalog source and destination directories must not overlap.")
    for target in (destination, staging):
        if target.exists():
            raise FileExistsError(f"Choose a new public-data directory; path already exists: {target}")
    manifest = load_json(catalog_dir / "manifest.json")
    options = list(manifest.get("options") or [])
    if not options:
        raise ValueError("Catalog manifest contains no options.")
    if minimum_sample_size < 1:
        raise ValueError("minimum_sample_size must be positive.")

    permit_type_ids = {}
    work_class_ids = {}
    documents = []
    seen = set()
    for item in options:
        relative = Path(str(item.get("path") or ""))
        source = catalog_dir / relative
        if relative.parent != Path("options") or relative.suffix != ".json" or source.resolve().parent != source_root / "options":
            raise ValueError(f"Unexpected catalog option path: {relative}")
        if relative.as_posix().casefold() in seen:
            raise ValueError(f"Duplicate catalog option path: {relative}")
        seen.add(relative.as_posix().casefold())
        if not source.is_file():
            raise FileNotFoundError(f"Catalog option was not found: {source}")
        document = load_json(source)
        option = document.get("option") or {}
        internal_type = str(option.get("permitTypeId") or "")
        internal_class = str(option.get("workClassId") or "")
        if internal_type not in permit_type_ids:
            permit_type_ids[internal_type] = _public_id(
                "t", len(permit_type_ids) + 1, 3
            )
        if internal_class not in work_class_ids:
            work_class_ids[internal_class] = _public_id(
                "w", len(work_class_ids) + 1, 3
            )
        documents.append(document)

    (staging / "options").mkdir(parents=True)

    public_options = []
    for index, (item, document) in enumerate(zip(options, documents), start=1):
        option_id = _public_id("o", index, 3)
        option = document.get("option") or {}
        projection = OptionProjection(
            document,
            option_id=option_id,
            permit_type_id=permit_type_ids[
                str(option.get("permitTypeId") or "")
            ],
            work_class_id=work_class_ids[
                str(option.get("workClassId") or "")
            ],
            minimum_sample_size=minimum_sample_size,
        )
        public_document = projection.project()
        relative = f"options/{option_id}.json"
        write_json(staging / relative, public_document)
        public_options.append(
            {
                "optionKey": option_id,
                "permitType": str(item.get("permitType") or ""),
                "workClass": str(item.get("workClass") or ""),
                "path": relative,
            }
        )

    public_manifest = {
        "schemaVersion": PUBLIC_SCHEMA_VERSION,
        "catalogVersion": manifest.get("catalogVersion"),
        "generatedAtUtc": manifest.get("generatedAtUtc"),
        "configurationAsOfDate": manifest.get("configurationAsOfDate"),
        "historyMinimumSampleSize": minimum_sample_size,
        "options": public_options,
    }
    write_json(staging / "manifest.json", public_manifest)

    staging.replace(destination)
    return {
        "catalogVersion": public_manifest.get("catalogVersion"),
        "configurationAsOfDate": public_manifest.get(
            "configurationAsOfDate"
        ),
        "options": len(public_options),
        "destination": str(destination),
        "minimumSampleSize": minimum_sample_size,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Build the reduced static-site data package from the complete "
            "internal estimator catalog."
        )
    )
    parser.add_argument("catalog_dir")
    parser.add_argument("--destination", required=True)
    parser.add_argument(
        "--minimum-sample-size",
        type=int,
        default=DEFAULT_MINIMUM_SAMPLE_SIZE,
    )
    args = parser.parse_args()
    result = project_catalog(
        args.catalog_dir,
        args.destination,
        minimum_sample_size=args.minimum_sample_size,
    )
    print("PUBLIC ESTIMATOR DATA BUILT")
    print(f"Catalog version: {result['catalogVersion']}")
    print(f"Options: {result['options']}")
    print(f"Minimum history sample: {result['minimumSampleSize']}")
    print(f"Destination: {result['destination']}")


if __name__ == "__main__":
    main()
