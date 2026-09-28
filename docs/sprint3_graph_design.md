# Graph architecture and the risk-ordering design record

The S2.5 brief names this file `sprint3_graph_design.md`. It describes the eleven-node
state machine delivered in Sprint 2 (FR-11) and the decision to determine risk before
retrieval (PRD A-09). Diagram: [`graph_state_diagram.png`](graph_state_diagram.png),
generated from the compiled graph by `scripts/render_graph_diagram.py`.

## 1. Nodes

Every node has the same signature, `(state, deps) -> partial state`. It reads only
sections written before it and writes exactly one section.

| # | Node | Reads | Writes | Model? | Side effects |
|---|---|---|---|---|---|
| 1 | `load` | `event` | `incident` | no | ServiceNow read (OAuth, integration user) |
| 2 | `validate` | `event`, `incident` | `eligibility` | no | — |
| 3 | `classify` | `incident` | `classification` | yes | — |
| 4 | `determine_risk` | `incident`, `classification` | `risk` | **no** | — |
| 5 | `retrieve` | `incident`, `classification` | `retrieval` | no (embeddings) | Qdrant search |
| 6 | `diagnose` | `incident`, `classification`, `retrieval` | `diagnosis` | yes | Diagnostic Agent |
| 7 | `generate` | `incident`, `retrieval`, `diagnosis`, `critic_feedback` | `draft`, `revision_count` | yes | Resolution Agent (revises on feedback) |
| 8 | `verify_evidence` | `draft`, `retrieval`, `diagnosis` | `verification`, `critic_feedback` | yes | Critic/Verifier Agent (deterministic + semantic) |
| 9 | `safety_check` | (S3.3, not merged) | `safety` | no | pass-through — `implemented=False` |
| 10 | `confidence_check` | `diagnosis`, `draft` | `confidence` | no | — |
| 11 | `act` | everything | `output` | no | the only ServiceNow write |

**Gate implementations in Sprint 3.**

- `verify_evidence` implements the full **Critic/Verifier Agent** (`implemented=True`):
  1. *Deterministic Python citation validation*: checks `article_id` and `section` against retrieved hits.
  2. *Semantic LLM verification*: evaluates isolated step assertions against cited evidence chunks.
  Produces structured `GateResult` and `CriticFeedback`.
- `safety_check` is an explicit pass-through: it returns
  `GateResult(gate="safety_check", passed=True, implemented=False)` without reading
  `state` or `deps`, so it cannot fail on any input today. The output guardrails
  (schema, action allowlist, secret scan of the draft) are **S3.3 — open, owner
  Tasneem Mohammed**; the edge condition that routes a failed gate to `act` already exists.
- `confidence_check` applies the manual's floor (0.45).

**`generate` output.** It is a numbered procedure. Each step cites its article, version
and section, for example `2. Sign out of the VPN client completely. [KB0001 v2.0
§Resolution]`, followed by a *Sources* line. A step that cites an article that was not
retrieved is dropped and counted, and the count lowers the confidence score. The draft
is capped at the 4000-character `ai_suggestion` field.

## 2. Edge conditions — fully enumerated

Each router is a pure function of one state section. A missing or malformed section
routes to `act`, so the graph fails closed. The table is `agent.edges.EDGE_TABLE`, and
`tests/test_graph.py::test_edge_table_matches_the_compiled_graph` asserts it equals the
compiled graph.

| From | Condition | To |
|---|---|---|
| START | always | `load` |
| `load` | always | `validate` |
| `validate` | `eligibility.eligible` | `classify` |
| `validate` | not eligible, or section missing | `act` |
| `classify` | always | `determine_risk` |
| `determine_risk` | `risk.level ∈ {low, elevated}` | `retrieve` |
| `determine_risk` | `risk.level == high`, or section missing | `act` |
| `retrieve` | `best_relevance ≥ threshold` and hits exist | `diagnose` |
| `retrieve` | below threshold, no hits, no corpus category, or section missing | `act` |
| `diagnose` | at least one *retrieved* article matches the fault | `generate` |
| `diagnose` | none matches, or section missing | `act` |
| `generate` | always | `verify_evidence` |
| `verify_evidence` | `verification.passed` | `safety_check` |
| `verify_evidence` | not `verification.passed` and `revisions < max` | `generate` |
| `verify_evidence` | not `verification.passed` and `revisions >= max`, or missing | `act` |
| `safety_check` | `safety.passed` | `confidence_check` |
| `safety_check` | failed or missing | `act` |
| `confidence_check` | always (`act` applies the result) | `act` |
| `act` | always | END |

