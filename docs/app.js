import {
  buildOptionUiSchema,
  cleanStaffFacingLabel,
  deriveInactiveScenarioFeeIds,
  displayOptionLabel,
  evaluateOption,
} from "./runtime.js";
import { OPTION_LABEL_MODE, STAKEHOLDER_PREVIEW } from "./site-config.js";

const DATA_ROOT = "./data/";
const state = {
  manifest: null,
  optionDocument: null,
  uiSchema: null,
  loadSequence: 0,
};

const elements = {
  stakeholderPreview: document.getElementById("stakeholder-preview"),
  stakeholderPreviewHeading: document.getElementById("stakeholder-preview-heading"),
  stakeholderPreviewMessage: document.getElementById("stakeholder-preview-message"),
  permitType: document.getElementById("permit-type"),
  workClass: document.getElementById("work-class"),
  sourceNote: document.getElementById("source-note"),
  runtimeNotice: document.getElementById("runtime-notice"),
  projectInputs: document.getElementById("project-inputs"),
  groupActions: document.getElementById("group-actions"),
  expandAllGroups: document.getElementById("expand-all-groups"),
  collapseAllGroups: document.getElementById("collapse-all-groups"),
  frequencyControl: document.getElementById("frequency-control"),
  showFrequency: document.getElementById("show-frequency"),
  prevalenceHelp: document.getElementById("input-prevalence-help"),
  relatedPermitControl: document.getElementById("related-permit-control"),
  showRelatedPermits: document.getElementById("show-related-permits"),
  relatedPermitsPanel: document.getElementById("related-permits-panel"),
  relatedPermits: document.getElementById("related-permits"),
  controls: document.getElementById("project-controls"),
  calculate: document.getElementById("calculate"),
  error: document.getElementById("error"),
  results: document.getElementById("results"),
  status: document.getElementById("status"),
  total: document.getElementById("total"),
  estimateLimitations: document.getElementById("estimate-limitations"),
  estimateInputs: document.getElementById("estimate-inputs"),
  calculatedFees: document.getElementById("calculated-fees"),
  reviewItems: document.getElementById("review-items"),
};

function money(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(value || 0));
}

function clear(element) {
  while (element.firstChild) element.removeChild(element.firstChild);
}

function optionElement(value, label, { optionName = false } = {}) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = optionName
    ? displayOptionLabel(label, OPTION_LABEL_MODE)
    : cleanStaffFacingLabel(label, { surface: "choice" });
  return option;
}

function manifestOptions() {
  return Array.from(state.manifest?.options ?? []);
}

function selectedManifestOption() {
  const permitType = elements.permitType.value;
  const workClass = elements.workClass.value;
  return manifestOptions().find(
    (item) =>
      String(item.permitType) === permitType
      && String(item.workClass) === workClass,
  );
}

function populatePermitTypes() {
  const values = Array.from(
    new Set(manifestOptions().map((item) => String(item.permitType))),
  ).sort((a, b) =>
    displayOptionLabel(a, OPTION_LABEL_MODE).localeCompare(
      displayOptionLabel(b, OPTION_LABEL_MODE),
    ));

  clear(elements.permitType);
  for (const value of values) {
    elements.permitType.appendChild(
      optionElement(value, value, { optionName: true }),
    );
  }
}

function populateWorkClasses(preferred = null) {
  const permitType = elements.permitType.value;
  const values = Array.from(
    new Set(
      manifestOptions()
        .filter((item) => String(item.permitType) === permitType)
        .map((item) => String(item.workClass)),
    ),
  ).sort((a, b) =>
    displayOptionLabel(a, OPTION_LABEL_MODE).localeCompare(
      displayOptionLabel(b, OPTION_LABEL_MODE),
    ));

  clear(elements.workClass);
  for (const value of values) {
    elements.workClass.appendChild(
      optionElement(value, value, { optionName: true }),
    );
  }
  if (preferred && values.includes(preferred)) {
    elements.workClass.value = preferred;
  }
}

function formatPrevalencePercent(value) {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return null;
  if (percent === 0) return "0%";
  return `${Math.round(percent)}%`;
}

