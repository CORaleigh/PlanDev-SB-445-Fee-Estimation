const EQUALITY_OPERATOR_ID = 1;
const NOT_EQUAL_OPERATOR_ID = 2;
const GREATER_THAN_OPERATOR_ID = 3;
const BLANK_OPERATOR_ID = 7;
const PRESENT_OPERATOR_ID = 8;
const SUPPORTED_OPERATOR_IDS = new Set([
  EQUALITY_OPERATOR_ID,
  NOT_EQUAL_OPERATOR_ID,
  GREATER_THAN_OPERATOR_ID,
  BLANK_OPERATOR_ID,
  PRESENT_OPERATOR_ID,
]);

const NON_USER_INPUT_SOURCES = new Set(["fee", "total", "constant"]);
const UI_NON_INPUT_SECTIONS = new Set([
  "manual-review",
  "event-contingent",
  "inactive",
]);

const REVIEW_ONLY_SECTIONS = new Set(["manual-review", "inactive"]);

function numberValue(value) {
  if (typeof value === "boolean" || value === null || value === undefined) {
    return null;
  }
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function roundMoney(value) {
  return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
}

function normalizeScalar(value) {
  if (typeof value === "boolean" || value === null || value === undefined) {
    return value ?? null;
  }
  const text = String(value).trim().toLowerCase();
  if (["true", "yes", "y", "1"].includes(text)) return true;
  if (["false", "no", "n", "0"].includes(text)) return false;
  return text;
}

function feeId(fee) {
  return String(fee?.feeTemplateFeeId ?? "");
}

function calculationRule(fee) {
  return fee?.calculation?.rule ?? {};
}

function ruleInputs(fee) {
  return Array.from(calculationRule(fee).inputs ?? []);
}

function requiresApplicabilityResolution(fee) {
  const section = String(fee?.section ?? "");
  const status = String(fee?.applicability?.status ?? "");
  if (["conditional", "event-contingent", "manual-review", "inactive"].includes(section)) {
    return true;
  }
  if (["conditional", "event-contingent", "manual-review", "inactive"].includes(status)) {
    return true;
  }
  return Array.isArray(fee?.conditions) && fee.conditions.length > 0;
}

function isReviewOnlyFee(fee) {
  const section = String(fee?.section ?? "");
  const mode = String(fee?.calculation?.mode ?? "");
  return REVIEW_ONLY_SECTIONS.has(section) || mode === "manual";
}

function isLaterEventFee(fee) {
  return String(fee?.disposition ?? "") === "later-event";
}

function systemFieldPolicy(condition) {
  const value = String(condition?.systemField ?? "").trim();
  return value || null;
}

function configurationApplicationDateTime(document) {
  const asOf = String(document?.configuration?.asOfDate ?? "").trim();
  if (!asOf) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(asOf)) return `${asOf}T00:00:00`;
  return asOf;
}

function systemApplicabilityValues(document) {
  const values = {};
  const applicationDate = configurationApplicationDateTime(document);
  const permitTypeId = String(document?.option?.permitTypeKey ?? "").trim();
  const workClassId = String(document?.option?.workClassKey ?? "").trim();
  if (permitTypeId) values["selected-permit-type"] = permitTypeId;
  if (workClassId) values["selected-work-class"] = workClassId;
  if (applicationDate !== null) {
    values["configuration-date"] = applicationDate;
  }
  return values;
}

function fieldEntry(fields, key, label, source) {
  const normalizedKey = String(key ?? "").trim();
  if (!normalizedKey) return null;
  if (!fields.has(normalizedKey)) {
    fields.set(normalizedKey, {
      key: normalizedKey,
      label: String(label || normalizedKey),
      source: source || "unknown",
      valueType: "unknown",
      usages: [],
      requiredForFeeIds: [],
      choices: [],
    });
  }
  const entry = fields.get(normalizedKey);
  if (label && (!entry.label || entry.label === entry.key)) {
    entry.label = String(label);
  }
  if (source && [null, undefined, "", "unknown"].includes(entry.source)) {
    entry.source = source;
  }
  return entry;
}

function appendUnique(items, value) {
  if (!items.some((item) => JSON.stringify(item) === JSON.stringify(value))) {
    items.push(value);
  }
}

function mergeValueType(current, expected) {
  if (current === "unknown") return expected;
  if (current === expected) return current;
  if (
    current === "nullable-number"
    && ["number", "presence"].includes(expected)
  ) {
    return "nullable-number";
  }
  if (
    expected === "nullable-number"
    && ["number", "presence"].includes(current)
  ) {
    return "nullable-number";
  }
  if (new Set([current, expected]).size === 2
      && [current, expected].includes("number")
      && [current, expected].includes("presence")) {
    return "nullable-number";
  }
  return "mixed";
}

function conditionLeaves(group) {
  const result = [];
  for (const condition of group?.childConditions ?? []) result.push(condition);
  for (const child of group?.childConditionGroups ?? []) {
    result.push(...conditionLeaves(child));
  }
  return result;
}

function blockerContext(fee) {
  const section = String(fee?.section ?? "");
  return {
    feeTemplateFeeId: feeId(fee),
    feeName: String(fee?.name ?? ""),
    section,
    sourceTemplate: String(fee?.sourceTemplate ?? ""),
    templateRelationship: String(fee?.templateRelationship ?? ""),
    severity: ["manual-review", "event-contingent", "inactive"].includes(section)
      ? "review-only"
      : "blocking",
  };
}

function applyChoiceContext(document, fields) {
  const context = document?.choiceContext;
  if (!context || typeof context !== "object") return;
  const referenced = new Map();
  for (const fee of document.fees ?? []) {
    for (const group of fee.applicability?.conditions ?? []) {
      for (const condition of conditionLeaves(group.sourceRule)) {
        const expression = condition.compareExpression ?? {};
        if (!Object.hasOwn(expression, "value")) continue;
        const key = String(condition.operandValue ?? "").trim();
        if (!referenced.has(key)) referenced.set(key, new Set());
        referenced.get(key).add(String(expression.value).trim().toLowerCase());
      }
    }
  }
  for (const entry of fields.values()) {
    if (entry.valueType !== "choice"
        || !(entry.isCustomField || entry.source === "custom-field")) continue;
    const matches = (context.fields ?? []).filter((field) =>
      field.key === entry.key && field.active === true);
    entry.choiceContextStatus = "unavailable";
    entry.choices = [];
    if (!context.layoutId || matches.length !== 1) continue;
    const field = matches[0];
    if (!field.fieldId || field.multiple !== false) continue;
    entry.choices = (field.choices ?? []).filter((choice) =>
      String(choice.label ?? "").trim()
      || referenced.get(entry.key)?.has(String(choice.value).trim().toLowerCase()));
    entry.choiceContextStatus = entry.choices.length ? "available" : "unavailable";
    entry.label = field.label || entry.label;
  }
}

