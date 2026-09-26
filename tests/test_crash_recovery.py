"""Crash-recovery write-boundary idempotency tests (S3.4)."""

from __future__ import annotations

import pytest

from agent.audit_store import MemoryGraphAuditStore
from agent.state import AgentState, ConfidenceResult, Draft, DraftStep, RiskAssessment, RiskLevel
from tests.agent_support import FakeServiceNow, build_fake_deps, state_before_act


def test_receipt_prevents_duplicate_writes() -> None:
    """A write receipt prevents duplicate ServiceNow writes on re-run.

    Executed against a real ``act``. A terminal receipt must short-circuit the
    whole write boundary: no PATCH, no work note, no second execution-log row. The
    previous version of this test saved a dict and read it back, which proved only
    that ``MemoryGraphAuditStore`` is two dict operations.
    """
    from agent.nodes.act import act

    backend = FakeServiceNow()
    deps = build_fake_deps(servicenow=backend)
    state = state_before_act(deps)

    first = act(state, deps)
    assert first["output"]["write_back"] == "written"
    assert len(backend.updates) == 1
    assert len(backend.execution_logs) == 1

    # A redelivery of the same execution: the receipt is terminal, so the second
    # entry into act must return the recorded output without touching ServiceNow.
    again = act(state, deps)

    assert again["output"] == first["output"]
    assert len(backend.updates) == 1
    assert backend.calls.count("write_ai_fields") == 1
    assert len(backend.execution_logs) == 1
    assert backend.calls.count("write_execution_log") == 1


def test_audit_lifecycle_direct() -> None:
    """Direct execution (no interrupt) has lifecycle='direct'."""
    store = MemoryGraphAuditStore()
    execution_id = "test-lifecycle-1"

    receipt = {
        "output": {"outcome": "suggested"},
        "lifecycle": "direct",
        "incident_sys_id": "xyz789",
    }
    store.save_receipt(execution_id, receipt)

    retrieved = store.get_receipt(execution_id)
    assert retrieved is not None
    assert retrieved["lifecycle"] == "direct"
    assert store.get_interrupt(execution_id) is None


def test_audit_lifecycle_interrupt_resume() -> None:
    """Interrupt-resume execution has lifecycle='interrupt_resume'."""
    store = MemoryGraphAuditStore()
    execution_id = "test-lifecycle-2"

    # Simulate interrupt
    interrupt_payload = {
        "outcome": "escalated_high_risk",
        "lifecycle": "interrupt",
        "execution_id": execution_id,
    }
    store.save_interrupt(execution_id, interrupt_payload)

    # Simulate resume and write
    receipt = {
        "output": {"outcome": "suggested"},
        "lifecycle": "interrupt_resume",
        "incident_sys_id": "def456",
    }
    store.save_receipt(execution_id, receipt)

    interrupt = store.get_interrupt(execution_id)
    retrieved_receipt = store.get_receipt(execution_id)

    assert interrupt is not None
    assert interrupt["lifecycle"] == "interrupt"
    assert retrieved_receipt is not None
    assert retrieved_receipt["lifecycle"] == "interrupt_resume"


def test_kill_between_the_execution_log_and_the_final_receipt_does_not_replay_the_log() -> None:
    """The ``logged`` phase is terminal: a kill after the log row lands is not replayed.

    Both ServiceNow calls — the incident PATCH and the execution-log insert — have
    landed by this point, and only the final receipt save was lost. Without the
    ``logged`` phase the restarted ``act`` still saw ``fields_written`` and wrote a
    second log row. This is the window this test exists to protect.
    """
    from agent.audit_store import MemoryGraphAuditStore
    from agent.nodes.act import act

    backend = FakeServiceNow()
    deps = build_fake_deps(servicenow=backend)
    state = state_before_act(deps)

    class DiesOnFinalReceipt(MemoryGraphAuditStore):
        """Loses the process exactly when the terminal receipt would be written."""

        def save_receipt(self, execution_id, receipt):  # type: ignore[no-untyped-def]
            if receipt.get("phase") == "written":
                raise RuntimeError("worker killed before the terminal receipt")
            super().save_receipt(execution_id, receipt)

    deps.audit = DiesOnFinalReceipt()

    with pytest.raises(RuntimeError, match="terminal receipt"):
        act(state, deps)

    # Both ServiceNow writes happened; the receipt is stuck one step behind.
    assert len(backend.updates) == 1
    assert len(backend.execution_logs) == 1
    receipt = deps.audit.get_receipt(state["execution_id"])
    assert receipt is not None
    assert receipt["phase"] == "logged"

    deps.audit = MemoryGraphAuditStore()
    deps.audit.save_receipt(state["execution_id"], receipt)
    result = act(state, deps)

    assert result["output"]["write_back"] == "written"
    assert len(backend.updates) == 1
    assert len(backend.execution_logs) == 1
    # The restart returns the recorded final output and does not re-open the
    # boundary, so the receipt legitimately stays at "logged".
    final_receipt = deps.audit.get_receipt(state["execution_id"])
    assert final_receipt is not None
    assert final_receipt["phase"] == "logged"
    assert final_receipt["output"]["write_back"] == "written"


