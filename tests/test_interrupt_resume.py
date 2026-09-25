"""LangGraph interrupt/resume tests (S3.4)."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from agent.audit_store import MemoryGraphAuditStore
from agent.graph import build_graph, run_graph
from agent.state import (
    AgentState,
    ConfidenceResult,
    Draft,
    DraftStep,
    EventPayload,
    Outcome,
    RiskAssessment,
    RiskLevel,
)
from tests.agent_support import (
    EXECUTION_ID,
    ORDER_P1,
    VPN,
    FakeLLM,
    FakeServiceNow,
    build_fake_deps,
    event_for,
    make_deps,
    vpn_answers,
)


def test_audit_store_interrupt_operations() -> None:
    """Memory audit store saves and retrieves interrupt payloads."""
    store = MemoryGraphAuditStore()
    execution_id = "test-interrupt-1"

    payload = {
        "outcome": "escalated_high_risk",
        "lifecycle": "interrupt",
        "execution_id": execution_id,
        "incident": {"number": "INC0010001"},
    }

    store.save_interrupt(execution_id, payload)
    retrieved = store.get_interrupt(execution_id)

    assert retrieved is not None
    assert retrieved["outcome"] == "escalated_high_risk"
    assert retrieved["lifecycle"] == "interrupt"


def test_audit_store_receipt_operations() -> None:
    """Memory audit store saves and retrieves write receipts."""
    store = MemoryGraphAuditStore()
    execution_id = "test-receipt-1"

    receipt = {
        "output": {"outcome": "suggested"},
        "lifecycle": "direct",
        "incident_sys_id": "abc123",
    }

    store.save_receipt(execution_id, receipt)
    retrieved = store.get_receipt(execution_id)

    assert retrieved is not None
    assert retrieved["lifecycle"] == "direct"
    assert retrieved["incident_sys_id"] == "abc123"


def test_audit_store_separate_interrupt_and_receipt() -> None:
    """Interrupt and receipt are stored separately for the same execution."""
    store = MemoryGraphAuditStore()
    execution_id = "test-both-1"

    interrupt_payload = {
        "outcome": "escalated_high_risk",
        "lifecycle": "interrupt",
        "execution_id": execution_id,
    }

    receipt = {
        "output": {"outcome": "suggested"},
        "lifecycle": "interrupt_resume",
        "incident_sys_id": "xyz789",
    }

    store.save_interrupt(execution_id, interrupt_payload)
    store.save_receipt(execution_id, receipt)

    interrupt = store.get_interrupt(execution_id)
    receipt_retrieved = store.get_receipt(execution_id)

    assert interrupt is not None
    assert interrupt["lifecycle"] == "interrupt"
    assert receipt_retrieved is not None
    assert receipt_retrieved["lifecycle"] == "interrupt_resume"


def test_audit_store_missing_returns_none() -> None:
    """Missing interrupt or receipt returns None."""
    store = MemoryGraphAuditStore()

    assert store.get_interrupt("nonexistent") is None
    assert store.get_receipt("nonexistent") is None


def test_audit_store_overwrites_on_save() -> None:
    """Saving overwrites existing interrupt/receipt."""
    store = MemoryGraphAuditStore()
    execution_id = "test-overwrite"

    first_payload = {"outcome": "first", "lifecycle": "interrupt"}
    second_payload = {"outcome": "second", "lifecycle": "interrupt"}

    store.save_interrupt(execution_id, first_payload)
    store.save_interrupt(execution_id, second_payload)

    retrieved = store.get_interrupt(execution_id)
    assert retrieved["outcome"] == "second"


def test_deps_includes_audit_store() -> None:
    """AgentDependencies includes audit store."""
    deps = build_fake_deps()
    assert hasattr(deps, "audit")
    assert isinstance(deps.audit, MemoryGraphAuditStore)


def test_interrupt_outcomes_set() -> None:
    """INTERRUPT_OUTCOMES includes the three required outcomes."""
    from agent.nodes.act import INTERRUPT_OUTCOMES

    assert Outcome.ESCALATED_HIGH_RISK in INTERRUPT_OUTCOMES
    assert Outcome.ESCALATED_BLOCKED in INTERRUPT_OUTCOMES
    assert Outcome.ESCALATED_LOW_CONFIDENCE in INTERRUPT_OUTCOMES
    assert Outcome.ESCALATED_NO_EVIDENCE not in INTERRUPT_OUTCOMES


def test_decide_outcome_high_risk_interrupt() -> None:
    """decide_outcome returns ESCALATED_HIGH_RISK for high risk."""
    from agent.nodes.act import decide_outcome

    state_high_risk: AgentState = {
        "risk": RiskAssessment(
            level=RiskLevel.HIGH,
            reasons=["P1"],
            approval_required=True,
        ).model_dump(),
    }
    assert decide_outcome(state_high_risk) == Outcome.ESCALATED_HIGH_RISK


def test_audit_store_lifecycle_field() -> None:
    """Audit store payloads include lifecycle field for path distinction."""
    store = MemoryGraphAuditStore()
    execution_id = "test-lifecycle"

    # Interrupt payload
    interrupt = {
        "outcome": "escalated_high_risk",
        "lifecycle": "interrupt",
        "execution_id": execution_id,
    }
    store.save_interrupt(execution_id, interrupt)

    # Receipt payload
    receipt = {
        "output": {"outcome": "suggested"},
        "lifecycle": "interrupt_resume",
        "incident_sys_id": "abc123",
    }
    store.save_receipt(execution_id, receipt)

    retrieved_interrupt = store.get_interrupt(execution_id)
    retrieved_receipt = store.get_receipt(execution_id)

    assert retrieved_interrupt["lifecycle"] == "interrupt"
    assert retrieved_receipt["lifecycle"] == "interrupt_resume"
    # Can distinguish paths
    assert retrieved_interrupt["lifecycle"] != retrieved_receipt["lifecycle"]


def test_interrupt_payload_structure() -> None:
    """interrupt_payload returns correct structure."""
    from agent.nodes.act import interrupt_payload
    from agent.state import FinalOutput

    state: AgentState = {
        "execution_id": "test-exec",
        "correlation_id": "test-corr",
        "incident": {"sys_id": "abc123", "number": "INC0010001"},
        "risk": RiskAssessment(
            level=RiskLevel.HIGH, reasons=["P1"], approval_required=True
        ).model_dump(),
        "confidence": ConfidenceResult(passed=True, score=0.9, floor=0.7).model_dump(),
        "draft": Draft(
            rendered="Test",
            sources=["KB0001"],
            steps=[DraftStep(text="Test action", article_id="KB0001", section="Resolution")],
        ).model_dump(),
    }

    output = FinalOutput(
        outcome=Outcome.ESCALATED_HIGH_RISK,
        summary="High risk",
        work_note="High risk",
        human_review_required=True,
        processing_state="awaiting_approval",
    )

    payload = interrupt_payload(state, output, Outcome.ESCALATED_HIGH_RISK)

    assert payload["outcome"] == "escalated_high_risk"
    assert payload["lifecycle"] == "interrupt"
    assert payload["execution_id"] == "test-exec"
    assert payload["correlation_id"] == "test-corr"
    assert "incident" in payload
    assert "risk" in payload


# -- FR-17 end to end: pause, then resume the *same* execution ----------------------


def _run(
    deps: Any,
    record: dict[str, Any],
    saver: InMemorySaver,
    *,
    resume: dict[str, Any] | None = None,
    nodes: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    graph = build_graph(deps, checkpointer=saver, nodes=nodes)
    result = run_graph(
        graph,
        EventPayload.model_validate(event_for(record)),
        execution_id=EXECUTION_ID,
        correlation_id="corr-hitl",
        attempt=1,
        deps=deps,
        resume=resume,
    )
    return graph, result


def test_interrupt_at_high_risk() -> None:
    """A P1 pauses in act: nothing written, payload persisted, act still pending."""
    backend = FakeServiceNow()
    deps = make_deps(llm=FakeLLM(vpn_answers()), servicenow=backend)
    saver = InMemorySaver()

    graph, result = _run(deps, ORDER_P1, saver)

    assert result["paused"] is True
    assert result["outcome"] == "escalated_high_risk"
    assert backend.updates == []
    assert deps.servicenow.calls == ["read_incident"]

    payload = deps.audit.get_interrupt(EXECUTION_ID)
    assert payload is not None
    assert payload["outcome"] == "escalated_high_risk"
    assert payload["lifecycle"] == "interrupt"
    assert payload["incident"]["number"] == ORDER_P1["number"]
    assert payload["brief"]["judgment_required"]
    assert payload["brief"]["degraded"] is False

    # The checkpoint itself shows act waiting, not completed.
    snapshot = graph.get_state({"configurable": {"thread_id": EXECUTION_ID, "attempt": 1}})
    assert snapshot.next == ("act",)
    assert "act" not in snapshot.values["path"]


def test_interrupt_at_blocked_gate() -> None:
    """A failed safety gate pauses instead of writing the escalation note."""
    backend = FakeServiceNow()
    deps = make_deps(servicenow=backend)

    def failing_safety(state: Any, deps: Any) -> dict[str, Any]:
        return {
            "safety": {
                "gate": "safety_check",
                "passed": False,
                "implemented": True,
                "checks": [],
                "reason": "draft contains a credential",
            }
        }

    _, result = _run(deps, VPN, InMemorySaver(), nodes={"safety_check": failing_safety})

    assert result["paused"] is True
    assert result["outcome"] == "escalated_blocked"
    assert result["path"][-1] == "safety_check"
    assert backend.updates == []
    payload = deps.audit.get_interrupt(EXECUTION_ID)
    assert payload is not None
    assert payload["safety"]["passed"] is False


def test_interrupt_at_low_confidence() -> None:
    """A draft under the confidence floor pauses after every gate has run."""
    backend = FakeServiceNow()
    deps = make_deps(llm=FakeLLM(vpn_answers(confidence=0.3)), servicenow=backend)

    _, result = _run(deps, VPN, InMemorySaver())

    assert result["paused"] is True
    assert result["outcome"] == "escalated_low_confidence"
    assert result["confidence"] < deps.settings.agent_confidence_floor
    assert backend.updates == []
    assert deps.audit.get_interrupt(EXECUTION_ID) is not None


def test_resume_with_approval() -> None:
    """Approving resumes the same thread to completion and performs the write."""
    backend = FakeServiceNow()
    deps = make_deps(llm=FakeLLM(vpn_answers()), servicenow=backend)
    saver = InMemorySaver()

    _, paused = _run(deps, ORDER_P1, saver)
    assert paused["paused"] is True

    _, resumed = _run(
        deps,
        ORDER_P1,
        saver,
        resume={"decision": "approved", "decided_by": "ops_analyst_1", "reason": "P1 window"},
    )

    assert resumed["resumed"] is True
    assert resumed["paused"] is False
    assert resumed["outcome"] == "escalated_high_risk"
    assert len(backend.updates) == 1
    body = backend.updates[0][1].to_table_api_body()
    assert body["x_2215032_ai_inc_0_ai_human_review_required"] == "true"

    receipt = deps.audit.get_receipt(EXECUTION_ID)
    assert receipt is not None
    assert receipt["lifecycle"] == "interrupt_resume"
    # The interrupt record is the audit trail; it is not consumed by the resume.
    assert deps.audit.get_interrupt(EXECUTION_ID) is not None


def test_resume_with_rejection() -> None:
    """Rejecting records the refusal instead of the planned suggestion."""
    backend = FakeServiceNow()
    deps = make_deps(llm=FakeLLM(vpn_answers()), servicenow=backend)
    saver = InMemorySaver()

    _, paused = _run(deps, ORDER_P1, saver)
    assert paused["paused"] is True

    _, resumed = _run(
        deps,
        ORDER_P1,
        saver,
        resume={"decision": "rejected", "decided_by": "security", "reason": "change freeze"},
    )

    assert resumed["resumed"] is True
    assert resumed["suggested"] is False
    assert len(backend.updates) == 1
    body = backend.updates[0][1].to_table_api_body()
    assert "x_2215032_ai_inc_0_ai_suggestion" not in body
    assert "human rejected by security" in body["work_notes"].lower()


def test_no_interrupt_on_suggested() -> None:
    """The happy path never interrupts and never touches the audit interrupt record."""
    backend = FakeServiceNow()
    deps = make_deps(servicenow=backend)

    _, result = _run(deps, VPN, InMemorySaver())

    assert result["paused"] is False
    assert result["outcome"] == "suggested"
    assert len(backend.updates) == 1
    assert deps.audit.get_interrupt(EXECUTION_ID) is None
    assert deps.audit.get_receipt(EXECUTION_ID)["lifecycle"] == "direct"


def test_double_resume_protection() -> None:
    """A completed thread returns its recorded output instead of running again."""
    backend = FakeServiceNow()
    deps = make_deps(servicenow=backend)
    saver = InMemorySaver()

    _, first = _run(deps, VPN, saver)
    _, second = _run(deps, VPN, saver)

    assert first["outcome"] == second["outcome"] == "suggested"
    assert second["resumed"] is True
    assert len(backend.updates) == 1