### Architectural Justification: Conditional-Edge Routing vs. Command/goto

The task specification calls for a "Command/goto or equivalent routing function" for the multi-agent revision loop. In our architecture, this is implemented using a pure LangGraph conditional edge (`edges.after_verify_evidence`) rather than node-embedded `Command(goto=...)`. This is a deliberate, production-grade choice that satisfies the requirement with several distinct advantages:

1. **Deterministic Separation of Concerns**: Routing decisions are isolated from node execution logic. Node functions (`generate`, `verify_evidence`) remain single-responsibility units that transform input state into validated sections, without being coupled to graph topology or routing destinations.
2. **Isolated Testability Without Runtime Overhead**: Because `after_verify_evidence(state, settings)` is a pure Python function, every routing branch (`safety_check`, `generate`, `act`) is unit-tested exhaustively in isolation without compiling the graph, mocking LangGraph runtime internals, or invoking LLMs (`tests/test_graph.py::test_gate_routers`).
3. **Auditability and Static Introspection**: In LangGraph, `StateGraph.add_conditional_edges()` registers routing transitions explicitly in the compiled graph structure (`compiled.edges`). This enables static validation against `EDGE_TABLE`, automated diagram rendering (`scripts/render_graph_diagram.py`), and checkpoint-safe resume semantics across worker restarts.

`act` derives the outcome from the same recorded sections, in the same order
(`decide_outcome`), so the route taken and the outcome written cannot disagree:

| Outcome | Written to ServiceNow |
|---|---|
| `skipped_ineligible` | nothing |
| `escalated_high_risk` | work note with the risk verdict; review flag |
| `escalated_no_evidence` | work note naming what was searched and the best match; review flag |
| `escalated_blocked` | work note naming the gate; review flag |
| `escalated_low_confidence` | work note with score vs floor; confidence; review flag |
| `suggested` | draft, confidence, classification, model, version; work note; review flag |
| `skipped_human_lock` | nothing (an analyst locked the incident before the write) |

## 3. Risk before retrieval — the design record

**Decision.** `determine_risk` runs after `classify` and before `retrieve`. A HIGH
verdict routes straight to `act`: no search, no diagnosis, no generation. It decides
from the incident record and the category label only.

**Rules** (`agent/policy.py`, all deterministic):

| Verdict | When | Source |
|---|---|---|
| HIGH | effective priority ∈ `AGENT_RISK_PRIORITIES` (default `[1]`) | manual §11.7 `risk_p` |
| HIGH | priority unknown | fail closed |
| HIGH | classification `security` | manual §6: suspected compromise goes to Security |
| ELEVATED | service is Tier 1 (order-processing, identity, sap-erp) | manual §11.1: no action on Tier 1 without approval |
| ELEVATED | MFA reset or lost/replaced authenticator | manual §6 (KB0006): always requires approval |
| LOW | otherwise | — |

**Effective priority** is the more severe of two values: the recorded priority, and the
priority derived from impact × urgency in the manual's §3.3 matrix. "Priority is derived,
not chosen," so a hand-lowered priority cannot lower the verdict.

**ELEVATED** may still draft, but `ai_processing_state` becomes `awaiting_approval` and
the work note says approval is required. The Sprint 4 interrupt hooks onto the same
`risk.approval_required` flag.

### Why this order — on cost

- A HIGH incident never needs a draft: the manual says a person handles it. Running
  retrieval, diagnosis and generation first would spend two of the three model calls,
  plus embedding and search, on output that is thrown away. Risk costs one short
  classification call, and for a P1 even that could be skipped.
