# Sprint 3.4 — Human-in-the-Loop Interrupt/Resume Design

## Overview

This document describes the design for true LangGraph interrupt/resume functionality (FR-17), replacing the Sprint 2 terminal `awaiting_approval` write pattern with a pause-and-resume workflow that maintains audit traceability and supports crash recovery.

## Problem Statement

Sprint 2's `act.py` wrote `awaiting_approval` to ServiceNow and terminated the graph. This had three issues:

1. **Premature write**: The incident state changed before the human decision was known
2. **No true pause**: The graph terminated; resumption required a new execution
3. **Crash vulnerability**: A worker crash between decision and write could cause duplication or loss

## Solution: LangGraph Interrupt

### Interrupt Points

We interrupt at three outcomes (FR-17):

```python
INTERRUPT_OUTCOMES = frozenset(
    {
        Outcome.ESCALATED_HIGH_RISK,
        Outcome.ESCALATED_BLOCKED,
        Outcome.ESCALATED_LOW_CONFIDENCE,
    }
)
```

**Why `ESCALATED_NO_EVIDENCE` is excluded**: A missing retrieval result is a data issue, not a gate verdict. The agent cannot proceed without evidence, so it escalates directly without requiring human approval. The incident remains `awaiting_approval` in ServiceNow, but the graph does not pause.

### Interrupt Payload (NFR-07)

When `act.py` hits an interrupt outcome, it:

1. Builds a structured payload containing:
   - Incident snapshot (sys_id, number, priority, description)
   - Gate verdict and outcome
   - Planned action (the work note that would be written)
   - Suggestion, confidence, classification
   - Risk assessment and retrieval results
   - All gate verdicts (verification, safety, confidence)
   - Execution metadata (execution_id, correlation_id)

2. Calls `render_brief()` to synthesize a human-readable brief

3. Persists the payload via `audit_store.save_interrupt(execution_id, payload)`

4. Calls `interrupt(payload)` from LangGraph

The payload is stored both in LangGraph's checkpoint (native) and in `workflow_state` (audit copy) so GET /approvals can read it without reconstituting a graph.

### Approval Brief Agent

Located in `src/agent/approval_brief.py`, this agent:

- Takes the interrupt payload as input
- Synthesizes a structured brief: incident summary, gate, planned action, judgment required
- **Never steers routing** — it's descriptive only
- Degrades gracefully if the LLM fails (structured fallback from raw payload)
- Caches the brief in the payload to avoid re-calling on repeated GETs

```python
def render_brief(payload: dict[str, Any], deps: AgentDependencies) -> dict[str, Any]:
    cached = payload.get("brief")
    if isinstance(cached, dict) and cached.get("incident_summary"):
        return cached
    try:
        parsed = deps.llm.structured(...)
        return parsed.model_dump(mode="json") | {"degraded": False}
    except Exception as exc:
        return _fallback(payload, reason=type(exc).__name__)
```

### Resume via Approvals API

`GET /api/v1/approvals/pending/{execution_id}` returns the parked execution's
payload — `status`, `brief`, `facts` and `decision: null` — so a reviewer can read
the brief *before* deciding. It reads the interrupt payload from the audit store and
never touches the graph.

`POST /api/v1/approvals/{id}/decide` (where `id` is the execution_id):

1. Rejects a decision on an execution that already has an approval row (409 — the
   decision is immutable once recorded)
2. Reads the interrupt payload from `audit_store.get_interrupt(execution_id)`
3. Builds a decision dict: `{"decision": "approved"|"rejected", "decided_by": "...", "reason": "..."}`
4. Calls `resume_incident_graph(execution_id, decision, correlation_id)` — **the
   graph is resumed first**, so a decision that could not be applied is never
   recorded as applied
5. Records the approval decision in the `approvals` table, closes the execution
   (`status`, `ended_at`, `termination_cause`) and returns the resumed result with
   `brief` and `facts` fields

If no interrupt is stored, the endpoint keeps the Sprint 2.1 stub contract (it
answers `200` with the decision echoed) and does not call the runtime.

The resume flow uses LangGraph's `Command(resume=decision)`:

```python
def run_graph(..., resume: dict[str, Any] | None = None):
    if resume is not None:
        payload = Command(resume=resume)
        resumed = True
    final = graph.invoke(payload, config)
```

### Human Decision Application

In `act.py`, after `interrupt()` returns with the decision:

```python
if outcome in INTERRUPT_OUTCOMES:
    payload = interrupt_payload(state, output, outcome)
    payload["brief"] = render_brief(payload, deps)
    deps.audit.save_interrupt(execution_id, payload)
    decision = _request_human_decision(payload)  # This calls interrupt()
    output = _apply_human_decision(output, decision)
```

`_apply_human_decision()`:
- `approved`: returns the original `output` unchanged (write proceeds)
- `rejected`: clears the suggestion, sets `approval_required=True`, updates summary to record refusal

### Audit Trail

Three audit records are written:

1. **Interrupt record** (`hitl.interrupt` node in `workflow_state`):
   - Saved at pause time via `audit_store.save_interrupt()`
   - Contains the full interrupt payload with brief
   - Status: `awaiting_approval`

