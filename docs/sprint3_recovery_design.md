# Sprint 3.4 — Crash-Recovery and Write-Boundary Idempotency Design

## Overview

This document describes the crash-recovery mechanism for write-boundary idempotency (FR-12, NFR-03), ensuring that worker crashes do not cause a duplicated incident write-back, a lost suggestion, or a missing audit trail. One residual window is documented rather than papered over — see Scenario 4.

## Problem Statement

Celery workers can crash at any point during execution. Without recovery protection:

1. **Kill-before-write**: Worker crashes after interrupt but before ServiceNow write → On restart, should write exactly once
2. **Kill-after-write**: Worker crashes after ServiceNow write but before acknowledgment → On restart, should NOT duplicate the write
3. **No audit trail**: Cannot distinguish between intentional interrupt-resume and crash-recovery

## Solution: Audit-Based Idempotency

### Write Receipt

The receipt carries a `phase` alongside the output, and it is saved *before* the call that
can die — not only after the write succeeds:

```python
def _perform_write(state, deps, output, *, resume_phase=None):
    ...
    # intent first: a restarted worker can always tell "in flight" from "never started"
    deps.audit.save_receipt(execution_id, {**base, "phase": "writing", "output": in_flight})
    ...  # ServiceNow PATCH
    deps.audit.save_receipt(execution_id, {**base, "phase": "fields_written", "output": in_flight})
    _write_execution_log(...)
    deps.audit.save_receipt(execution_id, {**base, "phase": "logged", "output": dumped})
    deps.audit.save_receipt(execution_id, {**base, "phase": "written", "output": dumped})
```

The phase set is:

| Phase | Means | Terminal? |
|---|---|---|
| `writing` | intent recorded, the PATCH is in flight or was never sent | no — probe |
| `fields_written` | the incident PATCH returned | no — but skips the PATCH |
| `logged` | the execution-log row landed | yes |
| `written` | the run is closed | yes |
| absent / `None` | saved before phases existed; can only postdate a completed write | yes |

The receipt is stored in `workflow_state` as node `servicenow.write` with status `succeeded`.
`logged` and `written` carry the *final* output; `writing` and `fields_written` carry the
output the attempt was in the middle of writing.

### Receipt Check on Entry

A receipt is **not** by itself proof that the write finished — it may say the write is
still in flight. `act` therefore branches on the phase:

```python
def act(state, deps):
    execution_id = str(state.get("execution_id") or "")
    receipt = deps.audit.get_receipt(execution_id) if execution_id else None
    if receipt and receipt.get("output"):
        phase = receipt.get("phase")
        if phase in (None, "logged", "written"):
            return {"output": receipt["output"]}  # terminal: nothing was left undone
        # died inside the boundary: resume the write it was making, with the output it
        # was writing, rather than recomputing the decision and interrupting a human twice
        return _perform_write(
            state, deps, FinalOutput.model_validate(receipt["output"]), resume_phase=phase
        )
```

Inside `_perform_write` the decision to send the PATCH is:

```python
write_fields = True
if resume_phase in ("fields_written", "logged"):
    write_fields = False
elif resume_phase is not None:
    write_fields = not _write_already_landed(state, deps, incident, payload)
```

`_write_already_landed` asks ServiceNow whether *this* attempt's PATCH is already there:
it compares the run's own `ai_processing_start` first (written by the same PATCH, so a
match proves this attempt landed rather than an earlier one), and otherwise requires
`work_notes`, `ai_classification`, `ai_suggestion`, `ai_agent_version` and
`ai_model_name` to match what was sent. **Any read failure returns `False` — write
again** — because at worst the work note is appended twice, which is the pre-existing
failure, rather than a write silently skipped.

This ensures:
- A terminal receipt short-circuits the whole boundary: no PATCH, no work note, no second
  execution-log row
- An in-flight receipt is resolved against ServiceNow, not guessed from local state

### Lifecycle Distinction

The receipt's `lifecycle` field distinguishes:

- `"direct"`: Normal execution without interrupt (no human in the loop)
- `"interrupt_resume"`: Execution was paused at interrupt, then resumed after human decision

This allows audit queries to distinguish:
- Intentional HITL workflows (interrupt → brief → decide → resume → write)
- Crash-recovery scenarios (crash → restart → receipt check → skip or write)

## Crash Scenarios

### Scenario 1: Kill-Before-Write

**Timeline:**
1. Graph reaches `act()`, hits interrupt outcome
2. `interrupt_payload()` saved to audit store
3. `interrupt()` called, graph pauses
4. On resume, `act()` saves the intent receipt, phase `writing`
5. **Worker crashes** before ServiceNow applies the PATCH