- The manual states the intent directly: "Priority 1 leaves the automated path before
  any search runs, so nothing is spent on an incident that was never eligible" (§11.7).
- NFR-02 budgets 90 s p95 per execution. Escalating a P1 quickly is where latency
  matters most, and this path is the shortest one: `load → validate → classify →
  determine_risk → act`.

### Why this order — on safety

- Nothing the model writes can reach a high-risk incident's form, because no draft
  exists. The manual's INC0010052 (P1 on order-processing) shows the risk: the
  knowledge base holds a *correct* article, KB0010 v2, whose first step is "do not
  restart". The retired v1 said the opposite. On a revenue-bearing outage the safe path
  is a person on the bridge, not a plausible procedure.
- A retrieval result cannot argue the verdict down. If risk were assessed *after*
  retrieval, a strong match could make a P1 look "routine", which is exactly the
  confidence the pilot must not borrow.
- It keeps the untrusted surface small. Retrieved text and model output are the
  prompt-injection vectors (manual §11.6); the verdict never sees either.

### Why this order — on decision independence

- The verdict depends only on data that exists before the agent runs: priority,
  impact, urgency, service and category. It is reproducible from the incident record
  alone, auditable from the `determine_risk` row in `workflow_state`, and unaffected by
  corpus changes, embedding-model changes or model non-determinism.
- The only model input is the category label, and it can only *raise* risk
  (`security`). No label lowers a verdict that the record already makes HIGH.
- Tests pin this in three places:
  - `test_nodes.py::TestDetermineRisk::test_reads_no_evidence`: the node ignores
    retrieval state.
  - `test_graph.py::test_risk_is_determined_before_any_retrieval`: with
    `determine_risk` removed, `retrieve` and `generate` are unreachable from START.
  - `test_graph.py::test_high_risk_escalates_at_determine_risk_without_retrieval_or_generation`:
    the retriever is never called and the only model call is `classify`.

### Alternatives considered

- **Risk after retrieval, using evidence** — rejected on all three grounds above.
- **Risk before classification** — would make `security` invisible to the verdict.
  Classification is a single cheap call, and for HIGH-by-priority incidents it is the
  only one made.
- **Model-judged risk** — rejected: not reproducible, and it puts the injection surface
  in front of the gate.

## 4. State and persistence

- **State:** `AgentState` is a `TypedDict` of JSON-only sections, each validated by a
  Pydantic model at the node boundary.
- **Checkpoints:** a checkpoint is written after every node into `workflow_state`
  (see `sprint2_tracing_and_agent.md` §5). The execution id is the LangGraph thread id.
- **Retries:** a retry with a higher attempt number resumes after the last completed
  node, and a finished thread is never run again.
- **Path:** `path` accumulates the visited nodes, which the tests use to assert every
  route.
- **Execution summary:** on success the worker writes the graph's outcome onto the
  `executions` row — `termination_cause` takes the outcome (for example
  `escalated_high_risk`), with `node_reached` and `agent_version`. `workflow_state`
  remains the authoritative per-node history; this row is the one-line answer to "what
  did the agent decide about this incident", which previously read `completed` for every
  run including a high-risk escalation. No schema change: S2.2 already declared these
  columns.

### Fail-closed reading of the state

Each routing decision is taken from a state section that may be absent after a
malformed or partial checkpoint, so `decide_outcome` maps every missing section to an
escalation. `compose` must then build that escalation from the *same* state — reading a
section it has just been told is missing turns the fail-closed route into a `KeyError`,
which leaves the incident with no work note at all. `test_compose_survives_the_state_
that_selected_the_outcome` drives every single-section-missing state through both.

The service tier follows the same rule. `get_incident` does not request display values,
so a populated `business_service` arrives as a bare reference. An unresolved reference is
recorded separately from an absent one: absent still allows LOW, unresolved fails closed
to ELEVATED, because a tier that cannot be read cannot be ruled Tier 1 (§11.1).

## 5. Open items for Sprint 4