2. **Write receipt** (`servicenow.write` node in `workflow_state`):
   - Saved after successful ServiceNow write via `audit_store.save_receipt()`
   - Contains `output`, `lifecycle`, `incident_sys_id`
   - Status: `succeeded`
   - Lifecycle field distinguishes:
     - `"direct"`: normal run without interrupt
     - `"interrupt_resume"`: resumed after interrupt

3. **Parked node row** (`act` node in `workflow_state`, status `awaiting_approval`):
   - `WorkflowStateSaver.put()` writes one row per node the graph *completed*, and a
     node parked in `interrupt()` never returns — so without this the authoritative
     history would stop at `determine_risk` and `Execution.node_reached` would name
     the wrong node
   - `run_graph()` calls `checkpointer.record_pause(config, node)` on every pause
     (backends without rows — the in-memory saver — ignore it); the row carries the
     previous node's checkpoint with an id of its own and no new channel values, so
     state reconstruction is unchanged
   - When the graph resumes and `act` completes, `put()` replaces that row through
     the usual `(execution_id, node_name, attempt)` conflict rule, so the history
     ends in the node's real final status (`blocked` for an escalation)
   - `record_pause` is idempotent: a redelivery that parks the same node under the
     same attempt returns without writing a second row

## Edge Cases

### Double Resume Protection

If a thread is already completed (no next node in checkpoint), `run_graph()` returns the cached output without re-running:

```python
if graph.checkpointer is not None:
    snapshot = graph.get_state(config)
    if snapshot.values and not snapshot.next:
        return _result(snapshot.values, resumed=True)
```

### Invalid Transitions

The approvals router validates:

- A decision on an execution that already has an approval row returns **409** —
  two contradictory decisions cannot both be stored for one execution
- A decision that could not be applied (the graph failed to resume) is **not**
  recorded, so the caller may retry
- An id with no execution and no interrupt falls through to the Sprint 2.1 stub
  response (`200`, decision echoed, nothing saved). That is the pre-existing
  contract, not a new one: returning 404 there is **#147**, owned by Mohamed and
  Ahmed Tamer, and is deliberately left to that fix

### Unit Test Direct Calls

When tests call `act()` directly (no compiled graph), `interrupt()` raises `RuntimeError`. We catch this and return a mock decision:

```python
def _request_human_decision(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        value = interrupt(payload)
    except RuntimeError:
        return {"decision": "approved", "decided_by": "direct-node-call", "source": "no_graph"}
```

## Testing

### Unit Tests (`tests/test_interrupt_resume.py`)

- `test_interrupt_at_high_risk`: Verifies interrupt at high-risk outcome
- `test_interrupt_at_blocked_gate`: Verifies interrupt at safety/verification failure
- `test_interrupt_at_low_confidence`: Verifies interrupt at low confidence
- `test_resume_with_approval`: Verifies resume completes the write
- `test_resume_with_rejection`: Verifies resume skips write on rejection
- `test_no_interrupt_on_suggested`: Verifies suggested outcome writes directly
- `test_double_resume_protection`: Verifies completed thread returns cached result

### Approval Brief Tests (`tests/test_approval_brief.py`)

- `test_fallback_basic`: Verifies fallback builds structured brief
- `test_fallback_missing_fields`: Verifies graceful handling of missing fields
- `test_render_brief_uses_cache`: Verifies cached brief avoids LLM call
- `test_render_brief_calls_llm`: Verifies LLM is called without cache
- `test_render_brief_degrades_on_llm_failure`: Verifies degradation on LLM failure
- `test_render_brief_large_payload_truncation`: Verifies payload truncation

### Approvals API Tests (`tests/test_approvals_resume.py`)

- `test_decide_resumes_the_parked_execution`: Parks a P1 in `act`, POSTs a decision
  and asserts the very thread that paused is resumed, the ServiceNow write happens
  only after the decision, and the approval row is recorded against that execution
- `test_decide_without_a_pause_keeps_the_stub_contract`: No interrupt stored → the
  Sprint 2.1 stub still answers and nothing is resumed
- `test_pending_endpoint_returns_the_brief_before_a_decision`: `GET .../pending/{id}`
  returns `brief` and `facts` while the ServiceNow backend stays untouched

### PostgreSQL Integration Tests (`tests/test_checkpointer.py`, `-m integration`)

- `test_high_risk_path_rows`: Against real PostgreSQL — the graph parks with no
  ServiceNow write, `workflow_state` ends in an `act` row of status
  `awaiting_approval`, and resuming replaces that row with the completed one while
  performing exactly one write

## Dependencies

- LangGraph `interrupt()` and `Command(resume=...)`
- Postgres `workflow_state` table (S2.2) for audit copy
- S2.5 LangGraph state machine (eleven-node graph)
- S2.2 checkpointer (Postgres-backed)

## Success Criteria

- Incidents requiring review suspend without premature ServiceNow writes
- Structured brief backed by raw audit data is surfaced via GET /approvals
- Resume via POST /api/v1/approvals/{id}/decide completes on the same Langfuse trace
- Audit trail distinguishes interrupt-resume from direct execution
