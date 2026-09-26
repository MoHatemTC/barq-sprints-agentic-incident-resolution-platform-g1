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

**Why `ESCALATED_NO_EVIDENCE` stays a terminal write.** The three FR-17 interrupt
outcomes are *gate verdicts* — the graph reached a decision about an action it could
otherwise have taken, and a person should decide that action. "No evidence" is not a
verdict about an action; it is the absence of the material any action would rest on. There
is nothing to approve: no draft was produced, so there is no proposed write for a human to
authorise. Pausing would put a decision in front of an operator about an execution with
no candidate outcome — an empty approval, which is worse than an escalation because it
looks like a choice.

So `ESCALATED_NO_EVIDENCE` keeps Sprint 2's terminal behaviour: `act` writes the
escalation record (`ai_processing_state = awaiting_approval`, `ai_human_review_required`,
the classification and a work note naming what was searched and the best score found) and
returns. The incident is still parked for a person, and the work note still says the run
was escalated rather than that it failed. What it does not do is create an `approvals` row
or a resumable thread, because there is no checkpointed decision to resume into — the
`interrupt()` payload would describe an action that does not exist. Manual §11.4 lists this
as its own outcome, *"Escalated — no evidence"*, distinct from the three that carry a draft.

This is a deliberate difference from the other three, and it is the reason
`ESCALATED_NO_EVIDENCE` is absent from `INTERRUPT_OUTCOMES` in `act.py` while the other
three are present.

**Resume routing reads the decision payload only.** The Approval Brief Agent's output is
stored in the same persisted payload as the interrupt facts, so it is worth being explicit
that routing never consults it: `_apply_human_decision` reads `decision`, `decided_by` and
`reason` from the value `interrupt()` returns, and nothing else. A brief cannot approve a
run, cannot reject one, and cannot change the write that follows. If it could, a model
call would be steering whether ServiceNow is written.
`tests/test_interrupt_resume.py::test_resume_routing_ignores_the_approval_brief_entirely`
proves it by storing a brief that instructs the opposite of the operator's decision and
asserting the operator's decision is what takes effect.

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

If no interrupt is stored, the endpoint still requires a paused execution: the path id
is looked up as an approval id and then as an execution id, an execution that is not in
`awaiting_approval` is refused with **409**, and an id that resolves to neither is **404**
(#147, closed). There is no stub that answers `200` with a decision nothing stored, and
the runtime is only called when there is a real interrupt to resume.

The resume flow uses LangGraph's `Command(resume=decision)`:

```python
def run_graph(..., resume: dict[str, Any] | None = None):
    if resume is not None:
        payload = Command(resume=resume)
        resumed = True
    final = graph.invoke(payload, config)
```

### 404 for an unresolvable id (#147, closed)

The Sprint 2.1 contract answered `200` with the decision echoed and stored nothing. That
is gone: `decide_approval` raises `ResourceNotFoundError` when the id is neither an
approval nor an execution, and its own docstring says so — *"there is no stub that reports
a saved decision nothing stored (#147)"*. `tests/test_approvals_resume.py::test_decide_for_an_unknown_id_is_404`
and `tests/test_approvals.py::test_decide_approval_unknown_id_returns_404` pin it.

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

## Running the live demo

`scripts/demo_s34_hitl_live.py` runs the whole mechanism against the real stack — real
ServiceNow instance with the OAuth integration user, real PostgreSQL, Redis and Celery,
real Qdrant hybrid retrieval, real Gemini through the LiteLLM proxy, real Langfuse
tracing. Nothing is faked. Every phase prints `PASS`/`FAIL` and the script exits non-zero
if any phase fails, so it works as a gate and not only as a transcript.
[`docs/demo_runbook.md`](demo_runbook.md) is the step-by-step runbook (prerequisites,
instance selection, credentials, troubleshooting); this section is the design-relevant
summary.

```bash
docker compose up -d postgres redis qdrant
uv run uvicorn app.main:app --host 127.0.0.1 --port 8099
uv run celery -A app.workers.celery_app worker \
  --queues=barq:incident:events --loglevel=INFO --pool=threads --concurrency=1
uv run python scripts/demo_s34_hitl_live.py --all
```

### macOS: the Celery pool must be threads

`--pool=threads --concurrency=1` is required on macOS and only on macOS. With the default
prefork pool every task dies instantly with
`ValueError: not enough values to unpack (expected 3, got 0)`. It is not an application
bug: `celery/app/trace.py` populates a module-level `_localized` list during worker
startup and reads it in the child process, and macOS defaults `multiprocessing` to
`spawn`, so the child starts with an empty list. Linux and the Docker/EC2 deployment use
`fork` and are unaffected. Recognise it by that exact message: it means the pool, not
the graph.

### What the script proves

| Phase | Proves | Requirement |
|---|---|---|
| unauthenticated call → 401 | the caller is authenticated before anything else | FR-07 |
| 202 in ~15 ms | no model on the request thread | FR-08, NFR-01 |
| replay flagged idempotent | one event, one execution | FR-09 |
| parked at `awaiting_approval` | a real LangGraph interrupt, not a terminal write | FR-17 |
| incident untouched while parked | no premature ServiceNow write | FR-17, NFR-05 |
| pending approval carries `facts` | the raw audit payload rode the checkpoint | NFR-07 |
| brief present before any decision | the brief agent is descriptive only | Approval Brief Agent |
| `decided_by` from the token | the body cannot write the audit identity | #148 |
| write lands only after the decision | the write is authorised by the human | NFR-05 |
| `interrupt_resume:…` termination cause | audit separates resume from crash-recovery | Audit Trail |
| second decision → 409 | approvals are immutable | Audit Trail, Edge Cases |

A high-risk incident (`--priority 1`) escalates at `determine_risk` *before* retrieval, so
the graph interrupts with nothing written at all — not even the state field. A low/medium
incident runs all eleven nodes straight through and no approval is raised. `--keep-parked`
stops at the pause and prints the exact `curl` for the decide call, which is the run to
use when the approval screen is being shown to a room.

### The transcript

`docs/evidence/s34_hitl_demo.json` is the recorded run, with the before/at-park/after
field snapshots per incident, the brief, the decision, the final status and the node
trace. Its two runs are the two paths above: a P1 that parks at `determine_risk` and
resumes with termination cause `interrupt_resume:escalated_high_risk`, and a priority 3
that reaches `act` having visited all eleven nodes. Read the node list with the caveat
from [`sprint3_graph_design.md`](sprint3_graph_design.md) §1 in mind: `safety_check:
succeeded` means the node ran, not that anything was checked.

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
- An id that resolves to neither an approval nor an execution is **404**, and nothing is
  stored. This closed #147: the Sprint 2.1 stub answered `200` with the decision echoed
  and saved nothing, so a caller could believe a decision had been recorded when no row
  existed. The router's docstring carries the same statement — *"there is no stub that
  reports a saved decision nothing stored (#147)"*
- An execution that exists but is not `awaiting_approval` is **409**. Without this the
  route could write an immutable approval against a run that already succeeded and was
  written back to ServiceNow, and the audit would assert that a human approved a run no
  human was ever asked about

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
- `test_decide_for_an_unknown_id_is_404`: an id that resolves to neither an approval nor
  an execution is 404 and nothing is stored — the stub is gone (#147)
- `test_second_decision_on_one_execution_is_refused`: whichever id form the caller uses,
  a second decision on the same execution is 409
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