**Recovery:**
1. Worker restarts, receives same event (idempotent enqueue)
2. Graph resumes from checkpoint with human decision
3. `act()` finds a receipt whose phase is `writing` — in flight, not done
4. `_write_already_landed` asks ServiceNow; nothing matches, so it returns `False`
5. The PATCH proceeds — the one write that never happened
6. Phases advance `fields_written` → `logged` → `written`

**Result:** Exactly one write to ServiceNow. Receipt lifecycle = `"interrupt_resume"`.

### Scenario 2: Kill-After-Write

**Timeline:**
1. Graph completes the PATCH; ServiceNow applies it
2. **Worker crashes** before the `fields_written` receipt is saved
3. ServiceNow carries the work note; the audit store still says `writing`

**Recovery:**
1. Worker restarts, receives same event (redelivery protection kicks in)
2. Graph runs to `act()`
3. `act()` finds phase `writing` — an unresolved boundary, not a completed write
4. `_write_already_landed` reads the incident back and finds this run's own
   `ai_processing_start`, so it returns `True`
5. PATCH skipped, the execution log is written once, phases advance to `written`

**Result:** Exactly one write to ServiceNow (the original) and one work note. This is the
window a receipt-after-the-write design cannot see, and it is the case
`test_kill_after_the_write_never_duplicates_it` exists for: that test fails if the
read-back probe is removed.

### Scenario 3: Kill between the execution log and the final receipt

**Timeline:**
1. PATCH landed, receipt at `fields_written`
2. The execution-log row landed
3. **Worker crashes** before the `logged` receipt is saved

**Recovery:**
1. `act()` finds `fields_written`, skips the PATCH
2. Writes the execution log once, saves `logged` then `written`

**Result:** One incident write, one log row, two receipt saves. Covered by
`test_kill_between_the_execution_log_and_the_final_receipt_does_not_replay_the_log`.

### Scenario 4: Kill after the log row, before the `logged` receipt — still open

**Timeline:**
1. PATCH landed (`fields_written`), the execution-log row landed
2. **Worker crashes** in the window before the receipt advances to `logged`
3. The receipt therefore still reads `fields_written`

**Recovery:** `act()` skips the PATCH and replays the execution-log insert, so a second
row is written.

**Why it is not closed.** Closing it needs a read-back probe for the execution-log table
the way `_write_already_landed` probes the incident, and `execution_id` is deliberately
not unique on that table: the knowledge-capture path writes its own row under the same
execution id (`agent="knowledge_capture"`, `agent/knowledge_capture.py`), and a redelivery
that ends in a human lock writes a second `blocked` row. Row existence therefore cannot
distinguish *this* attempt from an earlier one, and a probe that guessed "already written"
would risk losing the audit row for a run that never wrote one.

**Consequence, stated plainly:** an extra audit row, and nothing else. No duplicate work
note, no lost suggestion, no wrong field value. The authoritative record of a run is
`executions` + `workflow_state`; the execution-log table is a supporting audit trail.

### Scenario 5: Multiple Crashes After Write

**Timeline:**
1. Write succeeds, receipt saved
2. Worker crashes
3. Worker restarts, crashes again
4. Worker restarts again...

**Recovery:**
Each restart:
1. `act()` finds a terminal phase
2. Returns the recorded output
3. No ServiceNow call

**Result:** Still exactly one write, regardless of crash count.

## Audit Store Implementation

### Interface

```python
class GraphAuditStore(Protocol):
    def save_interrupt(self, execution_id: str, payload: dict[str, Any]) -> None: ...
    def get_interrupt(self, execution_id: str) -> dict[str, Any] | None: ...
    def save_receipt(self, execution_id: str, receipt: dict[str, Any]) -> None: ...
    def get_receipt(self, execution_id: str) -> dict[str, Any] | None: ...
```

### Postgres Implementation

Uses `workflow_state.ExecutionNodeState` table (S2.2 schema):

```python
class PostgresGraphAuditStore:
    def save_interrupt(self, execution_id: str, payload: dict[str, Any]) -> None:
        self._upsert(execution_id, "hitl.interrupt", "awaiting_approval", payload)

    def save_receipt(self, execution_id: str, receipt: dict[str, Any]) -> None:
        self._upsert(execution_id, "servicenow.write", "succeeded", receipt)

    def _upsert(self, execution_id: str, node: str, status: str, decision: dict[str, Any]):
        # Uses INSERT ... ON CONFLICT DO UPDATE on uq_workflow_state_execution_node_attempt
        # Increments sequence_number for each write
```

