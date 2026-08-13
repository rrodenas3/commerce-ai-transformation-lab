"use strict";

const app = document.querySelector("[data-app-state]");
const loading = document.querySelector("#loading-state");
const errorState = document.querySelector("#error-state");
const errorDetail = document.querySelector("#error-detail");
const content = document.querySelector("#evidence-content");
const caseIndex = document.querySelector("#case-index");
const outcomeFilter = document.querySelector("#outcome-filter");
const caseSearch = document.querySelector("#case-search");
const selectionStatus = document.querySelector("#selection-status");
const emptyState = document.querySelector("#case-empty-state");
const visibleCount = document.querySelector("#visible-count");
const verifiedDecision = document.querySelector("[data-verified-decision]");

const BIDI_OR_CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u202a-\u202e\u2066-\u2069]/u;
const SAFE_PUBLIC_PATH = /^(?:data|docs|journey)\/[a-zA-Z0-9._/-]+$/u;
const OUTCOME_COPY = {
  blocked: "Evidence or policy stopped progress before action.",
  control_stop: "A governed control intentionally stopped the route.",
  pending: "Execution did not earn a verified postcondition.",
  verified_no_new_action: "The verified state required no new action.",
  verified_remedy: "A synthetic operational remedy milestone was verified.",
  verified_wait: "The verified state supported a wait route."
};
const SHA256 = /^[0-9a-f]{64}$/u;

