"use strict";

const $ = (id) => document.getElementById(id);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
let token = "";
let executionId = "";

function message(text, kind = "") {
  $("message").textContent = text;
  $("message").className = `message ${kind}`;
}

function showBrief(approval) {
  $("review-panel").hidden = false;
  $("approval-status").textContent = approval.decision || approval.status;
  const brief = approval.brief || {};
  const details = $("brief-details");
  details.replaceChildren();
  for (const [name, value] of Object.entries(brief)) {
    const term = document.createElement("dt");
    term.textContent = name.replaceAll("_", " ");
    const description = document.createElement("dd");
    description.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    details.append(term, description);
  }
  $("raw-facts").textContent = JSON.stringify(approval.facts || {}, null, 2);
  $("decision-panel").hidden = approval.decision !== null;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}`, ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail || body.message || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

async function loadBrief(id) {
  if (!uuid.test(id)) { message("Enter a valid execution ID.", "error"); return; }
  const entered = $("operator-token").value.trim();
  if (entered) {
    token = entered.replace(/^Bearer\s+/i, "");
    $("operator-token").value = "";
  }
  if (!token) { message("Paste an operator bearer token.", "error"); return; }
  $("load-button").disabled = true;
  message("Loading approval brief…");
  try {
    const approval = await api(`/api/v1/approvals/pending/${encodeURIComponent(id)}`);
    executionId = id;
    showBrief(approval);
    if (approval.decision) {
      message(`Decision already recorded: ${approval.decision}.`, "info");
    } else {
      message("Approval brief loaded. Review it before deciding.", "success");
    }
  } catch (error) {
    $("review-panel").hidden = true;
    message(error.message, "error");
  } finally { $("load-button").disabled = false; }
}

$("load-button").addEventListener("click", () => {
  const id = $("execution-id").value.trim();
  loadBrief(id);
});

async function decide(decision) {
  if (!executionId) return;
  $("approve-button").disabled = true;
  $("reject-button").disabled = true;
  message("Resuming the paused run…");
  try {
    const approval = await api(`/api/v1/approvals/${encodeURIComponent(executionId)}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        reason: $("decision-reason").value.trim() || null,
        solution: $("human-solution").value.trim() || null,
      }),
    });
    showBrief(approval);
    message(`Decision recorded: ${approval.decision}. The paused run resumed.`, "success");
  } catch (error) {
    message(error.message, "error");
  } finally {
    $("approve-button").disabled = false;
    $("reject-button").disabled = false;
  }
}

$("approve-button").addEventListener("click", () => decide("approved"));
$("reject-button").addEventListener("click", () => decide("rejected"));

const initialId = new URLSearchParams(window.location.search).get("execution_id");
if (initialId && uuid.test(initialId)) {
  $("execution-id").value = initialId;
}