### Memory Implementation

For unit tests (no Postgres):

```python
class MemoryGraphAuditStore:
    def __init__(self):
        self.interrupts: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
```

## Worker Integration

### Status Tracking

In `tasks.py`, after graph execution:

```python
if result.get("paused"):
    repo.mark_awaiting_approval(
        execution_uuid,
        node_reached=str(result["node_reached"]),
        agent_version=str(result["agent_version"]),
    )
    return {"status": "awaiting_approval", "execution_id": execution_id, "result": result}
```

### Redelivery Protection

The worker already has redelivery protection via `claim_for_running()`. For awaiting_approval status:

```python
if status == "awaiting_approval":
    logger.info("hitl_redelivery_skipped", execution_id=execution_id)
    return {"status": "awaiting_approval", "execution_id": execution_id}
```

This prevents re-processing a paused thread until it's resumed via the approvals API.

## Testing

### Crash Recovery Tests (`tests/test_crash_recovery.py`)

The whole file, against a real `act` and a real `FakeServiceNow` where the write boundary
is what is under test:

- `test_kill_before_the_write_records_an_in_flight_receipt_and_the_retry_writes_once`:
  the intent receipt is saved before the PATCH, so the restart finds phase `writing`,
  the read-back finds nothing, and exactly one write happens
- `test_kill_after_the_write_never_duplicates_it`: the incident carries the work note
  while the receipt still says `writing`; the restart proves the write landed with a
  `read_incident` probe instead of sending the PATCH again. **Mutation-checked** — the
  test fails if the probe is removed
- `test_kill_between_the_execution_log_and_the_final_receipt_does_not_replay_the_log`:
  a kill as the terminal receipt would be written leaves the receipt at `logged`; the
  restart returns the recorded output and adds no second log row
- `test_multiple_crashes_after_write`: four cycles dying at different points — before the
  PATCH, after the PATCH but before the log, after both, and once more after completion —
  end with one write and one log row
- `test_receipt_prevents_duplicate_writes`: a redelivery of a completed execution
  short-circuits the whole boundary (no PATCH, no work note, no second log row)
- `test_receipt_check_in_act`: a terminal receipt short-circuits on entry to `act`
- `test_lifecycle_field_distinguishes_paths`, `test_audit_lifecycle_direct`,
  `test_audit_lifecycle_interrupt_resume`: the `lifecycle` field separates `direct` from
  `interrupt_resume`
- `test_interrupt_without_resume_stays_awaiting`: an interrupt that is never resumed
  leaves an interrupt record and no receipt
- `test_audit_store_concurrent_access`: five executions' records stay retrievable

### Integration with Interrupt Tests

The interrupt tests (`tests/test_interrupt_resume.py`) also verify:
- Resume after interrupt completes with write
- Receipt lifecycle is `"interrupt_resume"`
- Audit store has both interrupt and receipt records

## Edge Cases

### No Receipt, No Interrupt

If a thread reaches `act()` with neither interrupt nor receipt, it decides the outcome,
composes the output and writes it, then saves the receipt from `writing` onward. If
`AGENT_WRITE_BACK_ENABLED` is false the run is recorded as `dry_run` instead.

### Terminal Receipt (`written`, `logged`, or no phase)

The write and the log row are both done, so `act` returns the recorded output and touches
nothing. A receipt with no `phase` predates the boundary markers and can only have been
written after the write completed, so it is treated the same way.

### In-Flight Receipt (`writing`, `fields_written`)

The previous attempt died inside the write boundary. `act` resumes the write with the
output it was writing, so the human is not interrupted a second time, and the ServiceNow
read-back decides whether the PATCH goes out again.

### Interrupt Exists, No Receipt

This is the kill-before-write case. Resume proceeds with the write, receipt lifecycle
`interrupt_resume`.

## Dependencies

- S2.2 `workflow_state` schema and Postgres checkpointer
- S2.5 LangGraph state machine
- S3.4 interrupt/resume mechanism
- Celery worker idempotency (claim_for_running, redelivery protection)

## Success Criteria

- Kill-before-write: resume writes exactly once
- Kill-after-write: restart does not duplicate — proven against ServiceNow, not guessed
  from the receipt
- Audit trail distinguishes interrupt-resume from crash-recovery
- Multiple crashes after write still result in one write
- Paused threads remain awaiting_approval until resumed
- **Known residual, accepted:** a kill after the execution-log row lands but before the
  receipt reaches `logged` replays the log row (Scenario 4). The consequence is one extra
  audit row; `executions` + `workflow_state` remain the authoritative record.