function historicalPrevalenceBadge(control) {
  const evidence = control.historicalPrevalence ?? {};
  if (String(evidence.status ?? "") === "limited-history") {
    const badge = document.createElement("span");
    badge.className = "frequency-badge limited-history";
    badge.textContent = "Limited history";
    badge.title = "Limited historical information is available.";
    return badge;
  }

  const percent = Number(evidence.percent);
  if (
    String(evidence.status ?? "") !== "available"
    || !Number.isFinite(percent)
    || percent < 0
    || percent > 100
  ) {
    return null;
  }

  const badge = document.createElement("span");
  badge.className = "frequency-badge has-frequency";
  const percentText = formatPrevalencePercent(percent);
  const hue = Math.round((percent / 100) * 120);
  badge.textContent = percentText;
  badge.style.setProperty("--frequency-hue", String(hue));
  badge.title = `Past permits with this detail: ${percentText}.`;
  return badge;
}

function availableOccurrence(evidence) {
  const status = String(evidence?.status ?? "");
  if (status === "limited-history") {
    return Boolean(evidence?.hasOccurrences);
  }
  const percent = Number(evidence?.percent);
  return status === "available"
    && Number.isFinite(percent)
    && percent >= 0
    && percent <= 100;
}

function occurrenceBadge(evidence, label) {
  if (!availableOccurrence(evidence)) return null;
  const badge = document.createElement("span");
  badge.className = "occurrence-badge has-frequency";
  if (String(evidence?.status ?? "") === "limited-history") {
    badge.classList.add("limited-history");
    badge.textContent = "Limited history";
    badge.title = `${label}: Limited historical information is available.`;
    return badge;
  }
  const percent = Number(evidence.percent);
  const percentText = formatPrevalencePercent(percent);
  badge.textContent = percentText;
  badge.style.setProperty("--frequency-hue", String(Math.round((percent / 100) * 120)));
  badge.title = `${label}: ${percentText}.`;
  return badge;
}

function relatedPermitOccurrence(relation) {
  const evidence = relation?.history ?? {};
  if (!availableOccurrence(evidence)) return null;
  return {
    evidence,
    basis: String(relation?.historyBasis ?? "permit"),
  };
}

function relatedPermitFeeRows(relation) {
  return Array.from(relation?.fees ?? [])
    .filter((fee) =>
      String(fee?.assessmentRole ?? "") !== "dependency-only"
      && String(fee?.section ?? "") !== "inactive"
      && availableOccurrence(fee?.history))
    .sort((a, b) => {
      const percentDiff = Number(b.history?.percent ?? -1)
        - Number(a.history?.percent ?? -1);
      if (percentDiff) return percentDiff;
      return displayFeeName(a.name).localeCompare(displayFeeName(b.name));
    });
}

function relatedPermitLabel(relation) {
  const permitType = displayOptionLabel(
    String(relation?.permitType ?? ""),
    OPTION_LABEL_MODE,
  );
  const workClass = displayOptionLabel(
    String(relation?.workClass ?? ""),
    OPTION_LABEL_MODE,
  );
  return workClass ? `${permitType} · ${workClass}` : permitType;
}

function relatedFeeTable(relation, fees) {
  const table = document.createElement("table");
  addTableCaption(
    table,
    `Fees found with ${relatedPermitLabel(relation)}`,
  );
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Fee", "Past projects"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = label;
    headRow.appendChild(th);
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  for (const fee of fees) {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    nameCell.textContent = displayFeeName(fee.name) || "Fee";
    const rateCell = document.createElement("td");
    const badge = occurrenceBadge(
      fee.history,
      "Similar past projects that included this fee",
    );
    if (badge) rateCell.appendChild(badge);
    row.append(nameCell, rateCell);
    body.appendChild(row);
  }
  table.appendChild(body);
  return table;
}

