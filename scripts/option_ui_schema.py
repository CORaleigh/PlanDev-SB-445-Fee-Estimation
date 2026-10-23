from __future__ import annotations

import re
from collections import Counter

from option_runtime import compile_option_schema


UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
DENSE_OPTION_CONTROL_THRESHOLD = 12

# Curated presentation-only aliases for known internal/malformed labels.
# Keys use the same separator/case-insensitive identity as _label_identity.
DISPLAY_LABEL_ALIASES = {
    "workingwithouta": "Working without a",
    "bothmechandele": "Both Mechanical and Electrical",
}


def _identifier_tokens(value):
    text = str(value or "").strip()
    if not text:
        return []
    text = re.sub(r"[_-]+", " ", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return [token for token in text.split() if token]


def _humanize_identifier(value):
    tokens = _identifier_tokens(value)
    if not tokens:
        return ""

    upper = [token.upper() for token in tokens]
    if len(tokens) >= 2 and upper[-1] == "LF":
        tokens = tokens[:-1] + ["Linear Feet"]
    elif (
        len(tokens) >= 2
        and upper[-2:] == ["LF", "TOTAL"]
    ):
        tokens = tokens[:-2] + ["Linear Feet"]
    else:
        normalized = []
        for token in tokens:
            token_upper = token.upper()
            if token_upper == "LF":
                normalized.append("Linear Feet")
            elif token_upper in {"ID", "ROW", "CCTV", "ADA", "PM"}:
                normalized.append(token_upper)
            else:
                normalized.append(token)
        tokens = normalized

    return " ".join(tokens).strip()


def _label_identity(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _curated_display_label(value):
    return DISPLAY_LABEL_ALIASES.get(_label_identity(value))


EXTERNAL_OPTION_COMBO_REPLACEMENTS = (
    ("PLNR/ELNR/MENR", "Plumbing / Electrical / Mechanical - Non-Residential"),
    ("PLR/ELR/MER", "Plumbing / Electrical / Mechanical - Residential"),
    ("MENR/ELNR", "Mechanical / Electrical - Non-Residential"),
    ("MER/ELR", "Mechanical / Electrical - Residential"),
    ("PLNR/ELNR", "Plumbing / Electrical - Non-Residential"),
    ("PLNR/MENR", "Plumbing / Mechanical - Non-Residential"),
    ("PLR/ELR", "Plumbing / Electrical - Residential"),
    ("PLR/MER", "Plumbing / Mechanical - Residential"),
)

FEE_DISCIPLINE_CODE_REPLACEMENTS = (
    ("PLMNR", "Non-Residential Plumbing"),
    ("PLMR", "Residential Plumbing"),
    ("ELNR", "Non-Residential Electrical"),
    ("ELR", "Residential Electrical"),
    ("MENR", "Non-Residential Mechanical"),
    ("MER", "Residential Mechanical"),
)


def _strip_technical_context_suffix(value):
    text = str(value or "").strip()
    if not text.endswith(")"):
        return text

    marker_positions = []
    for pattern in (
        r"\bPM\s*-\s*\[",
        r"Fire Prev Misc Permit\s*-",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            marker_positions.append(match.start())

    if not marker_positions:
        return text

    marker_position = max(marker_positions)
    opening = text.rfind("(", 0, marker_position + 1)
    if opening < 0:
        return text
    return text[:opening].rstrip()


def clean_staff_facing_label(
    value,
    *,
    surface="general",
    strip_technical_context=False,
):
    text = str(value or "").strip()
    if not text:
        return text

    if strip_technical_context:
        text = _strip_technical_context_suffix(text)

    exact = _curated_display_label(text)
    if exact:
        text = exact

    replacements = (
        (r"Both_Mech_and_Ele", "Both Mechanical and Electrical"),
        (r"\bEle_Contractor\b", "Electrical Contractor"),
        (r"\bEle Contractor\b", "Electrical Contractor"),
        (r"\bMec_Contractor\b", "Mechanical Contractor"),
        (r"\bMec Contractor\b", "Mechanical Contractor"),
        (
            r"\bIndustrialManufacturingAgricultural\b",
            "Industrial / Manufacturing / Agricultural",
        ),
        (r"\bMultiFamilydwellings\b", "Multifamily Dwellings"),
        (r"\bWorkingwithouta\b", "Working without a"),
        (r"\bNumberof\b", "Number of"),
        (r"\bTypeof\b", "Type of"),
        (
            r"\bAUTOFIREEXTINGUISHIGNSYSTEMS\b",
            "Auto Fire Extinguishing Systems",
        ),
        (
            r"\bAUTOFIREEXTINGUISHINGSYSTEMS\b",
            "Auto Fire Extinguishing Systems",
        ),
        (
            r"\bBARRICADESFORAPPARATUSACCESS\b",
            "Barricades for Apparatus Access",
        ),
        (r"\bCYROGENICFLUIDSYSTEMS\b", "Cryogenic Fluid Systems"),
        (
            r"\bEMERGENCYRESPONDERRADIOSYSTEMS\b",
            "Emergency Responder Radio Systems",
        ),
        (r"\bSMOKECONTROLSYSTEMS\b", "Smoke Control Systems"),
        (
            r"\bSOLARPHOTOVOLTAICSYSTEMS\b",
            "Solar Photovoltaic Systems",
        ),
        (
            r"\bAUTO FIRE EXTINGUISHIGN SYSTEMS\b",
            "Auto Fire Extinguishing Systems",
        ),
        (r"\bALTERNATIVE SYSTEMS\b", "Alternative Systems"),
        (r"\bBATTERY SYSTEMS\b", "Battery Systems"),
        (r"\bCOMPRESSESD GASES\b", "Compressed Gases"),
        (r"\bCOMPRESSED GASES\b", "Compressed Gases"),
        (
            r"\bFLAMMABLE/COMBUST LIQUIDS\b",
            "Flammable/Combustible Liquids",
        ),
        (r"\bHAZARDOUS MATERIAL\b", "Hazardous Material"),
        (r"\bINDUSTRIAL OVEN\b", "Industrial Ovens"),
        (r"\bMEMBRANE STRUCT\b", "Membrane Structures"),
        (r"\bPR Vs\b", "PRVs"),
        (r"\bTempCertificate\b", "Temporary Certificate"),
        (r"\bSqFt\b", "Sq Ft"),
        (r"\bFacililty\b", "Facility"),
        (r"\bCombustable\b", "Combustible"),
        (r"\bCYROGENIC\b", "CRYOGENIC"),
        (r"\bEXTINGUISHIGN\b", "EXTINGUISHING"),
        (r"\bCOMPRESSESD\b", "COMPRESSED"),
        (
            r"\bSingle Family Less Than1000\b",
            "Single Family Less Than 1,000",
        ),
        (
            r"\bSingle Family3000to3999\b",
            "Single Family 3,000 to 3,999",
        ),
        (r"\bSingle Family > 5000\b", "Single Family > 5,000"),
        (
            r"\bStadiums Coliseums Race Tracks\b",
            "Stadiums / Coliseums / Race Tracks",
        ),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    text = re.sub(r"\bNon-Res\b", "Non-Residential", text)
    text = re.sub(r"\bNonRes\b", "Non-Residential", text)
    text = re.sub(r"\bMech\b", "Mechanical", text)
    text = re.sub(r"\bMec\b", "Mechanical", text)
    text = re.sub(r"\bEle\b", "Electrical", text)
    text = re.sub(r"\bFam\b", "Family", text)
    text = re.sub(r"\bResid\b", "Residential", text)
    text = re.sub(r"\bRes\b", "Residential", text)

    if surface == "fee":
        for code, replacement in FEE_DISCIPLINE_CODE_REPLACEMENTS:
            text = text.replace(f"[{code}]", f"[{replacement}]")
        text = text.replace(
            "[NR Electrical]",
            "[Non-Residential Electrical]",
        )
        text = re.sub(r"\bNR\b", "Non-Residential", text)

    if surface == "choice":
        if text.casefold() == "city-wake forest":
            text = "City - Wake Forest"
        elif text.casefold() == "etj-wake forest":
            text = "ETJ - Wake Forest"

    return re.sub(r"\s{2,}", " ", text).strip()


def display_option_label(value, mode="external"):
    raw = str(value or "").strip()
    if mode == "internal":
        return raw
    if mode != "external":
        raise ValueError(f"Unsupported option label mode: {mode}")

    text = re.sub(
        r"^(?:DSINSP|DSENG|DSLC|PRCR|PU|FIRE|ENG|ZP|MISC)\s*[-–]\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    for internal, external in EXTERNAL_OPTION_COMBO_REPLACEMENTS:
        text = text.replace(internal, external)

    text = re.sub(r"\bChangeout\b", "Change Out", text, flags=re.IGNORECASE)
    text = re.sub(
        r"Change Out-\s*Combo",
        "Change Out - Combo",
        text,
        flags=re.IGNORECASE,
    )
    return clean_staff_facing_label(text, surface="option")


def _display_label(field):
    key = str(field.get("key") or "").strip()
    label = str(field.get("label") or key).strip()

    key_suffix_removed = False
    if key:
        suffix = re.compile(
            rf"^(.*?)\s*\({re.escape(key)}(?:,\s*[^)]*)?\)\s*$",
            flags=re.IGNORECASE,
        )
        match = suffix.match(label)
        if match:
            label = str(match.group(1) or "").strip()
            key_suffix_removed = True

    label = clean_staff_facing_label(
        label,
        surface="control",
        strip_technical_context=True,
    )

    if (
        key_suffix_removed
        and re.search(r"\bQUANTITIES\b", label, flags=re.IGNORECASE)
    ):
        return clean_staff_facing_label(
            _curated_display_label(key)
            or _humanize_identifier(key)
            or key,
            surface="control",
        )

    if not label or _label_identity(label) == _label_identity(key):
        return clean_staff_facing_label(
            _curated_display_label(key)
            or _humanize_identifier(key)
            or key,
            surface="control",
        )

    return label


def _disambiguate_display_labels(controls):
    by_label = {}
    for control in controls:
        label = str(control.get("displayLabel") or "").strip()
        if not label:
            continue
        by_label.setdefault(label.casefold(), []).append(control)

    for group in by_label.values():
        if len(group) < 2:
            continue

        fallback = [
            _humanize_identifier(control.get("key"))
            or str(control.get("key") or "").strip()
            for control in group
        ]
        if len({value.casefold() for value in fallback}) == len(group):
            for control, value in zip(group, fallback):
                control["displayLabel"] = value
            continue

        for control, value in zip(group, fallback):
            base = str(control.get("displayLabel") or "").strip()
            control["displayLabel"] = (
                f"{base} ({value})" if base else value
            )


def _display_choice(choice):
    rendered = dict(choice)
    label = str(rendered.get("label") or "").strip()
    rendered["label"] = clean_staff_facing_label(
        label,
        surface="choice",
    )
    return rendered


def _choice_display_ready(choice):
    label = str(choice.get("label") or "").strip()
    value = choice.get("value")
    if isinstance(value, bool):
        return True
    if not label:
        return False
    if UUID_RE.match(label):
        return False
    if UUID_RE.match(str(value or "")) and label == str(value):
        return False
    return True


def _duplicate_choice_labels(choices):
    values_by_label = {}
    display_labels = {}
    for choice in choices or []:
        label = str(choice.get("label") or "").strip()
        if not label:
            continue
        identity = label.casefold()
        value = choice.get("value")
        browser_value = "" if value is None else str(value).strip()
        values_by_label.setdefault(identity, set()).add(browser_value)
        display_labels.setdefault(identity, label)

    return sorted(
        display_labels[identity]
        for identity, values in values_by_label.items()
        if len(values) > 1
    )


def _direct_input_prevalence(document, key):
    key = str(key or "").strip()
    for item in document.get("inputPrevalence") or []:
        if str(item.get("inputKey") or "").strip() != key:
            continue
        if str(item.get("status") or "") != "available":
            break
        return {
            **item,
            "status": "available",
            "inputKey": key,
            "basis": "direct-input-prevalence-query",
        }
    return {
        "status": "unavailable",
        "inputKey": key,
        "basis": "direct-input-prevalence-query",
    }


def _control_for_field(field):
    value_type = str(field.get("valueType") or "unknown")
    choices = [
        _display_choice(choice)
        for choice in (field.get("choices") or [])
    ]
    duplicate_choice_labels = _duplicate_choice_labels(choices)

    if value_type in {"number", "nullable-number"}:
        control_type = "number"
        display_ready = True
    elif value_type in {"boolean", "presence"}:
        control_type = "boolean"
        display_ready = True
    elif value_type == "choice":
        control_type = "select"
        display_ready = (
            bool(choices)
            and field.get("choiceContextStatus") != "unavailable"
            and not duplicate_choice_labels
            and all(
                _choice_display_ready(choice)
                for choice in choices
            )
        )
    else:
        control_type = "unsupported"
        display_ready = False

    return {
        "key": field.get("key"),
        "label": field.get("label"),
        "displayLabel": _display_label(field),
        "controlType": control_type,
        "valueType": value_type,
        "source": field.get("source"),
        "usages": list(field.get("usages") or []),
        "requiredForFeeIds": list(field.get("requiredForFeeIds") or []),
        "choices": choices,
        "duplicateChoiceLabels": duplicate_choice_labels,
        **({"choiceContextStatus": field["choiceContextStatus"]} if "choiceContextStatus" in field else {}),
        "displayReady": display_ready,
        "presentationGroup": None,
    }


def _scenario_group_label(control):
    label = str(control.get("label") or "").strip()
    match = re.search(
        r"\((.+?)\s*-\s*PM\s*-\s*\[[^\]]+\][^)]*\)\s*$",
        label,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    raw = str(match.group(1) or "").strip()
    if not raw:
        return None
    parts = []
    for part in re.split(r"\s+-\s+", raw):
        friendly = part.lower().title()
        friendly = re.sub(r"\bPod\b", "POD", friendly)
        friendly = re.sub(r"\bRow\b", "ROW", friendly)
        parts.append(
            clean_staff_facing_label(
                _curated_display_label(friendly) or friendly,
                surface="group",
            )
        )
    return " - ".join(parts)


def _presentation_group_key(kind, label):
    slug = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")
    return f"{kind}:{slug or 'group'}"


def _template_relationships(document, control):
    required_ids = {
        str(value)
        for value in control.get("requiredForFeeIds") or []
        if value
    }
    return {
        str(fee.get("templateRelationship") or "").strip().lower()
        for fee in document.get("fees") or []
        if str(fee.get("feeTemplateFeeId") or "") in required_ids
        and str(fee.get("templateRelationship") or "").strip()
    }


def _presentation_tokens(value):
    return {
        token
        for token in re.sub(
            r"[^a-z0-9]+",
            " ",
            str(value or "").lower(),
        ).split()
        if token not in {
            "row",
            "fee",
            "review",
            "hidden",
            "rate",
            "p",
            "s",
        }
    }


def _related_fee_names(document, control):
    required_ids = {
        str(value)
        for value in control.get("requiredForFeeIds") or []
        if value
    }
    return [
        str(fee.get("name") or "").strip()
        for fee in document.get("fees") or []
        if str(fee.get("feeTemplateFeeId") or "") in required_ids
        and str(fee.get("name") or "").strip()
    ]


def _inferred_scenario_group_label(
    document,
    control,
    scenario_labels,
):
    fee_tokens = set()
    for name in _related_fee_names(document, control):
        fee_tokens.update(_presentation_tokens(name))
    if not fee_tokens:
        return None

    best_label = None
    best_score = 0.0
    tied = False
    for label in scenario_labels:
        scenario_tokens = _presentation_tokens(label)
        if not scenario_tokens:
            continue
        score = (
            len(scenario_tokens & fee_tokens)
            / len(scenario_tokens)
        )
        if score > best_score:
            best_label = label
            best_score = score
            tied = False
        elif score == best_score and score > 0:
            tied = True

    if not tied and best_score >= 0.75:
        return best_label
    return None


def _apply_control_presentation(document, controls):
    ready = [control for control in controls if control.get("displayReady")]
    if len(ready) < DENSE_OPTION_CONTROL_THRESHOLD:
        return {
            "mode": "flat",
            "denseThreshold": DENSE_OPTION_CONTROL_THRESHOLD,
            "initialControlCount": len(ready),
            "groupedControlCount": 0,
            "groups": [],
        }

    scenario_labels = Counter(
        label
        for control in ready
        for label in [_scenario_group_label(control)]
        if label
    )

    candidate_groups = {}
    for control in ready:
        scenario_label = (
            _scenario_group_label(control)
            or _inferred_scenario_group_label(
                document,
                control,
                scenario_labels,
            )
        )
        group = None
        if (
            scenario_label
            and scenario_labels.get(scenario_label, 0) >= 2
        ):
            group = {
                "key": _presentation_group_key(
                    "scenario",
                    scenario_label,
                ),
                "label": scenario_label,
                "kind": "scenario",
                "collapsedByDefault": True,
            }
        else:
            usages = list(control.get("usages") or [])
            applicability_only = usages == ["applicability"]
            inherited = "child" in _template_relationships(
                document,
                control,
            )
            if inherited or applicability_only:
                group = {
                    "key": "supplemental:additional-fee-details",
                    "label": "Additional Fee Details",
                    "kind": "supplemental",
                    "collapsedByDefault": True,
                }
        if group:
            candidate_groups[control["key"]] = group

    group_counts = Counter(
        group["key"] for group in candidate_groups.values()
    )
    groups = []
    group_by_key = {}
    grouped_count = 0
    for control in controls:
        group = candidate_groups.get(control.get("key"))
        if group and group_counts[group["key"]] >= 2:
            control["presentationGroup"] = dict(group)
            grouped_count += int(bool(control.get("displayReady")))
            if group["key"] not in group_by_key:
                entry = {**group, "controlKeys": []}
                group_by_key[group["key"]] = entry
                groups.append(entry)
            group_by_key[group["key"]]["controlKeys"].append(
                control.get("key")
            )

    return {
        "mode": "progressive" if groups else "flat",
        "denseThreshold": DENSE_OPTION_CONTROL_THRESHOLD,
        "initialControlCount": len(ready) - grouped_count,
        "groupedControlCount": grouped_count,
        "groups": groups,
    }


def derive_inactive_scenario_fee_ids(ui_schema, raw_values=None):
    raw_values = dict(raw_values or {})
    controls = [
        control
        for control in ui_schema.get("controls") or []
        if control.get("displayReady")
    ]
    groups = [
        group
        for group in (ui_schema.get("presentation") or {}).get("groups") or []
        if group.get("kind") == "scenario"
    ]
    if not groups:
        return []

    control_by_key = {
        str(control.get("key") or ""): control
        for control in controls
    }
    protected_fee_ids = set()
    group_keys_by_fee = {}
    active_group_keys = set()

    def control_is_active(control):
        key = str(control.get("key") or "")
        if key not in raw_values:
            return False
        raw = raw_values[key]
        if raw is None:
            return False

        if (
            control.get("controlType") == "number"
            or control.get("valueType") == "number"
        ):
            if isinstance(raw, bool):
                return False
            text = str(raw).strip()
            if not text:
                return False
            try:
                return abs(float(text)) > 0.0000005
            except (TypeError, ValueError):
                return False

        if isinstance(raw, str):
            return bool(raw.strip())
        return True

    for control in controls:
        presentation_group = control.get("presentationGroup") or {}
        if presentation_group.get("kind") == "scenario":
            continue
        for fee_id in control.get("requiredForFeeIds") or []:
            fee_id = str(fee_id or "").strip()
            if fee_id:
                protected_fee_ids.add(fee_id)

    for group in groups:
        group_key = str(group.get("key") or "")
        group_controls = [
            control_by_key[str(key)]
            for key in group.get("controlKeys") or []
            if str(key) in control_by_key
        ]
        if any(control_is_active(control) for control in group_controls):
            active_group_keys.add(group_key)

        for control in group_controls:
            for fee_id in control.get("requiredForFeeIds") or []:
                fee_id = str(fee_id or "").strip()
                if not fee_id:
                    continue
                group_keys_by_fee.setdefault(fee_id, set()).add(
                    group_key
                )

    inactive = []
    for fee_id, owner_groups in group_keys_by_fee.items():
        if fee_id in protected_fee_ids:
            continue
        if any(group in active_group_keys for group in owner_groups):
            continue
        inactive.append(fee_id)

    return sorted(inactive)


def build_option_ui_schema(document):
    """
    Build a permit-agnostic UI contract from one catalog option.

    Presentation groups are also permit-agnostic. Dense forms can collapse
    repeated scenario-template contexts and inherited/applicability-only
    supplemental inputs without changing evaluation semantics.
    """
    runtime_schema = compile_option_schema(document)
    controls = [
        {
            **_control_for_field(field),
            "historicalPrevalence": _direct_input_prevalence(
                document,
                field.get("key"),
            ),
        }
        for field in runtime_schema.get("fields") or []
    ]
    _disambiguate_display_labels(controls)
    presentation = _apply_control_presentation(document, controls)
    ui_blockers = []

    for control in controls:
        if control["controlType"] == "unsupported":
            ui_blockers.append(
                {
                    "code": "UNSUPPORTED_UI_FIELD_TYPE",
                    "fieldKey": control["key"],
                    "message": (
                        "The field cannot yet be rendered by the generic UI."
                    ),
                }
            )
        elif not control["displayReady"]:
            duplicate_labels = control.get("duplicateChoiceLabels") or []
            ui_blockers.append(
                {
                    "code": (
                        "CHOICE_CONTEXT_NOT_AVAILABLE"
                        if control.get("choiceContextStatus") == "unavailable"
                        else "DUPLICATE_CHOICE_LABELS_NOT_DISPLAY_READY"
                        if duplicate_labels
                        else "CHOICE_LABELS_NOT_DISPLAY_READY"
                    ),
                    "fieldKey": control["key"],
                    "message": (
                        "The selected form does not identify an available choice field."
                        if control.get("choiceContextStatus") == "unavailable"
                        else "More than one configured value uses the same visible "
                        "choice label."
                        if duplicate_labels
                        else (
                            "Friendly configured labels are required before "
                            "this choice field can be shown to staff."
                        )
                    ),
                }
            )

    return {
        "optionKey": runtime_schema.get("optionKey"),
        "permitType": runtime_schema.get("permitType"),
        "workClass": runtime_schema.get("workClass"),
        "controls": controls,
        "presentation": presentation,
        "uiBlockers": ui_blockers,
        "runtimeCompileBlockers": list(
            runtime_schema.get("compileBlockers") or []
        ),
        "blockingRuntimeDiagnostics": list(
            runtime_schema.get("blockingCompileBlockers") or []
        ),
        "reviewRuntimeDiagnostics": list(
            runtime_schema.get("reviewCompileDiagnostics") or []
        ),
        "runtimeReady": bool(runtime_schema.get("runtimeSchemaReady")),
        "displayReady": not ui_blockers,
    }
