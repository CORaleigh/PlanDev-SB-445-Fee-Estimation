import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const args = {
    catalogDir: "output-prod/catalog-2026-09-15-r6",
    calculationBlocker: "",
    limit: 25,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const item = argv[i];
    if (item === "--catalog-dir") {
      args.catalogDir = argv[i + 1];
      i += 1;
    } else if (item === "--calculation-blocker") {
      args.calculationBlocker = String(argv[i + 1] || "").trim();
      i += 1;
    } else if (item === "--limit") {
      const parsed = Number(argv[i + 1]);
      if (!Number.isInteger(parsed) || parsed < 1) {
        throw new Error("--limit must be a positive integer");
      }
      args.limit = parsed;
      i += 1;
    }
  }
  return args;
}

async function loadRuntime() {
  const runtimePath = path.resolve("docs/runtime.js");
  const source = fs.readFileSync(runtimePath, "utf8");
  const dataUrl =
    "data:text/javascript;base64,"
    + Buffer.from(source, "utf8").toString("base64");
  return import(dataUrl);
}

function readJson(filename) {
  return JSON.parse(fs.readFileSync(filename, "utf8"));
}

function addCounts(target, source) {
  for (const [key, value] of Object.entries(source || {})) {
    target[key] = (target[key] || 0) + Number(value || 0);
  }
}

function addSetValue(target, key, value) {
  if (!target.has(key)) target.set(key, new Set());
  target.get(key).add(value);
}

function stableValue(value) {
  if (value === undefined) return "<undefined>";
  if (typeof value === "string") return JSON.stringify(value);
  return JSON.stringify(value);
}

function applicabilitySignature(item) {
  const code = String(item?.code || "UNKNOWN_APPLICABILITY_BLOCKER");
  if (code === "UNSUPPORTED_CONDITION_OPERATOR") {
    const operatorId = item?.operatorID;
    return `operatorID=${operatorId === null || operatorId === undefined ? "unknown" : operatorId}`;
  }
  if (code === "UNSUPPORTED_APPLICABILITY_CONDITION") {
    return `condition-kind=${String(item?.conditionKind || "unknown")}`;
  }
  if (code === "MISSING_CONDITION_RULE") return "missing-condition-rule";
  if (code === "INCOMPLETE_CONDITION") return "incomplete-condition";
  return `code=${code}`;
}

function percent(numerator, denominator) {
  if (!denominator) return "0.0";
  return ((100 * numerator) / denominator).toFixed(1);
}

const args = parseArgs(process.argv);
const catalogDir = path.resolve(args.catalogDir);
const manifest = readJson(path.join(catalogDir, "manifest.json"));
const runtime = await loadRuntime();

const summaries = [];
const archetypes = {};
const applicabilityDiagnostics = {};
const applicabilityRowsBySignature = new Map();
const allApplicabilityRows = new Set();
const operatorContexts = new Map();
const calculationContexts = new Map();
const uiBlockerControls = {};
const uiBlockerOptions = new Map();
const uiBlockerContexts = new Map();
let automaticFees = 0;
let supportedAutomaticFees = 0;
let assessableAutomaticFees = 0;
let supportedAssessableAutomaticFees = 0;
let dependencyOnlyAutomaticFees = 0;
let reviewOnlyFees = 0;
let uiReadyOptions = 0;
let fullySupportedOptions = 0;
let optionsWithAnySupportedAutomaticFee = 0;