function relatedPermitCard(relation, occurrence, fees) {
  const details = document.createElement("details");
  details.className = "related-permit";

  const summary = document.createElement("summary");
  const badge = occurrenceBadge(
    occurrence.evidence,
    occurrence.basis === "active-fee"
      ? "Similar past projects that included this permit with fees"
      : "Similar past projects that included this permit",
  );
  if (badge) summary.appendChild(badge);

  const label = document.createElement("span");
  label.className = "related-permit-label";
  label.textContent = relatedPermitLabel(relation);
  summary.appendChild(label);

  const family = String(relation?.relationshipFamily ?? "").trim();
  if (family) {
    const familyBadge = document.createElement("span");
    familyBadge.className = "source-badge";
    familyBadge.textContent = family === "MEP"
      ? "Mechanical, Electrical, and Plumbing"
      : cleanStaffFacingLabel(family, { surface: "group" });
    summary.appendChild(familyBadge);
  }

  const count = document.createElement("span");
  count.className = "related-permit-fee-count";
  count.textContent = `${fees.length} fee${fees.length === 1 ? "" : "s"} found`;
  summary.appendChild(count);
  details.appendChild(summary);

  const evidence = document.createElement("p");
  evidence.className = "related-permit-evidence";
  const limited = String(occurrence.evidence?.status ?? "")
    === "limited-history";
  const percentText = limited
    ? null
    : formatPrevalencePercent(occurrence.evidence?.percent);
  if (limited) {
    evidence.textContent =
      "Limited historical information is available for this related permit.";
  } else if (occurrence.basis === "active-fee") {
    evidence.textContent =
      `In past records, ${percentText} of similar projects included this related permit with at least one fee.`;
  } else {
    evidence.textContent =
      `In past records, ${percentText} of similar projects included this related permit. Fee-specific history was not available for these records.`;
  }
  details.appendChild(evidence);

  if (fees.length) {
    details.appendChild(relatedFeeTable(relation, fees));
  } else {
    const empty = document.createElement("p");
    empty.className = "related-permit-evidence";
    empty.textContent = "No fee history is available for this related permit.";
    details.appendChild(empty);
  }
  return details;
}

function updateRelatedPermitVisibility() {
  if (!elements.relatedPermitsPanel) return;
  elements.relatedPermitsPanel.hidden = !(
    elements.showRelatedPermits?.checked
    && !elements.relatedPermitControl?.hidden
  );
}

function renderRelatedPermits() {
  if (!elements.relatedPermits) return;
  clear(elements.relatedPermits);

  const items = Array.from(state.optionDocument?.relatedPermits ?? [])
    .map((relation) => {
      const occurrence = relatedPermitOccurrence(relation);
      const fees = relatedPermitFeeRows(relation);
      return { relation, occurrence, fees };
    })
    .filter(({ occurrence, fees }) =>
      occurrence
      && (
        occurrence.evidence?.status === "limited-history"
        || Number(occurrence.evidence?.percent) > 0
      )
      && (
        occurrence.basis === "active-fee"
        || fees.length > 0
      ))
    .sort((a, b) => {
      const rateDiff = (
        Number(b.occurrence.evidence.percent ?? -1)
        - Number(a.occurrence.evidence.percent ?? -1)
      );
      if (rateDiff) return rateDiff;
      return relatedPermitLabel(a.relation).localeCompare(
        relatedPermitLabel(b.relation),
      );
    });

  if (elements.relatedPermitControl) {
    elements.relatedPermitControl.hidden = items.length === 0;
  }

  for (const item of items) {
    elements.relatedPermits.appendChild(
      relatedPermitCard(item.relation, item.occurrence, item.fees),
    );
  }
  updateRelatedPermitVisibility();
}

function fieldInput(control) {
  const wrapper = document.createElement("label");
  wrapper.className = "field";

  const label = document.createElement("span");
  label.className = "field-label";

  const prevalenceBadge = historicalPrevalenceBadge(control);
  if (prevalenceBadge) label.appendChild(prevalenceBadge);

  const labelText = document.createElement("span");
  labelText.className = "field-label-text";
  labelText.textContent = control.displayLabel || control.label || control.key;
  label.appendChild(labelText);

  wrapper.appendChild(label);

  let input;
  if (control.controlType === "number") {
    input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.step = "any";
    input.inputMode = "decimal";
  } else if (control.controlType === "boolean") {
    input = document.createElement("select");
    input.appendChild(optionElement("", "Choose"));
    input.appendChild(optionElement("true", "Yes"));
    input.appendChild(optionElement("false", "No"));
  } else if (control.controlType === "select") {
    input = document.createElement("select");
    input.appendChild(optionElement("", "Choose"));
    for (const choice of control.choices ?? []) {
      input.appendChild(
        optionElement(String(choice.value ?? ""), String(choice.label ?? "")),
      );
    }
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.disabled = true;
    input.value = "This field is not available";
  }

  input.id = `field-${control.key}`;
  input.dataset.fieldKey = control.key;
  wrapper.appendChild(input);
  return wrapper;
}

