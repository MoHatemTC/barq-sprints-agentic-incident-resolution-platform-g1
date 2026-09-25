# Sprint 3.4 — Crash-Recovery and Write-Boundary Idempotency Design

## Overview

This document describes the crash-recovery mechanism for write-boundary idempotency (FR-12, NFR-03), ensuring that worker crashes do not cause duplicate ServiceNow writes or data loss.

## Problem Statement

Celery workers can crash at any point during execution. Without recovery protection:

1. **Kill-before-write**: Worker crashes after interrupt but before ServiceNow write → On restart, should write exactly once
2. **Kill-after-write**: Worker crashes after ServiceNow write but before acknowledgment → On restart, should NOT duplicate the write
3. **No audit trail**: Cannot distinguish between intentional interrupt-resume and crash-recovery

## Solution: Audit-Based Idempotency

### Write Receipt

After a successful ServiceNow write in `act.py`, we save a receipt:

```python
def _perform_write(state, deps, output):
    # ... ServiceNow write happens here ...
    deps.audit.save_receipt(
        str(state["execution_id"]),
        {
            "output": dumped,
            "lifecycle": "interrupt_resume" if was_interrupted else "direct",
            "incident_sys_id": incident.sys_id,
        },
    )
```

The receipt is stored in `workflow_state` as node `servicenow.write` with status `succeeded`.

### Receipt Check on Entry

When `act()` is entered (on initial run or resume), it first checks for an existing receipt:

```python
def act(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    execution_id = str(state.get("execution_id") or "")
    receipt = deps.audit.get_receipt(execution_id) if execution_id else None
    if receipt and receipt.get("output"):
        return {"output": receipt["output"]}  # Already written, skip
```

This ensures:
- If a receipt exists, we return the cached output without calling ServiceNow again
- The write is idempotent across worker restarts

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
4. **Worker crashes** (no write receipt saved)

**Recovery:**
1. Worker restarts, receives same event (idempotent enqueue)
2. Graph resumes from checkpoint with human decision
3. `act()` checks for receipt → none found
4. ServiceNow write proceeds
5. Receipt saved

**Result:** Exactly one write to ServiceNow. Receipt lifecycle = `"interrupt_resume"`.

### Scenario 2: Kill-After-Write

**Timeline:**
1. Graph completes `act()`, ServiceNow write succeeds
2. Receipt saved to audit store
3. **Worker crashes** before acknowledging completion

**Recovery:**
1. Worker restarts, receives same event (redelivery protection kicks in)
2. Graph runs to `act()`
3. `act()` checks for receipt → found!
4. Returns cached output without calling ServiceNow
5. No duplicate write

**Result:** Exactly one write to ServiceNow (the original). Receipt lifecycle = `"direct"` or `"interrupt_resume"` depending on path.

### Scenario 3: Multiple Crashes After Write

**Timeline:**
1. Write succeeds, receipt saved
2. Worker crashes
3. Worker restarts, crashes again
4. Worker restarts again...

**Recovery:**
Each restart:
1. `act()` finds the receipt
2. Returns cached output
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

- `test_kill_before_the_write_leaves_no_receipt_and_the_retry_writes_once`: No
  receipt after a kill before the write; the retry writes exactly once
- `test_kill_after_the_write_never_duplicates_it`: A receipt after the write makes
  the restart return the cached output instead of writing again
- `test_lifecycle_field_distinguishes_paths`: `direct` vs `interrupt_resume`
- `test_multiple_crashes_after_write`: Repeated crashes still result in one write
- `test_interrupt_without_resume_stays_awaiting`: A paused thread stays
  `awaiting_approval` with no receipt until it is resumed
- `test_receipt_check_in_act`, `test_receipt_prevents_duplicate_writes`,
  `test_audit_store_concurrent_access`: Receipt lookup and store behaviour

### Integration with Interrupt Tests

The interrupt tests (`test_interrupt_resume.py`) also verify:
- Resume after interrupt completes with write
- Receipt lifecycle is `"interrupt_resume"`
- Audit store has both interrupt and receipt records

## Edge Cases

### No Receipt, No Interrupt

If a thread reaches `act()` with neither interrupt nor receipt (shouldn't happen in normal flow), it proceeds with write and saves receipt as `"direct"`.

### Receipt Exists, No Interrupt

This is the crash-after-write case. Receipt check returns cached output, lifecycle remains whatever it was (usually `"direct"`).

### Interrupt Exists, No Receipt

This is the kill-before-write case. Resume proceeds with write, receipt saved as `"interrupt_resume"`.

## Dependencies

- S2.2 `workflow_state` schema and Postgres checkpointer
- S2.5 LangGraph state machine
- S3.4 interrupt/resume mechanism
- Celery worker idempotency (claim_for_running, redelivery protection)

## Success Criteria

- Kill-before-write: resume writes exactly once
- Kill-after-write: restart does not duplicate
- Audit trail distinguishes interrupt-resume from crash-recovery
- Multiple crashes after write still result in one write
- Paused threads remain awaiting_approval until resumed