export function compileOptionSchema(document) {
  const publicUi = document?.ui;
  if (Array.isArray(publicUi?.controls)) {
    const fields = publicUi.controls.map((control) => ({
      key: String(control?.key ?? ""),
      label: String(control?.label ?? control?.displayLabel ?? "Project detail"),
      source: "public-runtime",
      valueType: String(control?.valueType ?? "unknown"),
      usages: Array.from(control?.usages ?? []),
      requiredForFeeIds: Array.from(control?.requiredForFeeIds ?? []),
      choices: Array.from(control?.choices ?? []),
      choiceContextStatus: control?.choiceContextStatus,
    }));
    const blockers = Array.from(publicUi.blockingRuntimeDiagnostics ?? []);
    const option = document?.option ?? {};
    return {
      optionKey: option.optionKey,
      permitType: option.permitType,
      workClass: option.workClass,
      fields,
      compileBlockers: blockers,
      blockingCompileBlockers: blockers,
      reviewCompileDiagnostics: [],
      runtimeSchemaReady: Boolean(publicUi.runtimeReady),
    };
  }

  const fields = new Map();
  const blockers = [];
  const configuredInputs = new Map(
    (document.requiredInputs ?? [])
      .filter((item) => item?.key || item?.inputKey)
      .map((item) => [String(item.key || item.inputKey).trim(), item]),
  );

  for (const fee of document.fees ?? []) {
    const id = feeId(fee);
    const uiNonInputFee = UI_NON_INPUT_SECTIONS.has(String(fee?.section ?? ""));
    if (!uiNonInputFee) {
      for (const item of ruleInputs(fee)) {
        const source = String(item?.source ?? "unknown");
        if (NON_USER_INPUT_SOURCES.has(source)) continue;
        const key = String(item?.inputKey ?? "").trim();
        if (!key) continue;
        const configured = configuredInputs.get(key) ?? {};
        const entry = fieldEntry(
          fields,
          key,
          configured.label || item.label || key,
          configured.source || source,
        );
        appendUnique(entry.usages, "calculation");
        if (id) appendUnique(entry.requiredForFeeIds, id);
        entry.valueType = mergeValueType(entry.valueType, "number");
        if (entry.valueType === "nullable-number") entry.choices = [];
      }
    }

    for (const conditionDocument of fee?.applicability?.conditions ?? []) {
      const kind = String(conditionDocument?.kind ?? "");
      if (kind !== "expression") {
        blockers.push({
          ...blockerContext(fee),
          conditionKind: kind || "unknown",
          code: "UNSUPPORTED_APPLICABILITY_CONDITION",
          message: `Unsupported applicability condition kind: ${kind || "unknown"}.`,
        });
        continue;
      }

      const sourceRule = conditionDocument?.sourceRule;
      if (!sourceRule || typeof sourceRule !== "object") {
        blockers.push({
          ...blockerContext(fee),
          conditionKind: "expression",
          code: "MISSING_CONDITION_RULE",
          message: "Expression condition has no source rule.",
        });
        continue;
      }

      for (const condition of conditionLeaves(sourceRule)) {
        const operatorId = Number(condition?.operatorID);
        if (!SUPPORTED_OPERATOR_IDS.has(operatorId)) {
          blockers.push({
            ...blockerContext(fee),
            conditionKind: "expression",
            operatorID: Number.isFinite(operatorId) ? operatorId : null,
            operandValue: condition?.operandValue,
            operandFriendlyName: condition?.operandFriendlyName,
            comparisonValue: condition?.compareExpression?.value,
            code: "UNSUPPORTED_CONDITION_OPERATOR",
            message: `Condition operator ${condition?.operatorID} is not validated.`,
          });
          continue;
        }

        const key = String(condition?.operandValue ?? "").trim();
        const expression = condition?.compareExpression ?? {};
        if (
          !key
          || (
            [
              EQUALITY_OPERATOR_ID,
              NOT_EQUAL_OPERATOR_ID,
              GREATER_THAN_OPERATOR_ID,
            ].includes(operatorId)
            && !Object.prototype.hasOwnProperty.call(expression, "value")
          )
        ) {
          blockers.push({
            ...blockerContext(fee),
            conditionKind: "expression",
            operatorID: operatorId,
            operandValue: condition?.operandValue,
            code: "INCOMPLETE_CONDITION",
            message: "Applicability condition is missing a field or comparison value.",
          });
          continue;
        }

        const expected = expression.value;
        if (uiNonInputFee) continue;

        const systemPolicy = systemFieldPolicy(condition);
        if (systemPolicy !== null) {
          if (!Object.prototype.hasOwnProperty.call(
            systemApplicabilityValues(document),
            key,
          )) {
            blockers.push({
              ...blockerContext(fee),
              conditionKind: "expression",
              operatorID: operatorId,
              operandValue: key,
              code: "MISSING_SYSTEM_APPLICABILITY_VALUE",
              message: "A system-resolved applicability field has no value in the catalog context.",
            });
          }
          continue;
        }

        const entry = fieldEntry(
          fields,
          key,
          condition?.operandFriendlyName || key,
          "applicability",
        );
        if (condition?.operandTypeID === 3) entry.isCustomField = true;
        appendUnique(entry.usages, "applicability");
        if (id) appendUnique(entry.requiredForFeeIds, id);

        let expectedType;
        let choices;
        if ([BLANK_OPERATOR_ID, PRESENT_OPERATOR_ID].includes(operatorId)) {
          expectedType = "presence";
          choices = [
            { value: true, label: "Yes" },
            { value: false, label: "No" },
          ];
        } else if (operatorId === GREATER_THAN_OPERATOR_ID) {
          expectedType = "number";
          choices = [];
        } else {
          expectedType = typeof expected === "boolean" ? "boolean" : "choice";
          choices = typeof expected === "boolean"
            ? [
                { value: true, label: "Yes" },
                { value: false, label: "No" },
              ]
            : [{
                value: expected,
                label: String(expression?.label ?? expected),
              }];
        }

        entry.valueType = mergeValueType(entry.valueType, expectedType);
        if (entry.valueType === "nullable-number") {
          entry.choices = [];
        } else {
          for (const choice of choices) appendUnique(entry.choices, choice);
        }
      }
    }
  }

  applyChoiceContext(document, fields);
  const normalizedFields = Array.from(fields.values()).map((entry) => ({
    ...entry,
    usages: [...entry.usages].sort(),
    requiredForFeeIds: [...entry.requiredForFeeIds].sort(),
    choices: [...entry.choices].sort((a, b) =>
      String(a.label ?? "").localeCompare(String(b.label ?? ""))),
  }));
  const blocking = blockers.filter((item) => item.severity === "blocking");
  const reviewOnly = blockers.filter((item) => item.severity === "review-only");
  const option = document.option ?? {};

  return {
    optionKey: option.optionKey,
    permitType: option.permitType,
    workClass: option.workClass,
    fields: normalizedFields,
    compileBlockers: blockers,
    blockingCompileBlockers: blocking,
    reviewCompileDiagnostics: reviewOnly,
    runtimeSchemaReady: blocking.length === 0,
  };
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function identifierTokens(value) {
  let text = String(value ?? "").trim();
  if (!text) return [];
  text = text
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2");
  return text.split(/\s+/).filter(Boolean);
}

function humanizeIdentifier(value) {
  let tokens = identifierTokens(value);
  if (!tokens.length) return "";

  const upper = tokens.map((token) => token.toUpperCase());
  if (
    tokens.length >= 2
    && upper.at(-1) === "LF"
  ) {
    tokens = [...tokens.slice(0, -1), "Linear Feet"];
  } else if (
    tokens.length >= 2
    && upper.slice(-2).join("|") === "LF|TOTAL"
  ) {
    tokens = [...tokens.slice(0, -2), "Linear Feet"];
  } else {
    tokens = tokens.map((token) => {
      const normalized = token.toUpperCase();
      if (normalized === "LF") return "Linear Feet";
      if (["ID", "ROW", "CCTV", "ADA", "PM"].includes(normalized)) {
        return normalized;
      }
      return token;
    });
  }
  return tokens.join(" ").trim();
}

const DISPLAY_LABEL_ALIASES = new Map([
  ["workingwithouta", "Working without a"],
  ["bothmechandele", "Both Mechanical and Electrical"],
]);

const EXTERNAL_OPTION_COMBO_REPLACEMENTS = [
  ["PLNR/ELNR/MENR", "Plumbing / Electrical / Mechanical - Non-Residential"],
  ["PLR/ELR/MER", "Plumbing / Electrical / Mechanical - Residential"],
  ["MENR/ELNR", "Mechanical / Electrical - Non-Residential"],
  ["MER/ELR", "Mechanical / Electrical - Residential"],
  ["PLNR/ELNR", "Plumbing / Electrical - Non-Residential"],
  ["PLNR/MENR", "Plumbing / Mechanical - Non-Residential"],
  ["PLR/ELR", "Plumbing / Electrical - Residential"],
  ["PLR/MER", "Plumbing / Mechanical - Residential"],
];

const FEE_DISCIPLINE_CODE_REPLACEMENTS = [
  ["PLMNR", "Non-Residential Plumbing"],
  ["PLMR", "Residential Plumbing"],
  ["ELNR", "Non-Residential Electrical"],
  ["ELR", "Residential Electrical"],
  ["MENR", "Non-Residential Mechanical"],
  ["MER", "Residential Mechanical"],
];

function labelIdentity(value) {
  return String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function curatedDisplayLabel(value) {
  return DISPLAY_LABEL_ALIASES.get(labelIdentity(value)) ?? null;
}

function stripTechnicalContextSuffix(value) {
  const text = String(value ?? "").trim();
  if (!text.endsWith(")")) return text;

  const markerPositions = [
    text.search(/\bPM\s*-\s*\[/i),
    text.search(/Fire Prev Misc Permit\s*-/i),
  ].filter((position) => position >= 0);

  if (!markerPositions.length) return text;

  const markerPosition = Math.max(...markerPositions);
  const opening = text.lastIndexOf("(", markerPosition);
  if (opening < 0) return text;
  return text.slice(0, opening).trim();
}

export function cleanStaffFacingLabel(
  value,
  { surface = "general", stripTechnicalContext = false } = {},
) {
  let text = String(value ?? "").trim();
  if (!text) return text;

  if (stripTechnicalContext) {
    text = stripTechnicalContextSuffix(text);
  }

  const exact = curatedDisplayLabel(text);
  if (exact) text = exact;

  const literalReplacements = [
    [/Both_Mech_and_Ele/gi, "Both Mechanical and Electrical"],
    [/\bEle_Contractor\b/gi, "Electrical Contractor"],
    [/\bEle Contractor\b/gi, "Electrical Contractor"],
    [/\bMec_Contractor\b/gi, "Mechanical Contractor"],
    [/\bMec Contractor\b/gi, "Mechanical Contractor"],
    [/\bIndustrialManufacturingAgricultural\b/g, "Industrial / Manufacturing / Agricultural"],
    [/\bMulti[\s-]*Family\s*Dwellings\b/gi, "Multifamily Dwellings"],
    [/\bWorkingwithouta\b/gi, "Working without a"],
    [/\bNumberof\b/gi, "Number of"],
    [/\bTypeof\b/gi, "Type of"],
    [/\bAUTOFIREEXTINGUISHIGNSYSTEMS\b/g, "Auto Fire Extinguishing Systems"],
    [/\bAUTOFIREEXTINGUISHINGSYSTEMS\b/g, "Auto Fire Extinguishing Systems"],
    [/\bBARRICADESFORAPPARATUSACCESS\b/g, "Barricades for Apparatus Access"],
    [/\bCYROGENICFLUIDSYSTEMS\b/g, "Cryogenic Fluid Systems"],
    [/\bEMERGENCYRESPONDERRADIOSYSTEMS\b/g, "Emergency Responder Radio Systems"],
    [/\bSMOKECONTROLSYSTEMS\b/g, "Smoke Control Systems"],
    [/\bSOLARPHOTOVOLTAICSYSTEMS\b/g, "Solar Photovoltaic Systems"],
    [/\bAUTO FIRE EXTINGUISHIGN SYSTEMS\b/g, "Auto Fire Extinguishing Systems"],
    [/\bALTERNATIVE SYSTEMS\b/g, "Alternative Systems"],
    [/\bBATTERY SYSTEMS\b/g, "Battery Systems"],
    [/\bCOMPRESSESD GASES\b/g, "Compressed Gases"],
    [/\bCOMPRESSED GASES\b/g, "Compressed Gases"],
    [/\bFLAMMABLE\/COMBUST LIQUIDS\b/g, "Flammable/Combustible Liquids"],
    [/\bHAZARDOUS MATERIAL\b/g, "Hazardous Material"],
    [/\bINDUSTRIAL OVEN\b/g, "Industrial Ovens"],
    [/\bMEMBRANE STRUCT\b/g, "Membrane Structures"],
    [/\bPR Vs\b/g, "PRVs"],
    [/\bTempCertificate\b/g, "Temporary Certificate"],
    [/\bSqFt\b/g, "Sq Ft"],
    [/\bFacililty\b/g, "Facility"],
    [/\bCombustable\b/g, "Combustible"],
    [/\bCYROGENIC\b/g, "CRYOGENIC"],
    [/\bEXTINGUISHIGN\b/g, "EXTINGUISHING"],
    [/\bCOMPRESSESD\b/g, "COMPRESSED"],
    [/\bSingle Family Less Than1000\b/g, "Single Family Less Than 1,000"],
    [/\bSingle Family3000to3999\b/g, "Single Family 3,000 to 3,999"],
    [/\bSingle Family > 5000\b/g, "Single Family > 5,000"],
    [/\bStadiums Coliseums Race Tracks\b/g, "Stadiums / Coliseums / Race Tracks"],
  ];

  for (const [pattern, replacement] of literalReplacements) {
    text = text.replace(pattern, replacement);
  }

  text = text
    .replace(/\bNon-Res\b/g, "Non-Residential")
    .replace(/\bNonRes\b/g, "Non-Residential")
    .replace(/\bMech\b/g, "Mechanical")
    .replace(/\bMec\b/g, "Mechanical")
    .replace(/\bEle\b/g, "Electrical")
    .replace(/\bFam\b/g, "Family")
    .replace(/\bResid\b/g, "Residential")
    .replace(/\bRes\b/g, "Residential");

  if (surface === "fee") {
    for (const [code, replacement] of FEE_DISCIPLINE_CODE_REPLACEMENTS) {
      text = text.replace(
        new RegExp("\\[" + code + "\\]", "g"),
        "[" + replacement + "]",
      );
    }
    text = text
      .replace(/\[NR Electrical\]/g, "[Non-Residential Electrical]")
      .replace(/\[Residential Electrical\]/g, "[Residential Electrical]")
      .replace(/\[Non-Residential Mechanical\]/g, "[Non-Residential Mechanical]")
      .replace(/\[Residential Mechanical\]/g, "[Residential Mechanical]")
      .replace(/\[Residential Plumbing\]/g, "[Residential Plumbing]")
      .replace(/\bNR\b/g, "Non-Residential");
  }

  if (surface === "choice") {
    text = text
      .replace(/^CITY-WAKE FOREST$/i, "City - Wake Forest")
      .replace(/^ETJ-WAKE FOREST$/i, "ETJ - Wake Forest");
  }

  return text.replace(/\s{2,}/g, " ").trim();
}

export function displayOptionLabel(value, mode = "external") {
  const raw = String(value ?? "").trim();
  if (mode === "internal") return raw;
  if (mode !== "external") {
    throw new Error("Unsupported option label mode: " + mode);
  }

  let text = raw.replace(
    /^(?:DSINSP|DSENG|DSLC|PRCR|PU|FIRE|ENG|ZP|MISC)\s*[-–]\s*/i,
    "",
  );

  for (const [internal, external] of EXTERNAL_OPTION_COMBO_REPLACEMENTS) {
    text = text.replaceAll(internal, external);
  }

  text = text
    .replace(/\bChangeout\b/gi, "Change Out")
    .replace(/Change Out-\s*Combo/gi, "Change Out - Combo");

  return cleanStaffFacingLabel(text, { surface: "option" });
}

function displayChoice(choice) {
  const label = String(choice?.label ?? "").trim();
  return {
    ...choice,
    label: cleanStaffFacingLabel(label, { surface: "choice" }),
  };
}

function browserChoiceValue(choice) {
  return String(choice?.value ?? "").trim();
}

function choiceLabelIdentity(value) {
  return String(value ?? "").trim().toLocaleLowerCase();
}

function duplicateChoiceLabels(choices) {
  const valuesByLabel = new Map();
  const displayLabels = new Map();
  for (const choice of choices ?? []) {
    const label = String(choice?.label ?? "").trim();
    if (!label) continue;
    const identity = choiceLabelIdentity(label);
    if (!valuesByLabel.has(identity)) valuesByLabel.set(identity, new Set());
    valuesByLabel.get(identity).add(browserChoiceValue(choice));
    if (!displayLabels.has(identity)) displayLabels.set(identity, label);
  }

  return Array.from(valuesByLabel.entries())
    .filter(([, values]) => values.size > 1)
    .map(([identity]) => displayLabels.get(identity))
    .sort((a, b) => a.localeCompare(b));
}

function dedupeBrowserChoices(choices) {
  const byValue = new Map();
  for (const choice of choices ?? []) {
    const key = browserChoiceValue(choice);
    if (!byValue.has(key)) {
      byValue.set(key, choice);
      continue;
    }

    const current = byValue.get(key);
    const currentLabel = String(current?.label ?? "").trim();
    const candidateLabel = String(choice?.label ?? "").trim();
    const raw = key;
    const currentIsRaw = currentLabel === raw;
    const candidateIsRaw = candidateLabel === raw;

    // Prefer a friendly authoritative label over a raw-value echo when the
    // browser would submit the same string value for either source choice.
    if (currentIsRaw && !candidateIsRaw) {
      byValue.set(key, choice);
    }
  }

  const byLabel = new Map();
  for (const choice of byValue.values()) {
    const key = choiceLabelIdentity(choice?.label);
    if (!key || !byLabel.has(key)) byLabel.set(key, choice);
  }
  return Array.from(byLabel.values());
}

function displayFieldLabel(field) {
  const key = String(field?.key ?? "").trim();
  let label = String(field?.label ?? key).trim();
  let keySuffixRemoved = false;

  if (key) {
    const suffix = new RegExp(
      "^(.*?)\\s*\\(" + escapeRegExp(key)
      + "(?:,\\s*[^)]*)?\\)\\s*$",
      "i",
    );
    const match = label.match(suffix);
    if (match) {
      label = String(match[1] ?? "").trim();
      keySuffixRemoved = true;
    }
  }

  label = cleanStaffFacingLabel(label, {
    surface: "control",
    stripTechnicalContext: true,
  });

  if (
    keySuffixRemoved
    && /\bQUANTITIES\b/i.test(label)
  ) {
    return cleanStaffFacingLabel(
      curatedDisplayLabel(key) || humanizeIdentifier(key) || key,
      { surface: "control" },
    );
  }

  if (!label || labelIdentity(label) === labelIdentity(key)) {
    return cleanStaffFacingLabel(
      curatedDisplayLabel(key) || humanizeIdentifier(key) || key,
      { surface: "control" },
    );
  }

  return label;
}


function disambiguateDisplayLabels(controls) {
  const byLabel = new Map();
  for (const control of controls) {
    const label = String(control?.displayLabel ?? "").trim();
    if (!label) continue;
    const identity = label.toLocaleLowerCase();
    if (!byLabel.has(identity)) byLabel.set(identity, []);
    byLabel.get(identity).push(control);
  }

  for (const group of byLabel.values()) {
    if (group.length < 2) continue;

    const fallback = group.map((control) =>
      humanizeIdentifier(control?.key)
      || String(control?.key ?? "").trim());
    const unique = new Set(
      fallback.map((value) => value.toLocaleLowerCase()),
    ).size === group.length;

    group.forEach((control, index) => {
      const value = fallback[index];
      const base = String(control?.displayLabel ?? "").trim();
      control.displayLabel = unique
        ? value
        : (base ? `${base} (${value})` : value);
    });
  }
}
function isUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
    .test(String(value ?? ""));
}

function directInputPrevalence(document, field) {
  const key = String(field?.key ?? "").trim();
  const match = Array.from(document?.inputPrevalence ?? []).find(
    (item) => String(item?.inputKey ?? "").trim() === key,
  );
  if (!match || String(match?.status ?? "") !== "available") {
    return {
      status: "unavailable",
      inputKey: key,
      basis: "direct-input-prevalence-query",
    };
  }
  return {
    ...match,
    status: "available",
    inputKey: key,
    basis: "direct-input-prevalence-query",
  };
}

function fieldEvidence(document, field) {
  const requiredIds = new Set(
    Array.from(field?.requiredForFeeIds ?? []).map((value) => String(value)),
  );
  const fees = Array.from(document?.fees ?? []).filter((fee) =>
    requiredIds.has(String(fee?.feeTemplateFeeId ?? "")));

  const observed = fees
    .map((fee) => ({
      fee,
      occurrence: fee?.occurrence ?? {},
    }))
    .filter(({ occurrence }) => {
      const rate = Number(occurrence?.rate);
      return Number.isFinite(rate) && rate >= 0 && rate <= 1;
    })
    .sort((a, b) =>
      Number(b.occurrence.rate) - Number(a.occurrence.rate));

  let historicalOccurrence = {
    status: "unavailable",
    rate: null,
    numerator: null,
    denominator: null,
    feeName: null,
    feeTemplateFeeId: null,
    associatedFeeCount: fees.length,
    basis: "highest-associated-fee-occurrence",
  };
  if (observed.length) {
    const best = observed[0];
    historicalOccurrence = {
      status: String(best.occurrence?.status ?? "available"),
      rate: Number(best.occurrence.rate),
      numerator: best.occurrence?.numerator ?? null,
      denominator: best.occurrence?.denominator ?? null,
      feeName: String(best.fee?.name ?? ""),
      feeTemplateFeeId: String(best.fee?.feeTemplateFeeId ?? ""),
      associatedFeeCount: fees.length,
      basis: "highest-associated-fee-occurrence",
      sampleWarning: best.occurrence?.sampleWarning ?? null,
      denominatorDefinition: best.occurrence?.denominatorDefinition ?? null,
    };
  }

  const relationships = Array.from(
    new Set(
      fees
        .map((fee) => String(fee?.templateRelationship ?? "").trim())
        .filter(Boolean),
    ),
  ).sort();

  return {
    historicalOccurrence,
    historicalPrevalence: directInputPrevalence(document, field),
    provenance: {
      scope: "selected-permit",
      templateRelationships: relationships,
      inheritedTemplate: relationships.some(
        (value) => value.toLowerCase() === "child",
      ),
    },
  };
}


const DENSE_OPTION_CONTROL_THRESHOLD = 12;

function presentationGroupLabel(control) {
  const label = String(control?.label ?? "").trim();
  const match = label.match(
    /\((.+?)\s*-\s*PM\s*-\s*\[[^\]]+\][^)]*\)\s*$/i,
  );
  if (!match) return null;
  const raw = String(match[1] ?? "").trim();
  if (!raw) return null;
  return raw
    .split(/\s+-\s+/)
    .map((part) => {
      const friendly = part
        .toLowerCase()
        .replace(/\b\w/g, (letter) => letter.toUpperCase())
        .replace(/\bPod\b/g, "POD")
        .replace(/\bRow\b/g, "ROW");
      return cleanStaffFacingLabel(
        curatedDisplayLabel(friendly) || friendly,
        { surface: "group" },
      );
    })
    .join(" - ");
}