function rawFeeName(item) {
  const explicit = String(item?.name || item?.feeName || "").trim();
  if (explicit) return explicit;

  const id = String(item?.feeTemplateFeeId || "").trim();
  if (!id) return "Fee";
  const fee = (state.optionDocument?.fees ?? []).find(
    (candidate) =>
      String(candidate?.feeTemplateFeeId || "").trim() === id,
  );
  return String(fee?.name || "Fee").trim();
}

function displayFeeName(value) {
  let name = String(value || "").trim();
  if (!name) return "";

  if (/^\*+REINSPECTION FEES BELOW THIS LINE\*+$/i.test(name)) {
    return "";
  }

  name = name.replace(
    /^(?:DSINSP|MISC|DSENG|DSLC|ENG|FIRE|FR|PRCR|PU|ZP)\s*[-–]\s*/i,
    "",
  );

  return cleanStaffFacingLabel(name, { surface: "fee" });
}

function uniqueByDisplayName(items) {
  const grouped = new Map();
  for (const item of items || []) {
    const name = displayFeeName(rawFeeName(item));
    if (!name) continue;
    if (!grouped.has(name)) grouped.set(name, []);
    grouped.get(name).push(item);
  }
  return grouped;
}

function renderRuntimeNotice() {
  clear(elements.runtimeNotice);
  const blockers = state.uiSchema?.blockingRuntimeDiagnostics ?? [];

  if (!blockers.length) {
    elements.runtimeNotice.hidden = true;
    return;
  }

  elements.runtimeNotice.hidden = false;
  elements.runtimeNotice.className = "notice warning";
  const strong = document.createElement("strong");
  strong.textContent = "Some fees need review.";
  elements.runtimeNotice.appendChild(strong);

  const grouped = uniqueByDisplayName(blockers);
  const list = document.createElement("ul");
  for (const [displayName, group] of grouped.entries()) {
    const item = document.createElement("li");
    const name = document.createElement("strong");
    name.textContent = displayName;
    item.appendChild(name);
    item.append(": The tool cannot calculate this fee. Staff review is required.");

    list.appendChild(item);
  }
  elements.runtimeNotice.appendChild(list);

  const note = document.createElement("p");
  note.textContent =
    "Other fees can still be calculated. The fees listed above are not included in the estimate.";
  elements.runtimeNotice.appendChild(note);
}

function controlGroup(group, controls) {
  const details = document.createElement("details");
  details.className = "control-group";
  details.dataset.groupKey = group.key;
  details.open = !group.collapsedByDefault;

  const summary = document.createElement("summary");
  const title = document.createElement("span");
  title.className = "control-group-title";
  title.textContent = group.label === "Additional Fee Details"
    ? "Additional fee details" : group.label;
  const count = document.createElement("span");
  count.className = "control-group-count";
  count.textContent = `${controls.length} question${controls.length === 1 ? "" : "s"}`;
  summary.append(title, count);
  details.appendChild(summary);

  const help = document.createElement("p");
  help.className = "control-group-help";
  help.textContent = group.kind === "scenario"
    ? "If this situation applies, enter the details below. Otherwise, leave this section blank."
    : "Open this section if additional project details apply.";
  details.appendChild(help);

  const grid = document.createElement("div");
  grid.className = "control-group-fields";
  for (const control of controls) {
    grid.appendChild(fieldInput(control));
  }
  details.appendChild(grid);
  return details;
}

function setAllControlGroupsOpen(open) {
  for (const details of elements.controls.querySelectorAll(
    "details.control-group",
  )) {
    details.open = Boolean(open);
  }
}

function updateFrequencyVisibility() {
  elements.controls.classList.toggle(
    "show-frequency",
    Boolean(elements.showFrequency?.checked),
  );
}