let pack;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function canonicalJson(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    assert(Number.isFinite(value), "projection contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  assert(value && typeof value === "object", "projection contains an unsupported value");
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(`${canonicalJson(value)}\n`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function safeText(value, field = "text") {
  const text = String(value);
  assert(!BIDI_OR_CONTROL.test(text), `${field} contains unsafe control characters`);
  assert(text.length <= 10_000, `${field} is unexpectedly large`);
  return text;
}

function safePublicPath(path) {
  const value = safeText(path, "evidence path");
  assert(SAFE_PUBLIC_PATH.test(value), "evidence path is not public and local");
  assert(!value.includes("..") && !value.includes("//") && !value.toLowerCase().includes("private"), "evidence path is unsafe");
  return value;
}

function titleCase(value) {
  return safeText(value).replaceAll("_", " ").replace(/\b\w/gu, (letter) => letter.toUpperCase());
}

function renderValue(value) {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null) return "Not available";
  if (Array.isArray(value)) return value.map((item) => safeText(item)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return safeText(value);
}

function formatMetric(metric) {
  if (metric.unit === "basis_points") return `${(Number(metric.value) / 100).toFixed(2)}%`;
  return Number(metric.value).toLocaleString("en-GB");
}

function metric(metricId) {
  const found = pack.metrics.find((item) => item.metric_id === metricId);
  assert(found, `metric is missing: ${metricId}`);
  return found;
}

async function validatePack(value) {
  assert(value && typeof value === "object" && !Array.isArray(value), "projection is not an object");
  const pendingValues = [value];
  while (pendingValues.length > 0) {
    const candidate = pendingValues.pop();
    if (typeof candidate === "string") {
      safeText(candidate, "projection text");
    } else if (Array.isArray(candidate)) {
      pendingValues.push(...candidate);
    } else if (candidate && typeof candidate === "object") {
      pendingValues.push(...Object.values(candidate));
    }
  }
  assert(value.schema_version === "stage2-public-evidence-pack/v2", "projection schema is unsupported");
  assert(value.pack_id === "S2-PUBLIC-EVIDENCE-20260812-V2", "projection identity is unsupported");
  assert(SHA256.test(value.pack_digest), "projection digest is invalid");
  const digestMaterial = Object.fromEntries(Object.entries(value).filter(([key]) => key !== "pack_digest"));
  assert(await sha256(digestMaterial) === value.pack_digest, "projection digest does not match its content");
  assert(value.public_safe === true && value.read_only === true, "projection is not public-safe and read-only");
  assert(value.decision?.recommendation === "pause", "decision is not the evidence-bound PAUSE outcome");
  assert(value.decision?.authorises_company_pilot === false, "projection incorrectly authorises a company pilot");
  assert(value.maturity?.supported_ceiling === "local-mvp", "maturity ceiling is not local MVP");
  assert(value.maturity?.evaluation_status === "creator-evaluated", "evaluation status is unsupported");
  assert(value.maturity?.publication_status === "not_authorised_until_valid_signed_release_tag", "publication status is unsupported");
  const boundary = value.evidence_boundary || {};
  assert(boundary.synthetic === true, "synthetic evidence boundary is missing");
  assert(boundary.human_evidence === "not_observed", "human evidence boundary is inflated");
  assert(boundary.independent_validation === false, "independent validation is incorrectly claimed");
  assert(boundary.live_customer_outcome === "not_observed", "live customer outcome is incorrectly claimed");
  assert(boundary.realised_value === "not_observed", "realised value is incorrectly claimed");
  assert(boundary.simulated_actions === true && boundary.simulated_approvals === true, "simulated authority boundary is missing");
  assert(boundary.unsent_communications === true, "communication boundary is unsupported");
  assert(Array.isArray(value.cases) && value.cases.length === 36, "case denominator is not 36");
  assert(new Set(value.cases.map((item) => item.case_id)).size === 36, "case identities are not unique");
  const supportedBuckets = ["blocked", "control_stop", "escalated", "excluded", "failed", "pending", "verified_no_new_action", "verified_remedy", "verified_wait"];
  const outcomeCounts = value.outcomes?.counts || {};
  assert(Object.keys(outcomeCounts).sort().join("|") === [...supportedBuckets].sort().join("|"), "outcome buckets are unsupported");
  const observedCounts = Object.fromEntries(supportedBuckets.map((bucket) => [bucket, 0]));
  for (const item of value.cases) {
    assert(supportedBuckets.includes(item.outcome_bucket), "case outcome bucket is unsupported");
    observedCounts[item.outcome_bucket] += 1;
  }
  assert(supportedBuckets.every((bucket) => Number(outcomeCounts[bucket]) === observedCounts[bucket]), "outcome counts contradict case records");
  assert(Object.values(observedCounts).reduce((sum, item) => sum + item, 0) === 36 && value.outcomes?.denominator === 36, "outcome denominator does not conserve 36 cases");
  const requiredMetrics = ["recommendation_correctness", "safe_routing", "approval_validity", "execution_commit", "verified_remedy", "recovery_success", "closure_integrity", "unsupported_communication_facts", "provider_cost_unknown", "provider_latency_unknown"];
  assert(Array.isArray(value.metrics) && value.metrics.length === requiredMetrics.length, "metric inventory is incomplete");
  assert(requiredMetrics.every((metricId) => value.metrics.some((item) => item.metric_id === metricId)), "required metric is missing");
  for (const item of value.cases) {
    safeText(item.case_id, "case ID");
    safeText(item.outcome_bucket, "outcome bucket");
    assert(Array.isArray(item.evidence_chain) && item.evidence_chain.length >= 3, "case evidence chain is incomplete");
    for (const step of item.evidence_chain) safePublicPath(step.evidence_ref.path);
  }
  return value;
}

function element(name, options = {}) {
  const node = document.createElement(name);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = safeText(options.text);
  return node;
}

function renderSummary() {
  document.querySelector("[data-testid='decision-word']").textContent = pack.decision.recommendation.toUpperCase();
  document.querySelector("#decision-heading").textContent = "Do not start a company pilot.";
  document.querySelector("#decision-rationale").textContent = "All preregistered technical gates passed, but execution commitment, telemetry, economics, and human evidence do not yet support scale.";
  document.querySelector("#source-binding").textContent = `${pack.source_bindings.evaluation_pack_id} / ${pack.source_bindings.evaluation_run_id} / decision pack ${pack.source_bindings.decision_pack_id}`;

  const execution = metric("execution_commit");
  const cost = metric("provider_cost_unknown");
  const latency = metric("provider_latency_unknown");
  document.querySelector("#execution-commit").textContent = formatMetric(execution);
  document.querySelector("#pending-count").textContent = `${pack.outcomes.counts.pending} pending`;
  document.querySelector("#telemetry-count").textContent = `${cost.value}/${cost.denominator}`;
  document.querySelector("#economics-verdict").textContent = pack.economics.status;
  document.querySelector("#economics-copy").textContent = `Scenario classes are ${pack.economics.scenario_class_stable ? "stable" : "not stable"}; economics therefore ${pack.economics.supports_scale_next_experiment ? "support" : "do not support"} scaling the next experiment. Value remains ${titleCase(pack.economics.value_status)}.`;
  document.querySelector("#enablement-copy").textContent = `${pack.enablement.role_count} role packages define authority, help, appeal, incident, and review responsibilities. Their status is ${titleCase(pack.enablement.status)}.`;
  document.querySelector("#human-evidence").textContent = `Human evidence: ${titleCase(pack.evidence_boundary.human_evidence)}`;
  document.querySelector("#publication-status").textContent = `Publication status: ${titleCase(pack.maturity.publication_status)}.`;
  document.querySelector("#must-not-say").textContent = `Claim boundary — must not say: ${pack.claim_boundary.must_not_say}`;

  const metricsBody = document.querySelector("#metrics-body");
  metricsBody.replaceChildren(...pack.metrics.map((item) => {
    const row = element("tr");
    const heading = element("th", { text: item.label });
    heading.scope = "row";
    row.append(heading, element("td", { text: formatMetric(item) }), element("td", { text: item.denominator }), element("td", { text: titleCase(item.evidence_class) }));
    return row;
  }));

  const outcomesBody = document.querySelector("#outcomes-body");
  outcomesBody.replaceChildren(...Object.entries(pack.outcomes.counts).map(([bucket, count]) => {
    const row = element("tr");
    const heading = element("th", { text: titleCase(bucket) });
    heading.scope = "row";
    row.append(heading, element("td", { text: count }), element("td", { text: OUTCOME_COPY[bucket] || "Preserved in the frozen denominator." }));
    return row;
  }));
  document.querySelector("#outcome-total").textContent = pack.outcomes.denominator;

  const roles = document.querySelector("#roles-list");
  roles.replaceChildren(...pack.enablement.roles.map((role) => {
    const details = element("details");
    const summary = element("summary", { text: role.role_name });
    details.append(summary, element("p", { text: role.authority }), element("p", { text: `Evidence gap: ${role.evidence_gap}` }));
    return details;
  }));

  for (const bucket of Object.keys(pack.outcomes.counts)) {
    const option = element("option", { text: `${titleCase(bucket)} (${pack.outcomes.counts[bucket]})` });
    option.value = bucket;
    outcomeFilter.append(option);
  }
}

function caseButton(item) {
  const listItem = element("li");
  listItem.dataset.caseId = item.case_id;
  listItem.dataset.bucket = item.outcome_bucket;
  listItem.dataset.search = `${item.case_id} ${item.outcome_bucket} ${item.outcome_classification} ${item.recommended_action} ${item.route}`.toLowerCase();
  const button = element("button");
  button.type = "button";
  button.dataset.selectCase = item.case_id;
  button.setAttribute("aria-label", `${item.case_id}: ${titleCase(item.outcome_bucket)}`);
  button.append(element("span", { className: "case-id", text: item.case_id }), element("span", { className: "case-bucket", text: titleCase(item.outcome_bucket) }));
  listItem.append(button);
  return listItem;
}

function renderCaseIndex() {
  caseIndex.replaceChildren(...pack.cases.map(caseButton));
}

function evidenceLink(reference, stage) {
  const path = safePublicPath(reference.path);
  const link = element("a", { text: `Open bound ${titleCase(stage)} source` });
  link.href = `../${path}`;
  link.setAttribute("aria-label", `Open public evidence source for ${titleCase(stage)}`);
  return link;
}

function renderSelectedCase(item) {
  document.querySelector("[data-testid='selected-case-title']").textContent = `${item.case_id} / revision ${item.case_revision}`;
  document.querySelector("[data-testid='selected-case-outcome']").textContent = `${titleCase(item.outcome_bucket)} / ${titleCase(item.outcome_classification)}`;
  document.querySelector("#case-limitation").textContent = item.outcome_bucket === "pending"
    ? "Pending: the simulated action did not produce the required authoritative postcondition. This case remains outside the verified-remedy numerator."
    : "This is synthetic, creator-evaluated and non-independent evidence. Workflow state is not a customer outcome or realised value.";

  const facts = [
    ["Final state", item.final_state],
    ["Recommendation", item.recommended_action],
    ["Authority route", item.route],
    ["Provider attempt", item.provider_terminal_status],
    ["Approval", item.approval_label],
    ["Communication", item.communication_label]
  ];
  const factsList = document.querySelector("#case-facts");
  factsList.replaceChildren(...facts.map(([term, value]) => {
    const wrapper = element("div");
    wrapper.append(element("dt", { text: term }), element("dd", { text: titleCase(value) }));
    return wrapper;
  }));

  const chain = document.querySelector("#case-evidence-chain");
  chain.replaceChildren(...item.evidence_chain.map((step) => {
    const listItem = element("li");
    const body = element("div");
    body.append(
      element("h5", { text: titleCase(step.stage) }),
      element("p", { text: step.summary }),
      element("code", { text: `${step.evidence_label} / ${renderValue(step.value)}` }),
      evidenceLink(step.evidence_ref, step.stage)
    );
    listItem.append(body);
    return listItem;
  }));

  for (const button of caseIndex.querySelectorAll("button")) {
    button.setAttribute("aria-current", button.dataset.selectCase === item.case_id ? "true" : "false");
  }
  const url = new URL(window.location.href);
  url.searchParams.set("case", item.case_id);
  window.history.replaceState({}, "", url);
}

function selectCase(caseId, announce = false) {
  const item = pack.cases.find((candidate) => candidate.case_id === caseId);
  if (!item) {
    selectionStatus.textContent = `Case ${safeText(caseId)} could not be found. Showing the first frozen case instead.`;
    renderSelectedCase(pack.cases[0]);
    return;
  }
  if (announce) selectionStatus.textContent = `${item.case_id} selected. Its evidence chain is shown beside the ledger.`;
  renderSelectedCase(item);
}

function applyFilters() {
  const query = caseSearch.value.trim().toLowerCase();
  const bucket = outcomeFilter.value;
  let count = 0;
  let firstVisibleCaseId = null;
  let selectedCaseVisible = false;
  for (const item of caseIndex.children) {
    const visible = (bucket === "all" || item.dataset.bucket === bucket) && (!query || item.dataset.search.includes(query));
    item.hidden = !visible;
    if (visible) {
      count += 1;
      firstVisibleCaseId ||= item.dataset.caseId;
      if (item.querySelector("button")?.getAttribute("aria-current") === "true") {
        selectedCaseVisible = true;
      }
    }
  }
  visibleCount.textContent = count;
  emptyState.hidden = count !== 0;
  if (count === 0) {
    selectionStatus.textContent = "No cases match the active filters.";
    return;
  }
  if (!selectedCaseVisible) selectCase(firstVisibleCaseId);
  selectionStatus.textContent = `${count} of 36 cases shown. The denominator remains 36.`;
}

function clearFilters() {
  caseSearch.value = "";
  outcomeFilter.value = "all";
  applyFilters();
  caseSearch.focus();
}

function showReady() {
  app.dataset.appState = "ready";
  app.setAttribute("aria-busy", "false");
  document.documentElement.dataset.explorer = "read-only-synthetic-creator-evaluated";
  loading.hidden = true;
  errorState.hidden = true;
  verifiedDecision.hidden = false;
  content.hidden = false;
}

function showError(error) {
  app.dataset.appState = "error";
  app.setAttribute("aria-busy", "false");
  loading.hidden = true;
  verifiedDecision.hidden = true;
  content.hidden = true;
  errorState.hidden = false;
  errorDetail.textContent = `The explorer stopped safely: ${safeText(error.message || "the public projection is invalid")}.`;
}

async function start() {
  try {
    const signal = AbortSignal.timeout(10_000);
    const response = await fetch("data/evidence-pack.json", { cache: "no-store", credentials: "same-origin", signal });
    assert(response.ok, `public projection returned HTTP ${response.status}`);
    pack = await validatePack(await response.json());
    renderSummary();
    renderCaseIndex();
    const requested = new URL(window.location.href).searchParams.get("case");
    selectCase(requested || pack.cases[0].case_id, Boolean(requested));
    showReady();
  } catch (error) {
    showError(error);
  }
}

caseSearch.addEventListener("input", applyFilters);
outcomeFilter.addEventListener("change", applyFilters);
document.querySelector("#clear-filters").addEventListener("click", clearFilters);
document.querySelector("[data-clear-filters]").addEventListener("click", clearFilters);
caseIndex.addEventListener("click", (event) => {
  const button = event.target.closest("[data-select-case]");
  if (button) selectCase(button.dataset.selectCase, true);
});

start();