function presentationGroupKey(kind, label) {
  const slug = String(label ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${kind}:${slug || "group"}`;
}

function presentationTokens(value) {
  return new Set(
    String(value ?? "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim()
      .split(/\s+/)
      .filter((token) =>
        token
        && !["row", "fee", "review", "hidden", "rate", "p", "s"].includes(token)),
  );
}

function relatedFeeNames(document, control) {
  const ids = new Set(
    Array.from(control?.requiredForFeeIds ?? []).map((value) => String(value)),
  );
  return Array.from(document?.fees ?? [])
    .filter((fee) => ids.has(String(fee?.feeTemplateFeeId ?? "")))
    .map((fee) => String(fee?.name ?? "").trim())
    .filter(Boolean);
}

function inferredScenarioGroupLabel(document, control, scenarioLabels) {
  const feeTokens = new Set();
  for (const name of relatedFeeNames(document, control)) {
    for (const token of presentationTokens(name)) feeTokens.add(token);
  }
  if (!feeTokens.size) return null;

  let bestLabel = null;
  let bestScore = 0;
  let tied = false;
  for (const label of scenarioLabels.keys()) {
    const scenarioTokens = presentationTokens(label);
    if (!scenarioTokens.size) continue;
    let overlap = 0;
    for (const token of scenarioTokens) {
      if (feeTokens.has(token)) overlap += 1;
    }
    const score = overlap / scenarioTokens.size;
    if (score > bestScore) {
      bestLabel = label;
      bestScore = score;
      tied = false;
    } else if (score === bestScore && score > 0) {
      tied = true;
    }
  }
  return !tied && bestScore >= 0.75 ? bestLabel : null;
}

function applyControlPresentation(document, controls) {
  const ready = controls.filter((control) => control.displayReady);
  const baseControls = controls.map((control) => ({
    ...control,
    presentationGroup: null,
  }));
  if (ready.length < DENSE_OPTION_CONTROL_THRESHOLD) {
    return {
      controls: baseControls,
      presentation: {
        mode: "flat",
        denseThreshold: DENSE_OPTION_CONTROL_THRESHOLD,
        initialControlCount: ready.length,
        groupedControlCount: 0,
        groups: [],
      },
    };
  }

  const scenarioLabels = new Map();
  for (const control of ready) {
    const label = presentationGroupLabel(control);
    if (!label) continue;
    scenarioLabels.set(label, (scenarioLabels.get(label) ?? 0) + 1);
  }

  const candidates = baseControls.map((control) => {
    if (!control.displayReady) return { control, group: null };

    const directScenarioLabel = presentationGroupLabel(control);
    const scenarioLabel = directScenarioLabel
      || inferredScenarioGroupLabel(document, control, scenarioLabels);
    if (scenarioLabel && (scenarioLabels.get(scenarioLabel) ?? 0) >= 2) {
      return {
        control,
        group: {
          key: presentationGroupKey("scenario", scenarioLabel),
          label: scenarioLabel,
          kind: "scenario",
          collapsedByDefault: true,
        },
      };
    }

    const usages = Array.from(control.usages ?? []);
    const applicabilityOnly =
      usages.length === 1 && usages[0] === "applicability";
    if (control.provenance?.inheritedTemplate || applicabilityOnly) {
      return {
        control,
        group: {
          key: "supplemental:additional-fee-details",
          label: "Additional Fee Details",
          kind: "supplemental",
          collapsedByDefault: true,
        },
      };
    }

    return { control, group: null };
  });

  const candidateCounts = new Map();
  for (const item of candidates) {
    if (!item.group) continue;
    candidateCounts.set(
      item.group.key,
      (candidateCounts.get(item.group.key) ?? 0) + 1,
    );
  }

  const groups = [];
  const groupByKey = new Map();
  const presentedControls = candidates.map(({ control, group }) => {
    const effectiveGroup = (
      group && (candidateCounts.get(group.key) ?? 0) >= 2
    ) ? group : null;
    if (effectiveGroup) {
      if (!groupByKey.has(effectiveGroup.key)) {
        const entry = {
          ...effectiveGroup,
          controlKeys: [],
        };
        groupByKey.set(effectiveGroup.key, entry);
        groups.push(entry);
      }
      groupByKey.get(effectiveGroup.key).controlKeys.push(control.key);
    }
    return {
      ...control,
      presentationGroup: effectiveGroup,
    };
  });

  const groupedControlCount = presentedControls.filter(
    (control) => control.displayReady && control.presentationGroup,
  ).length;

  return {
    controls: presentedControls,
    presentation: {
      mode: groups.length ? "progressive" : "flat",
      denseThreshold: DENSE_OPTION_CONTROL_THRESHOLD,
      initialControlCount: ready.length - groupedControlCount,
      groupedControlCount,
      groups,
    },
  };
}

export function deriveInactiveScenarioFeeIds(
  uiSchema,
  rawValues = {},
) {
  const controls = Array.from(uiSchema?.controls ?? [])
    .filter((control) => control?.displayReady);
  const groups = Array.from(uiSchema?.presentation?.groups ?? [])
    .filter((group) => group?.kind === "scenario");
  if (!groups.length) return [];

  const controlByKey = new Map(
    controls.map((control) => [String(control?.key ?? ""), control]),
  );
  const groupKeysByFee = new Map();
  const protectedFeeIds = new Set();
  const activeGroupKeys = new Set();

  function controlIsActive(control) {
    const key = String(control?.key ?? "");
    if (!Object.prototype.hasOwnProperty.call(rawValues, key)) return false;
    const raw = rawValues[key];
    if (raw === null || raw === undefined) return false;

    if (
      control?.controlType === "number"
      || control?.valueType === "number"
    ) {
      if (typeof raw === "boolean") return false;
      const text = String(raw).trim();
      if (!text) return false;
      const numeric = Number(text);
      return Number.isFinite(numeric) && Math.abs(numeric) > 0.0000005;
    }

    if (typeof raw === "string") return raw.trim() !== "";
    return true;
  }

  for (const control of controls) {
    if (control?.presentationGroup?.kind === "scenario") continue;
    for (const feeId of control?.requiredForFeeIds ?? []) {
      const id = String(feeId ?? "").trim();
      if (id) protectedFeeIds.add(id);
    }
  }

  for (const group of groups) {
    const groupControls = Array.from(group?.controlKeys ?? [])
      .map((key) => controlByKey.get(String(key ?? "")))
      .filter(Boolean);

    if (groupControls.some(controlIsActive)) {
      activeGroupKeys.add(String(group?.key ?? ""));
    }

    for (const control of groupControls) {
      for (const feeId of control?.requiredForFeeIds ?? []) {
        const id = String(feeId ?? "").trim();
        if (!id) continue;
        if (!groupKeysByFee.has(id)) groupKeysByFee.set(id, new Set());
        groupKeysByFee.get(id).add(String(group?.key ?? ""));
      }
    }
  }

  const inactive = [];
  for (const [feeId, ownerGroups] of groupKeysByFee.entries()) {
    if (protectedFeeIds.has(feeId)) continue;
    const hasActiveOwner = Array.from(ownerGroups).some(
      (groupKey) => activeGroupKeys.has(groupKey),
    );
    if (!hasActiveOwner) inactive.push(feeId);
  }
  return inactive.sort();
}


export function buildOptionUiSchema(document) {
  const publicUi = document?.ui;
  if (Array.isArray(publicUi?.controls)) {
    const option = document?.option ?? {};
    const controls = Array.from(publicUi.controls).map((control) => {
      const choices = Array.from(control?.choices ?? []).map(displayChoice);
      const duplicateLabels = duplicateChoiceLabels(choices);
      return {
        ...control,
        choices,
        duplicateChoiceLabels: duplicateLabels,
        displayReady: Boolean(control?.displayReady) && !duplicateLabels.length,
      };
    });
    const uiBlockers = Array.from(publicUi.uiBlockers ?? []);
    for (const control of controls) {
      if (!control.duplicateChoiceLabels.length) continue;
      const code = "DUPLICATE_CHOICE_LABELS_NOT_DISPLAY_READY";
      const fieldKey = String(control?.key ?? "");
      const exists = uiBlockers.some((item) =>
        String(item?.code ?? "") === code
        && String(item?.fieldKey ?? "") === fieldKey);
      if (!exists) {
        uiBlockers.push({
          code,
          fieldKey,
          message: "More than one configured value uses the same visible choice label.",
        });
      }
    }
    return {
      optionKey: option.optionKey,
      permitType: option.permitType,
      workClass: option.workClass,
      controls,
      presentation: publicUi.presentation ?? { mode: "flat", groups: [] },
      uiBlockers,
      runtimeCompileBlockers: Array.from(
        publicUi.blockingRuntimeDiagnostics ?? [],
      ),
      blockingRuntimeDiagnostics: Array.from(
        publicUi.blockingRuntimeDiagnostics ?? [],
      ),
      reviewRuntimeDiagnostics: [],
      runtimeReady: Boolean(publicUi.runtimeReady),
      displayReady: Boolean(publicUi.displayReady) && uiBlockers.length === 0,
    };
  }

  const runtime = compileOptionSchema(document);
  let controls = runtime.fields.map((field) => {
    let controlType = "unsupported";
    let displayReady = false;

    if (["number", "nullable-number"].includes(field.valueType)) {
      controlType = "number";
      displayReady = true;
    } else if (["boolean", "presence"].includes(field.valueType)) {
      controlType = "boolean";
      displayReady = true;
    } else if (field.valueType === "choice") {
      controlType = "select";
    }

    const renderedChoices = (field.choices ?? []).map(displayChoice);
    const duplicateLabels = duplicateChoiceLabels(renderedChoices);
    const displayChoices = dedupeBrowserChoices(renderedChoices);
    if (field.valueType === "choice") {
      displayReady = displayChoices.length > 0
        && field.choiceContextStatus !== "unavailable"
        && duplicateLabels.length === 0
        && displayChoices.every((choice) => {
          const label = String(choice.label ?? "").trim();
          const value = choice.value;
          if (!label) return false;
          return !(isUuid(value) && label === String(value));
        });
    }

    return {
      ...field,
      ...fieldEvidence(document, field),
      choices: displayChoices,
      duplicateChoiceLabels: duplicateLabels,
      displayLabel: displayFieldLabel(field),
      controlType,
      displayReady,
    };
  });

  disambiguateDisplayLabels(controls);
  const presented = applyControlPresentation(document, controls);
  controls = presented.controls;

  const uiBlockers = controls
    .filter((control) => !control.displayReady)
    .map((control) => {
      const duplicateLabels = Array.from(
        control?.duplicateChoiceLabels ?? [],
      );
      if (control.controlType === "unsupported") {
        return {
          code: "UNSUPPORTED_UI_FIELD_TYPE",
          fieldKey: control.key,
          message: "The field cannot yet be rendered by the generic UI.",
        };
      }
      if (control.choiceContextStatus === "unavailable") {
        return {
          code: "CHOICE_CONTEXT_NOT_AVAILABLE",
          fieldKey: control.key,
          message: "The selected form does not identify an available choice field.",
        };
      }
      if (duplicateLabels.length) {
        return {
          code: "DUPLICATE_CHOICE_LABELS_NOT_DISPLAY_READY",
          fieldKey: control.key,
          message: "More than one configured value uses the same visible choice label.",
        };
      }
      return {
        code: "CHOICE_LABELS_NOT_DISPLAY_READY",
        fieldKey: control.key,
        message: "Friendly configured labels are required before this choice can be shown.",
      };
    });

  return {
    optionKey: runtime.optionKey,
    permitType: runtime.permitType,
    workClass: runtime.workClass,
    controls,
    presentation: presented.presentation,
    uiBlockers,
    runtimeCompileBlockers: runtime.compileBlockers,
    blockingRuntimeDiagnostics: runtime.blockingCompileBlockers,
    reviewRuntimeDiagnostics: runtime.reviewCompileDiagnostics,
    runtimeReady: runtime.runtimeSchemaReady,
    displayReady: uiBlockers.length === 0,
  };
}

export function coerceValues(schema, rawValues = {}) {
  const fields = new Map(
    (schema.fields ?? []).filter((field) => field.key)
      .map((field) => [String(field.key), field]),
  );
  const unknown = Object.keys(rawValues).filter((key) => !fields.has(key)).sort();
  if (unknown.length) {
    throw new Error(`Unknown option input(s): ${unknown.join(", ")}`);
  }

  const result = {};
  for (const [key, raw] of Object.entries(rawValues)) {
    const field = fields.get(key);
    const valueType = String(field?.valueType ?? "unknown");

    if (raw === null || raw === undefined) {
      if (valueType === "nullable-number") result[key] = null;
      continue;
    }

    const text = typeof raw === "string" ? raw.trim() : raw;
    if (text === "") {
      if (valueType === "nullable-number") result[key] = null;
      continue;
    }

    if (["number", "nullable-number"].includes(valueType)) {
      if (typeof raw === "boolean") throw new Error(`${key} must be numeric.`);
      const value = Number(text);
      if (!Number.isFinite(value)) throw new Error(`${key} must be numeric.`);
      result[key] = value;
      continue;
    }

    if (["boolean", "presence"].includes(valueType)) {
      if (typeof raw === "boolean") {
        result[key] = raw;
        continue;
      }
      const lowered = String(text).toLowerCase();
      if (["true", "yes", "1"].includes(lowered)) {
        result[key] = true;
        continue;
      }
      if (["false", "no", "0"].includes(lowered)) {
        result[key] = false;
        continue;
      }
      throw new Error(`${key} must be Yes or No.`);
    }

    if (field.choiceContextStatus !== undefined) {
      const selected = String(raw).trim().toLowerCase();
      const allowed = (field.choices ?? []).some((item) =>
        String(item.value).trim().toLowerCase() === selected);
      if (field.choiceContextStatus !== "available" || !allowed) {
        throw new Error(`${key} is not an available choice in the selected form.`);
      }
    }
    result[key] = raw;
  }
  return result;
}

function evaluateCondition(condition, values, schema) {
  const operatorId = Number(condition?.operatorID);
  if (!SUPPORTED_OPERATOR_IDS.has(operatorId)) {
    const error = new Error(`Condition operator ${condition?.operatorID} is not validated.`);
    error.code = "UNSUPPORTED_CONDITION_OPERATOR";
    throw error;
  }
  const key = String(condition?.operandValue ?? "").trim();
  if (!key) {
    const error = new Error("Condition does not identify an operand field.");
    error.code = "MISSING_CONDITION_FIELD";
    throw error;
  }
  if (!Object.prototype.hasOwnProperty.call(values, key)) {
    const error = new Error(`Condition input '${key}' was not supplied.`);
    error.code = "MISSING_CONDITION_INPUT";
    throw error;
  }
  if ([BLANK_OPERATOR_ID, PRESENT_OPERATOR_ID].includes(operatorId)) {
    const value = values[key];
    let present;
    if (typeof value === "boolean") present = value;
    else if (value === null || value === undefined) present = false;
    else if (typeof value === "string") present = value.trim() !== "";
    else present = true;
    return operatorId === BLANK_OPERATOR_ID ? !present : present;
  }

  const expression = condition?.compareExpression ?? {};
  if (!Object.prototype.hasOwnProperty.call(expression, "value")) {
    const error = new Error("Condition comparison value is absent.");
    error.code = "MISSING_CONDITION_VALUE";
    throw error;
  }
  if (operatorId === GREATER_THAN_OPERATOR_ID) {
    if (values[key] === null) return false;
    const left = numberValue(values[key]);
    const right = numberValue(expression.value);
    if (left === null || right === null) {
      const error = new Error("GreaterThan applicability operands must be numeric.");
      error.code = "INVALID_NUMERIC_CONDITION_VALUE";
      throw error;
    }
    return left > right;
  }

  const left = normalizeScalar(values[key]);
  const right = normalizeScalar(expression.value);
  if (operatorId === NOT_EQUAL_OPERATOR_ID) return left !== right;
  return left === right;
}

function evaluateConditionGroup(group, values, schema) {
  const results = [];
  for (const condition of group?.childConditions ?? []) {
    results.push(evaluateCondition(condition, values, schema));
  }
  for (const child of group?.childConditionGroups ?? []) {
    results.push(evaluateConditionGroup(child, values, schema));
  }
  if (!results.length) {
    const error = new Error("Condition group contains no evaluable conditions.");
    error.code = "EMPTY_CONDITION_GROUP";
    throw error;
  }
  return group?.isOrGroup ? results.some(Boolean) : results.every(Boolean);
}

function resolveApplicability(fees, values, schema) {
  const mapping = {};
  const unresolved = [];
  const byId = new Map(
    fees.filter((fee) => feeId(fee)).map((fee) => [feeId(fee), fee]),
  );

  for (const [id, fee] of byId.entries()) {
    const section = String(fee?.section ?? "");
    const status = String(fee?.applicability?.status ?? "");
    const conditions = Array.from(fee?.applicability?.conditions ?? []);
    const requires = ["conditional", "event-contingent"].includes(section)
      || ["conditional", "event-contingent"].includes(status)
      || (["manual-review"].includes(section) && conditions.length > 0)
      || (["manual-review"].includes(status) && conditions.length > 0);

    if (!requires) continue;
    if (section === "event-contingent" || status === "event-contingent") {
      mapping[id] = false;
      continue;
    }
    if (!conditions.length) {
      mapping[id] = false;
      unresolved.push({
        feeTemplateFeeId: id,
        name: String(fee?.name ?? ""),
        code: "MISSING_APPLICABILITY_CONDITION",
        message: "Conditional fee has no preserved condition.",
      });
      continue;
    }

    const unsupported = conditions.filter((item) => String(item?.kind ?? "") !== "expression");
    if (unsupported.length) {
      mapping[id] = false;
      unresolved.push({
        feeTemplateFeeId: id,
        name: String(fee?.name ?? ""),
        code: "UNSUPPORTED_APPLICABILITY_CONDITION",
        message: "Conditional fee includes unsupported applicability conditions.",
      });
      continue;
    }

    try {
      mapping[id] = conditions.every((item) =>
        evaluateConditionGroup(item?.sourceRule ?? {}, values, schema));
    } catch (error) {
      mapping[id] = false;
      unresolved.push({
        feeTemplateFeeId: id,
        name: String(fee?.name ?? ""),
        code: error.code || "APPLICABILITY_UNRESOLVED",
        message: error.message || "Fee applicability could not be resolved.",
      });
    }
  }

  for (const [id, fee] of byId.entries()) {
    if (String(fee?.category ?? "") !== "working-without-permit" || !mapping[id]) continue;
    const dependencies = ruleInputs(fee)
      .filter((item) => item?.source === "fee" && item?.inputKey)
      .map((item) => String(item.inputKey).trim());
    if (dependencies.length !== 1) continue;
    const dependency = byId.get(dependencies[0]);
    if (!dependency) continue;
    const section = String(dependency?.section ?? "");
    const status = String(dependency?.applicability?.status ?? "");
    if (section === "conditional" || status === "conditional") {
      if (!mapping[dependencies[0]]) mapping[id] = false;
    }
  }

  return { mapping, unresolved };
}

function applyBounds(amount, rule) {
  const minimum = numberValue(rule?.minimum);
  const maximum = numberValue(rule?.maximum);
  let result = amount;
  if (minimum !== null && minimum > 0) result = Math.max(result, minimum);
  if (maximum !== null && maximum > 0) result = Math.min(result, maximum);
  return result;
}

function roundAmount(amount, rule) {
  const roundingType = rule?.roundingType;
  const roundingValue = numberValue(rule?.roundingValue);
  if (roundingType === null || roundingType === undefined) return amount;
  const type = Number(roundingType);
  if (type === 1 && (roundingValue === null || roundingValue === 0)) return amount;
  if (type === 4 && roundingValue === 1) return Math.floor(amount + 0.5);
  if (type === 2 && roundingValue === 1) return Math.ceil(amount);
  const error = new Error(
    `Rounding type ${roundingType}, value ${roundingValue} is not validated.`,
  );
  error.code = "UNSUPPORTED_ROUNDING";
  throw error;
}

function selectTier(tiers, inputValue) {
  for (let index = 0; index < (tiers ?? []).length; index += 1) {
    const tier = tiers[index];
    const lower = numberValue(tier?.lowerLimit) ?? 0;
    const upper = numberValue(tier?.upperLimit);
    if (inputValue < lower) continue;
    if (tier?.unlimited || upper === null || inputValue <= upper) {
      return { tierNumber: index + 1, tier };
    }
  }
  return null;
}

function tierExtraUnits(behavior, inputValue, over, additionalUnit) {
  if (!behavior) throw new Error("Tier input behavior has not been validated.");
  if (behavior === "full-value") {
    if (over !== 0) throw new Error("Validated valuation tiers require over=0.");
    return inputValue / additionalUnit;
  }
  const raw = (inputValue - over) / additionalUnit;
  if (raw <= 0) return 0;
  if (behavior === "tenths") return Math.floor(raw * 10 + 0.5) / 10;
  if (behavior === "ceiling") return Math.ceil(raw);
  if (behavior === "integer") {
    if (!Number.isInteger(inputValue)) throw new Error("Tier input must be an integer.");
    return raw;
  }
  if (behavior === "bands") {
    throw new Error("Tier input has an unexpected additional-unit charge.");
  }
  if (behavior === "row-linear") {
    if (over !== 150 || additionalUnit !== 1) {
      throw new Error("Validated ROW linear-foot tiers require over=150 and additionalUnit=1.");
    }
    return raw;
  }
  throw new Error("Tier input does not have validated additional-unit semantics.");
}

function calculateTieredAmount(behavior, inputValue, tiers) {
  if (!behavior) throw new Error("Tier input behavior has not been validated.");
  const selected = selectTier(tiers, inputValue);
  if (!selected) throw new Error(`No configured tier contains input value ${inputValue}.`);
  let amount = numberValue(selected.tier?.amount);
  if (amount === null) throw new Error("Selected tier has no numeric amount.");
  const additionalAmount = numberValue(selected.tier?.additionalAmount) ?? 0;
  const additionalUnit = numberValue(selected.tier?.additionalUnit) ?? 0;
  const over = numberValue(selected.tier?.over) ?? 0;
  if (additionalAmount > 0) {
    if (additionalUnit <= 0) throw new Error("Selected tier has an additional amount without a positive unit.");
    amount += tierExtraUnits(behavior, inputValue, over, additionalUnit) * additionalAmount;
  }
  return { amount, tierNumber: selected.tierNumber };
}

function validatedTotalDependencies(fee, byId) {
  const componentIds = Array.from(
    calculationRule(fee)?.publicBehavior?.totalComponentIds ?? [],
  );
  if (!componentIds.length) return null;
  if (componentIds.some((id) => !byId.has(id))) return null;
  return [...componentIds];
}

function effectiveRate(fee, rule) {
  const configured = numberValue(rule?.amount);
  if (configured === null) return null;
  const scale = numberValue(rule?.publicBehavior?.rateScale) ?? 1;
  return configured * scale;
}

function roundHalfAwayFromZero(value, decimals = 2) {
  const factor = 10 ** decimals;
  const absolute = Math.abs(Number(value));
  const rounded = Math.floor((absolute * factor) + 0.5 + Number.EPSILON) / factor;
  return Number(value) < 0 ? -rounded : rounded;
}

function validatedFormulaShape(fee, byId) {
  const rule = calculationRule(fee);
  const inputs = Array.from(rule?.inputs ?? []);
  const configured = rule?.publicBehavior?.formula;
  if (!configured || typeof configured !== "object") return null;
  const kind = String(configured.kind ?? "");
  const dependencyIds = Array.from(configured.dependencyIds ?? [])
    .map((value) => String(value ?? "").trim())
    .filter(Boolean);
  if (dependencyIds.some((id) => !byId.has(id))) return null;

  if (kind === "fixed-formula") {
    if (inputs.length || numberValue(rule?.amount) === null) return null;
  } else if (kind === "minimum-times-rate") {
    if (dependencyIds.length !== 2 || inputs.length !== 2) return null;
  } else if (kind === "multiply") {
    if (
      dependencyIds.length !== 2
      || !dependencyIds.includes(String(configured.rateId ?? ""))
      || !dependencyIds.includes(String(configured.daysId ?? ""))
    ) return null;
  } else {
    return null;
  }
  return {
    ...configured,
    kind,
    dependencyIds,
  };
}

function calculateValidatedFormulaAmount(fee, dependencyValues, byId) {
  const shape = validatedFormulaShape(fee, byId);
  if (!shape) {
    throw new Error(
      "Formula does not match a validated ROW, Tree Conservation, or Pump Station shape.",
    );
  }

  if (shape.kind === "fixed-formula") {
    const configuredAmount = numberValue(calculationRule(fee)?.amount);
    if (configuredAmount === null) {
      throw new Error("Validated Pump Station formula has no numeric configured amount.");
    }
    return {
      amount: configuredAmount,
      operation: "validated-fixed-formula",
      expressionType: shape.expressionType,
      configuredRate: configuredAmount,
      validatedFixedBehavior: true,
    };
  }

  if (shape.kind === "minimum-times-rate") {
    const values = shape.dependencyIds.map((id) => Number(dependencyValues[id]));
    if (values.some((value) => !Number.isFinite(value))) {
      throw new Error("Validated Tree Conservation dependencies are not numeric.");
    }
    const configuredRate = numberValue(calculationRule(fee)?.amount);
    if (configuredRate === null) {
      throw new Error("Validated Tree Conservation formula has no numeric configured amount.");
    }
    const baseValue = roundHalfAwayFromZero(Math.min(...values), 2);
    return {
      amount: baseValue * configuredRate,
      operation: "minimum-cent-rounded-times-rate",
      expressionType: shape.expressionType,
      baseValue,
      configuredRate,
      dependencyValues: Object.fromEntries(
        shape.dependencyIds.map((id, index) => [id, values[index]]),
      ),
    };
  }

  if (
    !Object.prototype.hasOwnProperty.call(dependencyValues, shape.rateId)
    || !Object.prototype.hasOwnProperty.call(dependencyValues, shape.daysId)
  ) {
    throw new Error("Validated ROW formula dependencies were not fully evaluated.");
  }

  const rate = Number(dependencyValues[shape.rateId]);
  const days = Number(dependencyValues[shape.daysId]);
  if (!Number.isFinite(rate) || !Number.isFinite(days)) {
    throw new Error("Validated ROW formula dependencies are not numeric.");
  }

  return {
    amount: rate * days,
    operation: "multiply",
    expressionType: shape.expressionType,
    rateFeeTemplateFeeId: shape.rateId,
    daysFeeTemplateFeeId: shape.daysId,
    rateValue: rate,
    daysValue: days,
  };
}

function buildFeeGraphEvaluator(
  fees,
  values,
  applicability,
  inactiveFeeIds = new Set(),
) {
  const byId = new Map(
    fees.filter((fee) => feeId(fee)).map((fee) => [feeId(fee), fee]),
  );
  const cache = new Map();
  const visiting = new Set();

  function blocked(id, code, message, dependencies = []) {
    const result = {
      feeTemplateFeeId: id,
      status: "blocked",
      amount: null,
      validationCodes: [code],
      message,
      dependencies: [...dependencies],
    };
    cache.set(id, result);
    return result;
  }

  function resolveNonFeeInput(id, item) {
    const source = String(item?.source ?? "");
    const key = String(item?.inputKey ?? "").trim();

    if (["input", "custom-field", "system"].includes(source)) {
      if (!Object.prototype.hasOwnProperty.call(values, key)) {
        return { error: blocked(id, "INPUT_VALUE_MISSING", `Required project input '${key}' was not supplied.`) };
      }
      const value = numberValue(values[key]);
      if (value === null) {
        return { error: blocked(id, "INPUT_VALUE_MISSING", `Project input '${key}' is not numeric.`) };
      }
      return { value };
    }

    if (source === "constant") {
      const value = numberValue(item?.constant);
      if (value === null) {
        return { error: blocked(id, "UNSUPPORTED_INPUT_SOURCE", "Constant input is not numeric.") };
      }
      return { value };
    }

    return {
      error: blocked(id, "UNSUPPORTED_INPUT_SOURCE", `Input source '${source}' is not supported.`),
    };
  }

  function evaluate(id) {
    const key = String(id);
    if (cache.has(key)) return cache.get(key);
    if (!byId.has(key)) return blocked(key, "UNSUPPORTED_INPUT_SOURCE", `Referenced fee ${key} is not present.`);
    if (inactiveFeeIds.has(key)) {
      const result = {
        feeTemplateFeeId: key,
        status: "not-applicable",
        amount: 0,
        validationCodes: [],
        message: null,
        dependencies: [],
      };
      cache.set(key, result);
      return result;
    }
    if (visiting.has(key)) return blocked(key, "FEE_DEPENDENCY_CYCLE", `Fee dependency cycle detected at ${key}.`);

    const fee = byId.get(key);
    visiting.add(key);
    try {
      if (requiresApplicabilityResolution(fee)) {
        if (!Object.prototype.hasOwnProperty.call(applicability, key)) {
          return blocked(key, "APPLICABILITY_UNRESOLVED", "Fee applicability has not been resolved.");
        }
        if (!applicability[key]) {
          const result = {
            feeTemplateFeeId: key,
            status: "not-applicable",
            amount: 0,
            validationCodes: [],
            message: null,
            dependencies: [],
          };
          cache.set(key, result);
          return result;
        }
      }

      if (fee.estimationReview) {
        return blocked(key, "ESTIMATION_REVIEW_REQUIRED", String(fee.estimationReview));
      }

      const calculation = fee?.calculation ?? {};
      const mode = String(calculation?.mode ?? "");

      if (mode === "fixed") {
        const amount = numberValue(calculation?.fixedAmount);
        if (amount === null) return blocked(key, "UNSUPPORTED_CALCULATION_MODE", "Fixed fee has no numeric amount.");
        const result = {
          feeTemplateFeeId: key,
          status: "calculated",
          amount,
          validationCodes: [],
          message: null,
          dependencies: [],
        };
        cache.set(key, result);
        return result;
      }

      if (mode === "tiered") {
        const rule = calculationRule(fee);
        const inputs = Array.from(rule?.inputs ?? []);
        if (inputs.length !== 1 || inputs[0]?.source === "fee") {
          return blocked(key, "TIERED_CALCULATION_UNVALIDATED", "Validated tiered calculations require one non-fee input.");
        }
        const resolved = resolveNonFeeInput(key, inputs[0]);
        if (resolved.error) return resolved.error;
        try {
          const tiered = calculateTieredAmount(
            String(rule?.publicBehavior?.tierInputBehavior ?? ""),
            resolved.value,
            Array.from(rule?.tiers ?? []),
          );
          let amount = applyBounds(tiered.amount, rule);
          amount = roundAmount(amount, rule);
          const result = {
            feeTemplateFeeId: key,
            status: "calculated",
            amount,
            validationCodes: [],
            message: null,
            dependencies: [],
            tierNumber: tiered.tierNumber,
          };
          cache.set(key, result);
          return result;
        } catch (error) {
          return blocked(key, "TIERED_CALCULATION_UNVALIDATED", error.message);
        }
      }

      if (mode === "formula") {
        const rule = calculationRule(fee);
        const shape = validatedFormulaShape(fee, byId);
        if (!shape) {
          return blocked(
            key,
            "FORMULA_CALCULATION_UNVALIDATED",
            "Formula does not match a validated ROW or Tree Conservation shape.",
          );
        }

        const dependencyValues = {};
        for (const dependencyId of shape.dependencyIds) {
          const dependency = evaluate(dependencyId);
          if (dependency.status === "blocked") {
            return blocked(
              key,
              dependency.validationCodes?.[0] || "DEPENDENCY_BLOCKED",
              `Dependency ${dependencyId} could not be evaluated: ${dependency.message}`,
              shape.dependencyIds,
            );
          }
          dependencyValues[dependencyId] = Number(dependency.amount || 0);
        }

        try {
          const formula = calculateValidatedFormulaAmount(
            fee,
            dependencyValues,
            byId,
          );
          let amount = applyBounds(formula.amount, rule);
          amount = roundAmount(amount, rule);
          const result = {
            feeTemplateFeeId: key,
            status: "calculated",
            amount,
            validationCodes: [],
            message: null,
            dependencies: [...shape.dependencyIds],
            operation: formula.operation,
            expressionType: formula.expressionType,
          };
          for (const metadataKey of [
            "rateValue",
            "daysValue",
            "baseValue",
            "configuredRate",
            "dependencyValues",
            "validatedFixedBehavior",
          ]) {
            if (Object.prototype.hasOwnProperty.call(formula, metadataKey)) {
              result[metadataKey] = formula[metadataKey];
            }
          }
          cache.set(key, result);
          return result;
        } catch (error) {
          return blocked(
            key,
            "FORMULA_CALCULATION_UNVALIDATED",
            error.message,
            shape.dependencyIds,
          );
        }
      }

      if (mode !== "input") {
        return blocked(key, "UNSUPPORTED_CALCULATION_MODE", `Calculation mode '${mode}' is not supported.`);
      }

      const rule = calculationRule(fee);
      const inputs = Array.from(rule?.inputs ?? []);

      if (
        Boolean(rule?.publicBehavior?.fixedInput)
        && inputs.length === 0
      ) {
        const amount = numberValue(rule?.amount);
        if (amount === null) {
          return blocked(
            key,
            "UNSUPPORTED_CALCULATION_MODE",
            "Validated fixed-behavior assignment has no numeric configured amount.",
          );
        }
        const result = {
          feeTemplateFeeId: key,
          status: "calculated",
          amount,
          validationCodes: [],
          message: null,
          dependencies: [],
          inputSources: [],
          baseValue: amount,
          rate: null,
          validatedFixedBehavior: true,
        };
        cache.set(key, result);
        return result;
      }

      const feeKeys = inputs
        .filter((item) => item?.source === "fee")
        .map((item) => String(item?.inputKey ?? "").trim());
      if (new Set(feeKeys).size !== feeKeys.length) {
        return blocked(
          key,
          "DUPLICATE_FEE_INPUT_SEMANTICS_UNVALIDATED",
          "Duplicate fee-input records have unvalidated semantics.",
          feeKeys,
        );
      }

      const resolvedValues = [];
      const dependencies = [];
      const sources = [];

      for (const item of inputs) {
        const source = String(item?.source ?? "");
        const inputKey = String(item?.inputKey ?? "").trim();
        sources.push(source);

        if (source === "fee") {
          dependencies.push(inputKey);
          const dependency = evaluate(inputKey);
          if (dependency.status === "blocked") {
            return blocked(
              key,
              dependency.validationCodes?.[0] || "DEPENDENCY_BLOCKED",
              `Dependency ${inputKey} could not be evaluated: ${dependency.message}`,
              dependencies,
            );
          }
          resolvedValues.push(Number(dependency.amount || 0));
          continue;
        }

        if (source === "total") {
          const componentIds = validatedTotalDependencies(fee, byId);
          if (!componentIds) {
            return blocked(
              key,
              "UNSUPPORTED_INPUT_SOURCE",
              "Total-of-fees semantics are not validated for this fee assignment.",
              dependencies,
            );
          }
          let totalValue = 0;
          for (const componentId of componentIds) {
            if (!dependencies.includes(componentId)) dependencies.push(componentId);
            const dependency = evaluate(componentId);
            if (dependency.status === "blocked") {
              return blocked(
                key,
                dependency.validationCodes?.[0] || "DEPENDENCY_BLOCKED",
                `Dependency ${componentId} could not be evaluated: ${dependency.message}`,
                dependencies,
              );
            }
            totalValue += Number(dependency.amount || 0);
          }
          resolvedValues.push(totalValue);
          continue;
        }

        const resolved = resolveNonFeeInput(key, item);
        if (resolved.error) return resolved.error;
        resolvedValues.push(resolved.value);
      }

      if (!resolvedValues.length) {
        return blocked(key, "INPUT_VALUE_MISSING", "Input calculation contains no evaluable inputs.");
      }

      let baseValue;
      if (resolvedValues.length === 1) {
        baseValue = resolvedValues[0];
      } else if (
        inputs.every((item) => item?.source === "fee")
        && String(rule?.publicBehavior?.aggregate ?? "") === "sum"
      ) {
        baseValue = resolvedValues.reduce((sum, value) => sum + value, 0);
      } else {
        return blocked(
          key,
          "MULTI_INPUT_SEMANTICS_UNVALIDATED",
          "Multiple-input aggregation semantics are not validated for this fee.",
          dependencies,
        );
      }

      const rate = effectiveRate(fee, rule);
      if (rate === null) {
        return blocked(key, "UNSUPPORTED_CALCULATION_MODE", "Input calculation has no numeric rate.", dependencies);
      }

      let amount = applyBounds(baseValue * rate, rule);
      try {
        amount = roundAmount(amount, rule);
      } catch (error) {
        return blocked(key, error.code || "UNSUPPORTED_ROUNDING", error.message, dependencies);
      }

      const result = {
        feeTemplateFeeId: key,
        status: "calculated",
        amount,
        validationCodes: [],
        message: null,
        dependencies,
        inputSources: sources,
        baseValue,
        rate,
      };
      cache.set(key, result);
      return result;
    } finally {
      visiting.delete(key);
    }
  }

  return { evaluate };
}

function classifyEstimateStatus(blocked, unresolved, staffReview, possibleAdditional) {
  const reasons = [];
  if (staffReview.length) {
    reasons.push({
      code: "MANUAL_REVIEW_FEES",
      label: "Some fees must be reviewed by staff before the estimate is complete.",
      count: staffReview.length,
    });
  }
  if (unresolved.length) {
    reasons.push({
      code: "UNRESOLVED_APPLICABILITY",
      label: "Staff needs more project information to decide whether some fees apply.",
      count: unresolved.length,
    });
  }
  if (blocked.length) {
    reasons.push({
      code: "CALCULATION_BLOCKERS",
      label: "Some fee amounts must be calculated or confirmed by staff.",
      count: blocked.length,
    });
  }
  if (reasons.length) {
    return {
      code: "staff-review-required",
      label: "Staff Review Needed",
      requiresStaffReview: true,
      reasons,
    };
  }
  if (possibleAdditional.length) {
    return {
      code: "estimate-calculated-possible-additions",
      label: "Estimate Calculated",
      requiresStaffReview: false,
      reasons: [],
    };
  }
  return {
    code: "automatic-estimate-complete",
    label: "Estimate Ready",
    requiresStaffReview: false,
    reasons: [],
  };
}

function supportedRoundingShape(rule) {
  const type = rule?.roundingType;
  const value = numberValue(rule?.roundingValue);
  if (type === null || type === undefined) return true;
  if (Number(type) === 1 && (value === null || value === 0)) return true;
  if (Number(type) === 4 && value === 1) return true;
  if (Number(type) === 2 && value === 1) return true;
  return false;
}

function structuralCalculationCapability(fee, byId) {
  if (fee?.estimationReview) return "staff-review";
  const calculation = fee?.calculation ?? {};
  const mode = String(calculation?.mode ?? "");
  const rule = calculationRule(fee);
  const inputs = ruleInputs(fee);

  if (mode === "fixed") {
    return numberValue(calculation?.fixedAmount) === null
      ? "fixed-missing-amount"
      : "supported";
  }

  if (mode === "formula") {
    return validatedFormulaShape(fee, byId) ? "supported" : "formula";
  }
  if (mode === "manual") return "manual";
  if (mode === "unsupported" || !mode) return "unsupported-mode";

  if (!supportedRoundingShape(rule)) return "rounding";

  if (mode === "tiered") {
    if (inputs.length !== 1 || inputs[0]?.source === "fee") {
      return "tiered-input-shape";
    }
    return String(rule?.publicBehavior?.tierInputBehavior ?? "")
      ? "supported"
      : "tiered-input-semantic";
  }

  if (mode !== "input") return "unsupported-mode";
  if (!inputs.length) {
    return Boolean(rule?.publicBehavior?.fixedInput)
      && numberValue(rule?.amount) !== null
      ? "supported"
      : "missing-input";
  }

  const feeKeys = inputs
    .filter((item) => item?.source === "fee")
    .map((item) => String(item?.inputKey ?? "").trim());
  if (new Set(feeKeys).size !== feeKeys.length) {
    return "duplicate-fee-input";
  }

  const sources = inputs.map((item) => String(item?.source ?? ""));
  if (sources.includes("total")) {
    const totalInputs = inputs.filter((item) => item?.source === "total");
    if (
      totalInputs.length !== 1
      || inputs.length !== 1
      || !validatedTotalDependencies(fee, byId)
    ) {
      return "total-input";
    }
  }

  const unsupportedSources = sources.filter(
    (source, index) => {
      if (
        source === "fee"
        || source === "total"
        || source === "constant"
      ) return false;
      if (["input", "custom-field", "system"].includes(source)) return false;
      return true;
    },
  );
  if (unsupportedSources.length) return "input-source";

  if (inputs.length === 1) return "supported";

  const allFeeInputs = inputs.every((item) => item?.source === "fee");
  if (
    allFeeInputs
    && String(rule?.publicBehavior?.aggregate ?? "") === "sum"
  ) {
    return "supported";
  }

  return "multi-input";
}

export function analyzeOptionCapabilities(document) {
  const ui = buildOptionUiSchema(document);
  const byId = new Map(
    (document.fees ?? [])
      .filter((fee) => feeId(fee))
      .map((fee) => [feeId(fee), fee]),
  );
  const blockerIds = new Set(
    (ui.blockingRuntimeDiagnostics ?? [])
      .map((item) => String(item?.feeTemplateFeeId ?? ""))
      .filter(Boolean),
  );

  const archetypes = new Map();
  const blockedAutomaticFeeDetails = [];
  let automaticFees = 0;
  let structurallySupportedAutomaticFees = 0;
  let assessableAutomaticFees = 0;
  let structurallySupportedAssessableAutomaticFees = 0;
  let dependencyOnlyAutomaticFees = 0;
  let reviewOnlyFees = 0;

  for (const fee of document.fees ?? []) {
    const section = String(fee?.section ?? "");
    const applicabilityStatus = String(fee?.applicability?.status ?? "");
    if (
      isReviewOnlyFee(fee)
      || section === "inactive"
      || section === "event-contingent"
      || applicabilityStatus === "event-contingent"
    ) {
      reviewOnlyFees += 1;
      continue;
    }

    automaticFees += 1;
    const dependencyOnly = (
      String(fee?.assessmentRole ?? "") === "dependency-only"
    );
    if (dependencyOnly) {
      dependencyOnlyAutomaticFees += 1;
    } else {
      assessableAutomaticFees += 1;
    }

    const id = feeId(fee);
    let capability = blockerIds.has(id)
      ? "applicability"
      : structuralCalculationCapability(fee, byId);

    if (capability === "supported") {
      structurallySupportedAutomaticFees += 1;
      if (!dependencyOnly) {
        structurallySupportedAssessableAutomaticFees += 1;
      }
      continue;
    }

    archetypes.set(capability, (archetypes.get(capability) ?? 0) + 1);
    const rule = calculationRule(fee);
    const inputs = ruleInputs(fee);
    blockedAutomaticFeeDetails.push({
      feeTemplateFeeId: id,
      feeName: String(fee?.name ?? ""),
      section,
      capability,
      calculationMode: String(fee?.calculation?.mode ?? ""),
      inputCount: inputs.length,
      inputSources: inputs.map((item) => String(item?.source ?? "")),
      inputKeys: inputs.map((item) => String(item?.inputKey ?? "").trim()),
      expressionType: rule?.publicBehavior?.formula?.expressionType ?? null,
    });
  }

  return {
    optionKey: document?.option?.optionKey,
    permitType: document?.option?.permitType,
    workClass: document?.option?.workClass,
    uiDisplayReady: Boolean(ui.displayReady),
    applicabilityRuntimeReady: Boolean(ui.runtimeReady),
    automaticFees,
    structurallySupportedAutomaticFees,
    blockedAutomaticFees:
      automaticFees - structurallySupportedAutomaticFees,
    assessableAutomaticFees,
    structurallySupportedAssessableAutomaticFees,
    blockedAssessableAutomaticFees:
      assessableAutomaticFees
      - structurallySupportedAssessableAutomaticFees,
    dependencyOnlyAutomaticFees,
    reviewOnlyFees,
    blockedAutomaticFeeDetails,
    blockerArchetypes: Object.fromEntries(
      Array.from(archetypes.entries()).sort((a, b) =>
        a[0].localeCompare(b[0])),
    ),
  };
}


export function evaluateOption(
  document,
  rawValues = {},
  runtimeOptions = {},
) {
  const schema = compileOptionSchema(document);
  const values = coerceValues(schema, rawValues);
  const inactiveFeeIds = new Set(
    Array.from(runtimeOptions?.inactiveFeeIds ?? [])
      .map((value) => String(value ?? "").trim())
      .filter(Boolean),
  );
  const systemValues = systemApplicabilityValues(document);
  const resolvedApplicabilityValues = { ...systemValues, ...values };
  const fees = Array.from(document.fees ?? []);
  const evaluationInactiveFeeIds = new Set(inactiveFeeIds);
  for (const fee of fees) {
    if (!isLaterEventFee(fee)) continue;
    const id = feeId(fee);
    if (id) evaluationInactiveFeeIds.add(id);
  }
  const applicabilityResult = resolveApplicability(
    fees,
    resolvedApplicabilityValues,
    schema,
  );
  for (const id of evaluationInactiveFeeIds) {
    applicabilityResult.mapping[id] = false;
  }
  applicabilityResult.unresolved = applicabilityResult.unresolved.filter(
    (item) => !evaluationInactiveFeeIds.has(
      String(item?.feeTemplateFeeId ?? ""),
    ),
  );
  const graph = buildFeeGraphEvaluator(
    fees,
    values,
    applicabilityResult.mapping,
    evaluationInactiveFeeIds,
  );

  const calculated = [];
  const excluded = [];
  const possibleAdditional = [];
  const blocked = [];
  const staffReview = [];
  const inactive = [];

  const unresolvedIds = new Set(
    applicabilityResult.unresolved.map((item) => String(item.feeTemplateFeeId ?? "")),
  );

  for (const fee of fees) {
    const row = {
      feeTemplateFeeId: feeId(fee),
      name: String(fee?.name ?? "Unnamed fee"),
      section: String(fee?.section ?? "unknown"),
      mode: String(fee?.calculation?.mode ?? "unknown"),
      assessmentRole: String(
        fee?.assessmentRole ?? "standalone-or-assessed"
      ),
      category: String(fee?.category ?? "ordinary"),
    };

    if (row.section === "inactive") {
      inactive.push(row);
      continue;
    }

    const id = row.feeTemplateFeeId;
    const status = String(fee?.applicability?.status ?? "");

    if (inactiveFeeIds.has(id)) {
      excluded.push(row);
      continue;
    }

    if (isReviewOnlyFee(fee)) {
      if (row.section === "event-contingent" || status === "event-contingent" || isLaterEventFee(fee)) {
        excluded.push(row);
        continue;
      }
      const conditions = Array.from(fee?.applicability?.conditions ?? []);
      const applies = Object.prototype.hasOwnProperty.call(applicabilityResult.mapping, id)
        ? applicabilityResult.mapping[id]
        : null;
      if (conditions.length) {
        if (unresolvedIds.has(id)) possibleAdditional.push(row);
        else if (applies === true) staffReview.push(row);
        else if (applies === false) excluded.push(row);
        else possibleAdditional.push(row);
        continue;
      }
      possibleAdditional.push(row);
      continue;
    }

    if (!id) {
      blocked.push({
        ...row,
        validationCode: "MISSING_FEE_ASSIGNMENT_ID",
        message: "Fee has no assignment id and cannot be evaluated.",
      });
      continue;
    }

    const result = graph.evaluate(id);
    if (result.status === "calculated") {
      calculated.push({
        ...row,
        amount: Number(result.amount || 0),
        dependencies: Array.from(result.dependencies ?? []),
      });
    } else if (result.status === "not-applicable") {
      excluded.push(row);
    } else {
      blocked.push({
        ...row,
        validationCode: String(result.validationCodes?.[0] || "UNKNOWN_BLOCK"),
        message: String(result.message || "Unable to calculate safely."),
      });
    }
  }

  const unresolved = applicabilityResult.unresolved.filter((item) => {
    const fee = fees.find((candidate) => feeId(candidate) === String(item.feeTemplateFeeId ?? ""));
    return fee && !isReviewOnlyFee(fee) && !isLaterEventFee(fee);
  });

  const dependencyOnly = calculated.filter(
    (item) => item.assessmentRole === "dependency-only",
  );
  const assessableCalculated = calculated.filter(
    (item) => item.assessmentRole !== "dependency-only",
  );
  const wwp = assessableCalculated.filter(
    (item) => item.category === "working-without-permit",
  );
  const regular = assessableCalculated.filter(
    (item) => item.category !== "working-without-permit",
  );
  const regularSubtotal = roundMoney(regular.reduce((sum, item) => sum + item.amount, 0));
  const wwpSubtotal = roundMoney(wwp.reduce((sum, item) => sum + item.amount, 0));
  const subtotal = roundMoney(regularSubtotal + wwpSubtotal);
  const estimateStatus = classifyEstimateStatus(
    blocked,
    unresolved,
    staffReview,
    possibleAdditional,
  );

  return {
    schema,
    values,
    systemApplicabilityValues: systemValues,
    resolvedApplicabilityValues,
    report: {
      catalogVersion: document.catalogVersion,
      configurationAsOfDate: document?.configuration?.asOfDate,
      option: document.option ?? {},
      scenario: {
        values,
        inactiveFeeIds: Array.from(inactiveFeeIds).sort(),
      },
      regularPermitFeesSubtotal: regularSubtotal,
      workingWithoutPermitAdditionalFeeSubtotal: wwpSubtotal,
      subtotal,
      estimatedTotal: subtotal,
      estimateStatus: estimateStatus.code,
      estimateStatusLabel: estimateStatus.label,
      requiresStaffReview: estimateStatus.requiresStaffReview,
      staffReviewReasons: estimateStatus.reasons,
      regularPermitFees: regular,
      workingWithoutPermitAdditionalFees: wwp,
      calculatedFees: calculated,
      dependencyOnlyFees: dependencyOnly,
      excludedFees: excluded,
      possibleAdditionalFees: possibleAdditional,
      blockedFees: blocked,
      unresolvedApplicabilityFees: unresolved,
      staffReviewFees: staffReview,
      inactiveFees: inactive,
      disclosures: Array.from(document.disclosures ?? []),
    },
  };
}