for (const item of manifest.options || []) {
  const document = readJson(path.join(catalogDir, item.path));
  const summary = runtime.analyzeOptionCapabilities(document);
  const ui = runtime.buildOptionUiSchema(document);
  summaries.push(summary);

  for (const item of ui.blockingRuntimeDiagnostics || []) {
    const signature = applicabilitySignature(item);
    applicabilityDiagnostics[signature] =
      (applicabilityDiagnostics[signature] || 0) + 1;
    const rowKey = [
      String(summary.optionKey || ""),
      String(item?.feeTemplateFeeId || ""),
    ].join("|");
    allApplicabilityRows.add(rowKey);
    addSetValue(applicabilityRowsBySignature, signature, rowKey);

    if (
      String(item?.code || "") === "UNSUPPORTED_CONDITION_OPERATOR"
      && item?.operatorID !== null
      && item?.operatorID !== undefined
    ) {
      const operatorId = String(item.operatorID);
      if (!operatorContexts.has(operatorId)) {
        operatorContexts.set(operatorId, new Map());
      }
      const fieldKey = String(item?.operandValue || "<missing-field>");
      const friendly = String(
        item?.operandFriendlyName || fieldKey,
      );
      const comparison = stableValue(item?.comparisonValue);
      const contextKey = [fieldKey, friendly, comparison].join("|");
      const contexts = operatorContexts.get(operatorId);
      if (!contexts.has(contextKey)) {
        contexts.set(contextKey, {
          fieldKey,
          friendly,
          comparison,
          diagnostics: 0,
          feeRows: new Set(),
          optionKeys: new Set(),
          feeNames: new Set(),
        });
      }
      const context = contexts.get(contextKey);
      context.diagnostics += 1;
      context.feeRows.add(rowKey);
      context.optionKeys.add(String(summary.optionKey || ""));
      if (item?.feeName) context.feeNames.add(String(item.feeName));
    }
  }

  for (const detail of summary.blockedAutomaticFeeDetails || []) {
    const capability = String(detail?.capability || "unknown");
    if (capability === "applicability") continue;

    const mode = String(detail?.calculationMode || "unknown");
    const sources = Array.from(detail?.inputSources || []).join("+") || "(none)";
    const inputCount = Number(detail?.inputCount || 0);
    const expressionType = detail?.expressionType === null
      || detail?.expressionType === undefined
      ? "(none)"
      : String(detail.expressionType);
    const contextKey = [
      capability,
      mode,
      String(inputCount),
      sources,
      expressionType,
    ].join("|");

    if (!calculationContexts.has(contextKey)) {
      calculationContexts.set(contextKey, {
        capability,
        mode,
        inputCount,
        sources,
        expressionType,
        feeRows: 0,
        optionKeys: new Set(),
        feeNames: new Set(),
        feeIds: new Set(),
        inputKeys: new Set(),
      });
    }
    const context = calculationContexts.get(contextKey);
    context.feeRows += 1;
    context.optionKeys.add(String(summary.optionKey || ""));
    if (detail?.feeName) context.feeNames.add(String(detail.feeName));
    if (detail?.feeTemplateFeeId) {
      context.feeIds.add(String(detail.feeTemplateFeeId));
    }
    for (const key of detail?.inputKeys || []) {
      if (key) context.inputKeys.add(String(key));
    }
  }

  for (const item of ui.uiBlockers || []) {
    const code = String(item?.code || "UNKNOWN_UI_BLOCKER");
    uiBlockerControls[code] = (uiBlockerControls[code] || 0) + 1;
    addSetValue(
      uiBlockerOptions,
      code,
      String(summary.optionKey || ""),
    );

    const fieldKey = String(item?.fieldKey || "");
    const control = (ui.controls || []).find(
      (candidate) => String(candidate?.key || "") === fieldKey,
    ) || {};
    const contextKey = [
      code,
      String(summary.optionKey || ""),
      fieldKey,
    ].join("|");
    uiBlockerContexts.set(contextKey, {
      code,
      optionKey: String(summary.optionKey || ""),
      permitType: String(summary.permitType || ""),
      workClass: String(summary.workClass || ""),
      fieldKey,
      displayLabel: String(control?.displayLabel || control?.label || fieldKey),
      valueType: String(control?.valueType || ""),
      controlType: String(control?.controlType || ""),
      choices: Array.from(control?.choices || []).map((choice) => ({
        value: choice?.value,
        label: choice?.label,
      })),
    });
  }

  automaticFees += summary.automaticFees;
  supportedAutomaticFees += summary.structurallySupportedAutomaticFees;
  assessableAutomaticFees += summary.assessableAutomaticFees || 0;
  supportedAssessableAutomaticFees +=
    summary.structurallySupportedAssessableAutomaticFees || 0;
  dependencyOnlyAutomaticFees +=
    summary.dependencyOnlyAutomaticFees || 0;
  reviewOnlyFees += summary.reviewOnlyFees;
  if (summary.uiDisplayReady) uiReadyOptions += 1;
  if (summary.structurallySupportedAutomaticFees > 0) {
    optionsWithAnySupportedAutomaticFee += 1;
  }
  if (
    summary.uiDisplayReady
    && summary.blockedAutomaticFees === 0
  ) {
    fullySupportedOptions += 1;
  }
  addCounts(archetypes, summary.blockerArchetypes);
}