- **Sprint 3.1 Delivery:** `verify_evidence` is fully implemented as the Critic / Verifier Agent
  with deterministic citation checks, LLM-based semantic evidence verification against retrieved text,
  and structured feedback emission driving the multi-agent revision loop.
- **S3.3 (guardrails — open, owner Tasneem Mohammed):** implement `safety_check` (output
  schema, action allowlist, secret scan of the draft), plus input screening for embedded
  instructions (manual §11.6). Until it lands, `safety_check` passes every draft
  unconditionally.
- **Approval interrupt on `risk.approval_required`:** shipped in S3.4 — `interrupt()` in
  `act` plus the approvals API; see [`sprint3_hitl_design.md`](sprint3_hitl_design.md).
- **S2.4 (#110):** `QdrantRetriever` already calls whichever entry point the tree
  carries (`_run_search`); once #110 is on `main`, delete the try/except shim in
  `agent/retrieval.py` and import `hybrid_search` directly. The evidence gate cannot
  simply adopt the reranker's score: `CrossEncoderReranker` writes raw ms-marco logits
  into `hit.score` (values from about −11 to +11, negative for a poor match), which are
  not comparable to the calibrated 0–1 §11.7 threshold. Either normalise them or keep
  the separate dense cosine, which is what happens today.
- **Write-back idempotency across a hard kill — closed in S3.4.** This used to be listed
  here as an open defect: `act` PATCHes ServiceNow before LangGraph commits its
  checkpoint, so a SIGKILL (or Celery's `soft_time_limit`) in that window replayed the
  PATCH on resume and, because `work_notes` is append-only, duplicated the note. `act` now
  carries a three-phase intent receipt instead — `writing` (saved *before* the PATCH, so a
  restart can always tell "in flight" from "never started") → `fields_written` (the PATCH
  returned) → `logged` (the execution-log row landed) → `written` (run closed). `logged`
  and `written` are terminal and short-circuit; `fields_written` skips the PATCH; any other
  non-terminal phase asks ServiceNow through the read-back probe `_write_already_landed`,
  which compares this run's own `ai_processing_start` first and otherwise falls back to
  `work_notes` / `ai_classification` / `ai_suggestion` / `ai_agent_version` /
  `ai_model_name`, returning `False` (write again) on any read failure. Coverage:
  `tests/test_crash_recovery.py::test_kill_before_the_write_records_an_in_flight_receipt_and_the_retry_writes_once`,
  `::test_kill_after_the_write_never_duplicates_it` (mutation-checked: it fails if the
  read-back probe is removed) and
  `::test_kill_between_the_execution_log_and_the_final_receipt_does_not_replay_the_log`.
- **Write-back is on by default, and one window is still open.** `AGENT_WRITE_BACK_ENABLED`
  defaults to `True` (`agent/config.py:132`) and `.env.example` sets it true, so the PATCH
  really is issued in every run these paths cover. The residual: a kill *after* the
  execution-log row lands but *before* the receipt reaches `logged` still replays the log
  row. Closing it needs a read-back probe for the execution-log table, and `execution_id`
  is deliberately not unique there, so row existence cannot distinguish this attempt from
  an earlier one. The consequence is one extra audit row; the authoritative record of a
  run is `executions` + `workflow_state`. Design detail:
  [`sprint3_recovery_design.md`](sprint3_recovery_design.md).
- **Corpus:** decide on the `restricted` tags that exclude every hardware article (see
  `sprint2_tracing_and_agent.md` §4).
- **"Ask" outcome:** the W0.3 set expects vague reports to be answered with a request
  for detail. Today they escalate as low confidence.
- **Failed executions on the incident:** when the worker dead-letters an event
  (retries exhausted or a terminal error), nothing is written back, so the incident stays
  `pending`. Manual §11.4 expects "AI Status shows failed, with a reason". The fix is a
  failure write-back (`ai_processing_state=failed` + `ai_failure_reason`) in the worker's
  failure path. That path is S2.3's code, so the change needs its owner.
- **Service tiers:** `load` reads the service from `business_service`, and only the
  display value names the tier. Reading with `sysparm_display_value` is a small S1.5
  client change. Without it, the Tier 1 rule relies on priority alone.