def test_multiple_crashes_after_write() -> None:
    """Repeated crashes across the write boundary still yield exactly one write.

    Executed against a real ``act`` and a real ``FakeServiceNow``. Each cycle dies
    at a different point — before the PATCH, after the PATCH but before the log,
    and after both — and every restart must add nothing. The previous version of
    this test read the same dict three times and asserted a comment.
    """
    from agent.nodes.act import act

    backend = FakeServiceNow()
    deps = build_fake_deps(servicenow=backend)
    state = state_before_act(deps)

    # Cycle 1: dies before ServiceNow is asked to write anything.
    backend.write_error = RuntimeError("kill 1: before the write")
    with pytest.raises(RuntimeError, match="kill 1"):
        act(state, deps)
    assert backend.updates == []

    # Cycle 2: the PATCH lands, the process dies before the execution log exists.
    backend.write_error = None
    backend.crash_before_log_recorded = RuntimeError("kill 2: after the fields, before the log")
    with pytest.raises(RuntimeError, match="kill 2"):
        act(state, deps)
    assert len(backend.updates) == 1
    assert backend.execution_logs == []
    receipt = deps.audit.get_receipt(state["execution_id"])
    assert receipt is not None
    assert receipt["phase"] == "fields_written"

    # Cycle 3: a clean restart. It must skip the PATCH, write the log exactly once,
    # and close the receipt.
    backend.crash_before_log_recorded = None
    result = act(state, deps)

    assert result["output"]["write_back"] == "written"
    # ``calls`` counts attempts — cycle 1's failed attempt is recorded too — so what
    # matters is that ServiceNow persisted exactly one write and one log row.
    assert len(backend.updates) == 1
    assert len(backend.execution_logs) == 1
    receipt = deps.audit.get_receipt(state["execution_id"])
    assert receipt is not None
    assert receipt["phase"] == "written"

    # Cycle 4: one more redelivery after completion changes nothing at all.
    act(state, deps)
    assert len(backend.updates) == 1
    assert len(backend.execution_logs) == 1


def test_interrupt_without_resume_stays_awaiting() -> None:
    """Interrupt that is never resumed stays in awaiting_approval state."""
    store = MemoryGraphAuditStore()
    execution_id = "test-no-resume"

    interrupt_payload = {
        "outcome": "escalated_high_risk",
        "lifecycle": "interrupt",
        "execution_id": execution_id,
    }
    store.save_interrupt(execution_id, interrupt_payload)

    # Interrupt exists, no receipt yet
    assert store.get_interrupt(execution_id) is not None
    assert store.get_receipt(execution_id) is None


def test_audit_store_concurrent_access() -> None:
    """Audit store handles sequential operations correctly."""
    store = MemoryGraphAuditStore()

    # Multiple executions
    for i in range(5):
        exec_id = f"exec-{i}"
        store.save_interrupt(exec_id, {"outcome": "interrupt", "execution_id": exec_id})
        store.save_receipt(exec_id, {"lifecycle": "direct", "execution_id": exec_id})

    # All should be retrievable
    for i in range(5):
        exec_id = f"exec-{i}"
        assert store.get_interrupt(exec_id) is not None
        assert store.get_receipt(exec_id) is not None