function renderControls() {
  clear(elements.controls);
  const controls = (state.uiSchema?.controls ?? [])
    .filter((control) => control.displayReady);
  elements.projectInputs.hidden = controls.length === 0;

  const hasDirectPrevalence = controls.some((control) => {
    const evidence = control.historicalPrevalence ?? {};
    const percent = Number(evidence.percent);
    return (
      evidence.status === "limited-history"
      || (
        evidence.status === "available"
        && Number.isFinite(percent)
        && percent >= 0
        && percent <= 100
      )
    );
  });
  if (elements.frequencyControl) {
    elements.frequencyControl.hidden = !hasDirectPrevalence;
  }
  if (elements.prevalenceHelp) {
    elements.prevalenceHelp.hidden = !hasDirectPrevalence;
  }

  const presentationGroups = Array.from(
    state.uiSchema?.presentation?.groups ?? [],
  );
  if (elements.groupActions) {
    elements.groupActions.hidden = presentationGroups.length === 0;
  }

  const groupedKeys = new Set();
  const ungrouped = controls.filter((control) => !control.presentationGroup);
  for (const control of ungrouped) {
    elements.controls.appendChild(fieldInput(control));
  }

  for (const group of presentationGroups) {
    const groupControls = controls.filter(
      (control) => control.presentationGroup?.key === group.key,
    );
    if (!groupControls.length) continue;
    for (const control of groupControls) groupedKeys.add(control.key);
    elements.controls.appendChild(controlGroup(group, groupControls));
  }

  for (const control of controls) {
    if (ungrouped.includes(control) || groupedKeys.has(control.key)) continue;
    elements.controls.appendChild(fieldInput(control));
  }
  updateFrequencyVisibility();
  renderRelatedPermits();
}

function rawValues() {
  const values = {};
  for (const control of state.uiSchema?.controls ?? []) {
    if (!control.displayReady) continue;
    const input = document.getElementById(`field-${control.key}`);
    if (!input) continue;
    if (!input.checkValidity()) {
      input.reportValidity();
      throw new Error(
        `${control.displayLabel || control.label || control.key} contains an invalid value.`,
      );
    }
    values[control.key] = input.value;
  }
  return values;
}

const PROMINENT_DISCLOSURE_CODES = new Set([
  "ENERGOV_SOURCE_OF_TRUTH",
  "ESTIMATE_NOT_INVOICE",
]);

const PROMINENT_DISCLOSURE_TEXT = new Map([
  [
    "ENERGOV_SOURCE_OF_TRUTH",
    {
      title: "Current fee data",
      text: "Fees and calculation rules may change. Current assessed charges may differ.",
    },
  ],
  [
    "ESTIMATE_NOT_INVOICE",
    {
      title: "Estimate only",
      text: "This is an estimate, not an invoice or a guarantee of final charges.",
    },
  ],
]);

function displayInputValue(control, rawValue) {
  const value = String(rawValue ?? "").trim();
  if (control.controlType === "boolean") {
    if (value === "true") return "Yes";
    if (value === "false") return "No";
  }
  if (control.controlType === "select") {
    const choice = (control.choices ?? []).find(
      (item) => String(item.value ?? "") === value,
    );
    if (choice) return String(choice.label ?? value);
  }
  return value;
}

function formatPublicDate(value) {
  const text = String(value ?? "").trim();
  if (!text) return "";
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return text;
  const date = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
  );
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

function renderStakeholderPreview() {
  if (!elements.stakeholderPreview) return;

  const enabled = Boolean(STAKEHOLDER_PREVIEW?.enabled);
  elements.stakeholderPreview.hidden = !enabled;
  if (!enabled) return;

  elements.stakeholderPreviewHeading.textContent =
    String(STAKEHOLDER_PREVIEW?.heading || "Preview");
  elements.stakeholderPreviewMessage.textContent =
    String(STAKEHOLDER_PREVIEW?.message || "");
}


function dataFreshnessText(document) {
  const configuration = document?.configuration ?? {};
  const parts = [];
  const configurationDate = formatPublicDate(configuration.asOfDate);
  if (configurationDate) {
    parts.push("Fee configuration as of " + configurationDate);
  }

  const period = configuration.historicalPeriod ?? {};
  const start = formatPublicDate(period.startDate);
  const end = formatPublicDate(period.endDate);
  if (start && end) {
    parts.push("Past projects reviewed: " + start + " to " + end);
  }
  return parts.length ? parts.join(". ") + "." : "";
}