const blockedOptions = summaries
  .filter((item) => item.blockedAutomaticFees > 0)
  .sort((a, b) => {
    if (b.blockedAutomaticFees !== a.blockedAutomaticFees) {
      return b.blockedAutomaticFees - a.blockedAutomaticFees;
    }
    return String(a.permitType).localeCompare(String(b.permitType));
  });

console.log("STATIC BROWSER RUNTIME COVERAGE");
console.log(`Catalog: ${manifest.catalogVersion || "unknown"}`);
console.log(`Options: ${summaries.length}`);
console.log(
  `UI display-ready options: ${uiReadyOptions}/${summaries.length} (${percent(uiReadyOptions, summaries.length)}%)`,
);
console.log(
  `Options with at least one structurally supported automatic fee: ${optionsWithAnySupportedAutomaticFee}/${summaries.length} (${percent(optionsWithAnySupportedAutomaticFee, summaries.length)}%)`,
);
console.log(
  `Fully structurally supported options: ${fullySupportedOptions}/${summaries.length} (${percent(fullySupportedOptions, summaries.length)}%)`,
);
console.log(
  `Structurally supported automatic fees: ${supportedAutomaticFees}/${automaticFees} (${percent(supportedAutomaticFees, automaticFees)}%)`,
);
console.log(
  `Structurally supported assessable automatic fees: ${supportedAssessableAutomaticFees}/${assessableAutomaticFees} (${percent(supportedAssessableAutomaticFees, assessableAutomaticFees)}%)`,
);
console.log(
  `Dependency-only automatic fee assignments: ${dependencyOnlyAutomaticFees}`,
);
console.log(`Review/manual/event/inactive fees: ${reviewOnlyFees}`);
console.log("");

console.log("BLOCKER ARCHETYPES");
const ranked = Object.entries(archetypes)
  .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
if (!ranked.length) {
  console.log("(none)");
} else {
  for (const [name, count] of ranked) {
    console.log(`${name}\t${count}`);
  }
}

console.log("");
console.log("APPLICABILITY BLOCKER DETAILS");
console.log(
  `Blocked automatic fee rows with applicability diagnostics: ${allApplicabilityRows.size}`,
);
const applicabilityRanked = Object.keys(applicabilityDiagnostics)
  .map((name) => [
    name,
    applicabilityRowsBySignature.get(name)?.size || 0,
    applicabilityDiagnostics[name] || 0,
  ])
  .sort((a, b) => b[1] - a[1] || b[2] - a[2] || a[0].localeCompare(b[0]));
if (!applicabilityRanked.length) {
  console.log("(none)");
} else {
  for (const [name, feeRows, diagnosticCount] of applicabilityRanked) {
    console.log(
      `${name}\tfee-rows=${feeRows}\tdiagnostics=${diagnosticCount}`,
    );
  }
}

console.log("");
console.log("UNSUPPORTED OPERATOR CONTEXTS");
const operatorIds = Array.from(operatorContexts.keys())
  .sort((a, b) => Number(a) - Number(b));
if (!operatorIds.length) {
  console.log("(none)");
} else {
  for (const operatorId of operatorIds) {
    console.log(`operatorID=${operatorId}`);
    const contexts = Array.from(operatorContexts.get(operatorId).values())
      .sort((a, b) => {
        if (b.feeRows.size !== a.feeRows.size) {
          return b.feeRows.size - a.feeRows.size;
        }
        if (b.diagnostics !== a.diagnostics) {
          return b.diagnostics - a.diagnostics;
        }
        return a.fieldKey.localeCompare(b.fieldKey);
      });
    for (const context of contexts.slice(0, 25)) {
      const feeExamples = Array.from(context.feeNames)
        .sort()
        .slice(0, 3)
        .join(" | ");
      console.log(
        [
          `field=${context.fieldKey}`,
          `label=${context.friendly}`,
          `compare=${context.comparison}`,
          `fee-rows=${context.feeRows.size}`,
          `options=${context.optionKeys.size}`,
          `diagnostics=${context.diagnostics}`,
          `fees=${feeExamples || "(none)"}`,
        ].join("\t"),
      );
    }
    if (contexts.length > 25) {
      console.log(`... ${contexts.length - 25} additional context(s)`);
    }
  }
}