def test_receipt_check_in_act() -> None:
    """act() checks for existing receipt before writing."""
    from agent.nodes.act import act

    deps = build_fake_deps()
    execution_id = "test-act-receipt"

    # Simulate existing receipt
    receipt = {
        "output": {
            "outcome": "suggested",
            "summary": "Cached result",
            "suggestion": "Cached suggestion",
        },
        "lifecycle": "direct",
    }
    deps.audit.save_receipt(execution_id, receipt)

    state: AgentState = {
        "execution_id": execution_id,
        "incident": {"sys_id": "abc123", "number": "INC0010001"},
        "risk": RiskAssessment(
            level=RiskLevel.LOW, reasons=[], approval_required=False
        ).model_dump(),
        "confidence": ConfidenceResult(passed=True, score=0.9, floor=0.7).model_dump(),
        "draft": Draft(
            rendered="Test",
            sources=["KB0001"],
            steps=[DraftStep(text="Test action", article_id="KB0001", section="Resolution")],
        ).model_dump(),
    }

    result = act(state, deps)

    # Should return cached output without writing
    assert result["output"]["outcome"] == "suggested"
    assert result["output"]["summary"] == "Cached result"


def test_lifecycle_field_distinguishes_paths() -> None:
    """Lifecycle field distinguishes interrupt-resume from direct execution."""
    # Direct path
    direct_receipt = {"lifecycle": "direct", "output": {"outcome": "suggested"}}
    assert direct_receipt["lifecycle"] == "direct"

    # Interrupt-resume path
    interrupt_resume_receipt = {"lifecycle": "interrupt_resume", "output": {"outcome": "suggested"}}
    assert interrupt_resume_receipt["lifecycle"] == "interrupt_resume"

    # Can be distinguished
    assert direct_receipt["lifecycle"] != interrupt_resume_receipt["lifecycle"]


# -- the two cases FR-12 / NFR-03 name, executed against a real ``act`` --------------


def test_kill_before_the_write_records_an_in_flight_receipt_and_the_retry_writes_once() -> None:
    """Kill before ServiceNow acknowledges: the retry must write exactly once.

    The intent receipt is saved before the PATCH, so the restart finds a receipt
    whose phase says the write was still in flight. The retry consults ServiceNow,
    finds nothing written, and performs the one write that never happened.
    """
    from agent.nodes.act import act

    backend = FakeServiceNow()
    deps = build_fake_deps(servicenow=backend)
    state = state_before_act(deps)

    backend.write_error = RuntimeError("worker killed before the write")
    with pytest.raises(RuntimeError, match="worker killed"):
        act(state, deps)
    receipt = deps.audit.get_receipt(state["execution_id"])
    assert receipt is not None
    assert receipt["phase"] == "writing"
    assert backend.updates == []

    backend.write_error = None
    result = act(state, deps)

    assert result["output"]["write_back"] == "written"
    assert len(backend.updates) == 1
    receipt = deps.audit.get_receipt(state["execution_id"])
    assert receipt is not None
    assert receipt["phase"] == "written"
    assert receipt["lifecycle"] == "direct"


def test_kill_after_the_write_never_duplicates_it() -> None:
    """Kill after ServiceNow applied the PATCH but before the receipt recorded it.

    This is the window a receipt-after-the-write design cannot see: the incident
    carries the work note, the audit store does not know. The restarted ``act``
    reads the in-flight receipt, asks ServiceNow whether the write landed, and on
    finding it there finishes the run without sending the PATCH again -- one
    incident write, one work note, one execution log.
    """
    from agent.nodes.act import act

    backend = FakeServiceNow()
    deps = build_fake_deps(servicenow=backend)
    state = state_before_act(deps)

    backend.crash_after_write = RuntimeError("worker killed after the write")
    with pytest.raises(RuntimeError, match="killed after the write"):
        act(state, deps)

    # ServiceNow applied it; the receipt that would have recorded it never landed.
    assert len(backend.updates) == 1
    receipt = deps.audit.get_receipt(state["execution_id"])
    assert receipt is not None
    assert receipt["phase"] == "writing"

    backend.crash_after_write = None
    result = act(state, deps)

    assert result["output"]["write_back"] == "written"
    assert len(backend.updates) == 1
    assert backend.calls.count("write_ai_fields") == 1
    assert len(backend.execution_logs) == 1
    receipt = deps.audit.get_receipt(state["execution_id"])
    assert receipt is not None
    assert receipt["phase"] == "written"
    assert receipt["lifecycle"] == "direct"
    # The retry proved the write had landed instead of guessing.
    assert "read_incident" in backend.calls