function renderSourceNote() {
  const show = Boolean(STAKEHOLDER_PREVIEW?.enabled && STAKEHOLDER_PREVIEW?.showSourceDates);
  const note = show ? dataFreshnessText(state.optionDocument) : "";
  elements.sourceNote.hidden = !note;
  elements.sourceNote.querySelector("em").textContent = note;
}

function renderEstimateLimitations() {
  clear(elements.estimateLimitations);
  const disclosures = (state.optionDocument?.disclosures ?? []).filter(
    (item) => PROMINENT_DISCLOSURE_CODES.has(String(item.code ?? "")),
  );
  if (!disclosures.length) return;

  const heading = document.createElement("h3");
  heading.textContent = "Important information";
  elements.estimateLimitations.appendChild(heading);

  const list = document.createElement("ul");
  for (const disclosure of disclosures) {
    const display = PROMINENT_DISCLOSURE_TEXT.get(String(disclosure.code ?? ""));
    const item = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = String(display?.title || disclosure.title || "Notice");
    item.appendChild(title);
    const text = String(display?.text || disclosure.text || "").trim();
    if (text) item.append(`: ${text}`);
    list.appendChild(item);
  }
  elements.estimateLimitations.appendChild(list);
}

function renderEstimateInputs(values) {
  clear(elements.estimateInputs);
  const controls = (state.uiSchema?.controls ?? []).filter(
    (control) => control.displayReady,
  );
  const supplied = controls
    .map((control) => ({
      control,
      rawValue: values?.[control.key],
    }))
    .filter(({ rawValue }) => String(rawValue ?? "").trim() !== "");

  const heading = document.createElement("h3");
  heading.textContent = "Project information used";
  elements.estimateInputs.appendChild(heading);

  if (!supplied.length) {
    const empty = document.createElement("p");
    empty.textContent = "No project details were entered.";
    elements.estimateInputs.appendChild(empty);
    return;
  }

  const table = document.createElement("table");
  addTableCaption(table, "Project inputs used");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Project detail", "Value"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.appendChild(cell);
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  for (const { control, rawValue } of supplied) {
    const row = document.createElement("tr");
    const label = document.createElement("td");
    label.textContent =
      control.displayLabel || control.label || control.key;
    const value = document.createElement("td");
    value.textContent = displayInputValue(control, rawValue);
    row.append(label, value);
    body.appendChild(row);
  }
  table.appendChild(body);
  elements.estimateInputs.appendChild(table);
}

function addTableCaption(table, text) {
  const caption = document.createElement("caption");
  caption.className = "sr-only";
  caption.textContent = text;
  table.appendChild(caption);
}

function feeTable(items) {
  const table = document.createElement("table");
  addTableCaption(table, "Calculated fees");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const label of ["Fee", "Amount"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    if (label === "Amount") cell.className = "amount";
    headRow.appendChild(cell);
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  for (const item of items) {
    const displayName = displayFeeName(rawFeeName(item));
    if (!displayName) continue;
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = displayName;
    const amount = document.createElement("td");
    amount.className = "amount";
    amount.textContent = money(item.amount);
    row.append(name, amount);
    body.appendChild(row);
  }
  table.appendChild(body);
  return table;
}

function reviewTable(report, includedNames) {
  const grouped = new Map();

  function add(items, reason) {
    for (const item of items ?? []) {
      const name = displayFeeName(rawFeeName(item));
      if (!name || includedNames.has(name)) continue;
      if (!grouped.has(name)) {
        grouped.set(name, {
          reasons: new Set(),
        });
      }
      const entry = grouped.get(name);
      entry.reasons.add(reason);
    }
  }

  add(
    report.staffReviewFees,
    "This fee is determined or confirmed by staff.",
  );
  add(
    report.unresolvedApplicabilityFees,
    "Staff must confirm whether this fee applies.",
  );
  add(
    report.blockedFees,
    "Staff must confirm or calculate this fee amount.",
  );

  const items = [];
  for (const [name, entry] of grouped.entries()) {
    const reasons = Array.from(entry.reasons);
    items.push({
      name,
      reason: reasons.length > 1
        ? "Staff must determine whether this fee applies and confirm the amount."
        : reasons[0],
    });
  }
  items.sort((a, b) => a.name.localeCompare(b.name));

  clear(elements.reviewItems);
  if (!items.length) return;

  const heading = document.createElement("h3");
  heading.textContent = "Fees that need review";
  elements.reviewItems.appendChild(heading);

  const table = document.createElement("table");
  addTableCaption(table, "Items that still need review");
  const head = document.createElement("thead");
  const row = document.createElement("tr");
  for (const label of ["Fee", "Reason"]) {
    const cell = document.createElement("th");
    cell.textContent = label;
    row.appendChild(cell);
  }
  head.appendChild(row);
  table.appendChild(head);

  const body = document.createElement("tbody");
  for (const item of items) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = item.name;
    const reason = document.createElement("td");
    reason.textContent = item.reason;
    tr.append(name, reason);
    body.appendChild(tr);
  }
  table.appendChild(body);
  elements.reviewItems.appendChild(table);
}