console.log("");
console.log("CALCULATION BLOCKER CONTEXTS");
const calculationRanked = Array.from(calculationContexts.values())
  .filter((item) =>
    !args.calculationBlocker
    || item.capability === args.calculationBlocker)
  .sort((a, b) => {
    if (b.feeRows !== a.feeRows) return b.feeRows - a.feeRows;
    return a.capability.localeCompare(b.capability);
  });
if (!calculationRanked.length) {
  console.log("(none)");
} else {
  for (const context of calculationRanked.slice(0, args.limit)) {
    const feeExamples = Array.from(context.feeNames)
      .sort()
      .slice(0, 4)
      .join(" | ");
    const feeIds = Array.from(context.feeIds)
      .sort()
      .slice(0, 12)
      .join(" | ");
    const inputExamples = Array.from(context.inputKeys)
      .sort()
      .slice(0, 6)
      .join(" | ");
    console.log(
      [
        `blocker=${context.capability}`,
        `mode=${context.mode}`,
        `inputs=${context.inputCount}`,
        `sources=${context.sources}`,
        `expressionType=${context.expressionType}`,
        `fee-rows=${context.feeRows}`,
        `options=${context.optionKeys.size}`,
        `fees=${feeExamples || "(none)"}`,
        `fee-ids=${feeIds || "(none)"}`,
        `input-keys=${inputExamples || "(none)"}`,
      ].join("\t"),
    );
  }
  if (calculationRanked.length > args.limit) {
    console.log(
      `... ${calculationRanked.length - args.limit} additional calculation blocker context(s) omitted`,
    );
  }
}

console.log("");
console.log("UI BLOCKERS");
const uiRanked = Object.keys(uiBlockerControls)
  .map((code) => [
    code,
    uiBlockerOptions.get(code)?.size || 0,
    uiBlockerControls[code] || 0,
  ])
  .sort((a, b) => b[1] - a[1] || b[2] - a[2] || a[0].localeCompare(b[0]));
if (!uiRanked.length) {
  console.log("(none)");
} else {
  for (const [code, optionCount, controlCount] of uiRanked) {
    console.log(
      `${code}\toptions=${optionCount}\tcontrols=${controlCount}`,
    );
  }
}

console.log("");
console.log("UI BLOCKER CONTEXTS");
const uiContexts = Array.from(uiBlockerContexts.values())
  .sort((a, b) =>
    a.code.localeCompare(b.code)
    || a.permitType.localeCompare(b.permitType)
    || a.workClass.localeCompare(b.workClass)
    || a.fieldKey.localeCompare(b.fieldKey));
if (!uiContexts.length) {
  console.log("(none)");
} else {
  for (const context of uiContexts) {
    const choicePreview = context.choices
      .slice(0, 8)
      .map((choice) =>
        `${stableValue(choice.value)}=>${stableValue(choice.label)}`)
      .join(" | ");
    console.log(
      [
        `blocker=${context.code}`,
        `permit=${context.permitType}`,
        `workclass=${context.workClass}`,
        `field=${context.fieldKey}`,
        `label=${context.displayLabel}`,
        `valueType=${context.valueType || "(none)"}`,
        `controlType=${context.controlType || "(none)"}`,
        `choices=${choicePreview || "(none)"}`,
      ].join("\t"),
    );
  }
}

console.log("");
console.log("TOP OPTIONS BY BLOCKED AUTOMATIC FEES");
for (const item of blockedOptions.slice(0, args.limit)) {
  console.log(
    [
      item.blockedAutomaticFees,
      item.automaticFees,
      item.permitType,
      item.workClass,
      JSON.stringify(item.blockerArchetypes),
    ].join("\t"),
  );
}