function renderPossibleAdditionalFees(report, includedNames) {
  const names = Array.from(
    new Set(
      (report.possibleAdditionalFees ?? [])
        .map((item) => displayFeeName(rawFeeName(item)))
        .filter((name) => name && !includedNames.has(name)),
    ),
  ).sort((a, b) => a.localeCompare(b));

  if (!names.length) return;

  const heading = document.createElement("h3");
  heading.textContent = "Other fees that may apply";
  elements.reviewItems.appendChild(heading);

  const note = document.createElement("p");
  note.textContent =
    "These fees are not included in the estimate. They may apply later depending on the project or events such as reinspections.";
  elements.reviewItems.appendChild(note);

  const list = document.createElement("ul");
  for (const name of names) {
    const item = document.createElement("li");
    item.textContent = name;
    list.appendChild(item);
  }
  elements.reviewItems.appendChild(list);
}

function renderReport(report, values) {
  elements.results.hidden = false;

  clear(elements.status);
  const strong = document.createElement("strong");
  strong.textContent = report.estimateStatusLabel || "Estimate";
  elements.status.appendChild(strong);

  const reviewNames = new Set();
  for (const collection of [
    report.staffReviewFees,
    report.unresolvedApplicabilityFees,
    report.blockedFees,
  ]) {
    for (const item of collection ?? []) {
      const name = displayFeeName(rawFeeName(item));
      if (name) reviewNames.add(name);
    }
  }
  if (reviewNames.size) {
    const line = document.createElement("div");
    line.textContent =
      `The amount shown includes only the fees the tool can calculate. Staff must review ${reviewNames.size} item${reviewNames.size === 1 ? "" : "s"}.`;
    elements.status.appendChild(line);
  }

  elements.total.textContent = report.requiresStaffReview
    ? `Calculated fees so far: ${money(report.estimatedTotal)}`
    : `Estimated total: ${money(report.estimatedTotal)}`;

  renderEstimateLimitations();
  renderEstimateInputs(values);

  clear(elements.calculatedFees);
  const heading = document.createElement("h3");
  heading.textContent = "Calculated fees";
  elements.calculatedFees.appendChild(heading);

  const fees = [
    ...(report.regularPermitFees ?? []),
    ...(report.workingWithoutPermitAdditionalFees ?? []),
  ].filter((item) =>
    Math.abs(Number(item.amount || 0)) >= 0.005
    && Boolean(displayFeeName(item.name)),
  );
  const includedNames = new Set(
    fees
      .map((item) => displayFeeName(item.name))
      .filter(Boolean),
  );
  if (fees.length) {
    elements.calculatedFees.appendChild(feeTable(fees));
  } else {
    const empty = document.createElement("p");
    empty.textContent = "No fees could be calculated from the information available.";
    elements.calculatedFees.appendChild(empty);
  }

  reviewTable(report, includedNames);
  renderPossibleAdditionalFees(report, includedNames);
}

function showError(error) {
  elements.error.hidden = false;
  elements.error.textContent = error?.message || String(error);
}

function hideError() {
  elements.error.hidden = true;
  elements.error.textContent = "";
}

async function loadOption() {
  const sequence = ++state.loadSequence;
  hideError();
  elements.results.hidden = true;
  elements.calculate.disabled = true;
  elements.projectInputs.hidden = true;
  elements.runtimeNotice.hidden = true;
  state.optionDocument = null;
  state.uiSchema = null;
  renderSourceNote();
  const selected = selectedManifestOption();
  if (!selected) return;

  try {
    const response = await fetch(
      DATA_ROOT + selected.path,
      { cache: "no-store" },
    );
    if (sequence !== state.loadSequence) return;
    if (!response.ok) {
      throw new Error("Could not load the selected permit information. Please refresh the page and try again.");
    }

    const document = await response.json();
    if (sequence !== state.loadSequence) return;
    state.optionDocument = document;
    state.uiSchema = buildOptionUiSchema(document);

    renderSourceNote();

    renderRuntimeNotice();
    renderControls();
    elements.calculate.disabled = false;
  } catch (error) {
    if (sequence === state.loadSequence) throw error;
  }
}

function optionHash(permitType, workClass) {
  return (
    `#${encodeURIComponent(String(permitType ?? ""))}`
    + `#${encodeURIComponent(String(workClass ?? ""))}`
  );
}

function preferredFromHash() {
  const raw = String(window.location.hash || "").replace(/^#/, "");
  if (!raw) return null;

  const parts = raw.split("#");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;

  try {
    return {
      permitType: decodeURIComponent(parts[0]),
      workClass: decodeURIComponent(parts[1]),
    };
  } catch {
    return null;
  }
}

function preferredFromUrl() {
  const hashPreferred = preferredFromHash();
  if (hashPreferred) return hashPreferred;

  const params = new URLSearchParams(window.location.search);
  return {
    permitType: params.get("permitType"),
    workClass: params.get("workClass"),
  };
}

function updateOptionUrl() {
  const permitType = elements.permitType.value;
  const workClass = elements.workClass.value;
  if (!permitType || !workClass) return;

  const url = new URL(window.location.href);
  url.searchParams.delete("permitType");
  url.searchParams.delete("workClass");
  url.hash = optionHash(permitType, workClass);
  window.history.replaceState(null, "", url);
}

async function initialize() {
  try {
    const response = await fetch(
      DATA_ROOT + "manifest.json",
      { cache: "no-store" },
    );
    if (!response.ok) {
      throw new Error("Could not load fee information. Please refresh the page and try again.");
    }
    state.manifest = await response.json();
    renderStakeholderPreview();

    populatePermitTypes();
    const preferred = preferredFromUrl();
    if (
      preferred.permitType
      && Array.from(elements.permitType.options)
        .some((item) => item.value === preferred.permitType)
    ) {
      elements.permitType.value = preferred.permitType;
    }
    populateWorkClasses(preferred.workClass);
    updateOptionUrl();
    await loadOption();
  } catch (error) {
    showError(error);
  }
}

elements.permitType.addEventListener("change", async () => {
  try {
    populateWorkClasses();
    updateOptionUrl();
    await loadOption();
  } catch (error) {
    showError(error);
  }
});

elements.workClass.addEventListener("change", async () => {
  try {
    updateOptionUrl();
    await loadOption();
  } catch (error) {
    showError(error);
  }
});

elements.expandAllGroups?.addEventListener("click", () => {
  setAllControlGroupsOpen(true);
});

elements.collapseAllGroups?.addEventListener("click", () => {
  setAllControlGroupsOpen(false);
});

elements.showFrequency?.addEventListener(
  "change",
  updateFrequencyVisibility,
);

elements.showRelatedPermits?.addEventListener(
  "change",
  updateRelatedPermitVisibility,
);

elements.calculate.addEventListener("click", () => {
  hideError();
  try {
    if (!state.optionDocument) {
      throw new Error("Select a permit type and work class before calculating an estimate.");
    }
    const values = rawValues();
    const runtime = evaluateOption(
      state.optionDocument,
      values,
      {
        inactiveFeeIds: deriveInactiveScenarioFeeIds(
          state.uiSchema,
          values,
        ),
      },
    );
    renderReport(runtime.report, values);
    elements.results.focus();
  } catch (error) {
    showError(error);
  }
});

initialize();
