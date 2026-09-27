from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import agent.guardrails.output_validation as output_validation_module
import agent.nodes.safety_check as safety_check_module
from agent.dependencies import AgentDependencies
from agent.nodes.act import _blocked_gate, decide_outcome
from agent.nodes.safety_check import safety_check
from agent.state import (
    Diagnosis,
    Draft,
    DraftStep,
    Eligibility,
    EvidenceItem,
    GateResult,
    Outcome,
    RetrievalResult,
    RiskAssessment,
    RiskLevel,
)
from observability.tracing import get_tracer


def _evidence(
    article_id: str = "KB0010001", section: str = "Resolution Steps", **overrides: Any
) -> dict:
    base = dict(
        article_id=article_id,
        article_number="KB0010001",
        version="3",
        title="VPN error 807",
        section=section,
        chunk_index=0,
        text="Restart the VPN client service.",
        fused_score=0.9,
        relevance=0.87,
    )
    base.update(overrides)
    return EvidenceItem(**base).model_dump(mode="json")


def _draft(
    steps: list[dict[str, str]], rendered: str = "1. Restart the VPN client service."
) -> dict:
    return Draft(
        steps=[DraftStep(**s) for s in steps],
        rendered=rendered,
        sources=["KB0010001"],
    ).model_dump(mode="json")


def _retrieval(hits: list[dict]) -> dict:
    return RetrievalResult(
        query="vpn error 807",
        category_filter="network",
        hits=[EvidenceItem.model_validate(h) for h in hits],
        best_relevance=0.87,
        threshold=0.55,
        sufficient=True,
        latency_ms=42.0,
    ).model_dump(mode="json")


@pytest.fixture()
def tools_mock() -> Mock:
    """The ToolRegistry stand-in. If safety_check ever calls `.invoke(...)` on
    this, the security test below fails -- it never needs to succeed or raise,
    because it must never be called at all."""
    mock = Mock()
    mock.invoke = AsyncMock(side_effect=AssertionError("safety_check must never call tools.invoke"))
    return mock


@pytest.fixture()
def deps(tools_mock: Mock) -> AgentDependencies:
    return AgentDependencies(
        settings=None,
        llm=None,  # safety_check must not need a model call either
        retriever=None,
        tools=tools_mock,
        tracer=get_tracer(),
    )


def test_valid_output_passes(deps: AgentDependencies) -> None:
    state = {
        "draft": _draft(
            [
                {
                    "text": "Restart the VPN client.",
                    "article_id": "KB0010001",
                    "section": "Resolution Steps",
                }
            ]
        ),
        "retrieval": _retrieval([_evidence()]),
    }
    result = safety_check(state, deps)
    gate = GateResult.model_validate(result["safety"])
    assert gate.gate == "safety_check"
    assert gate.implemented is True
    assert gate.passed is True


def test_malformed_output_fails(deps: AgentDependencies) -> None:
    # No draft at all is the "couldn't even be produced" case.
    result = safety_check({}, deps)
    gate = GateResult.model_validate(result["safety"])
    assert gate.passed is False
    assert gate.implemented is True


def test_excessive_field_length_fails(deps: AgentDependencies) -> None:
    long_text = "x" * 1000
    state = {
        "draft": _draft(
            [
                {
                    "text": long_text,
                    "article_id": "KB0010001",
                    "section": "Resolution Steps",
                }
            ]
        ),
        "retrieval": _retrieval([_evidence()]),
    }
    result = safety_check(state, deps)
    gate = GateResult.model_validate(result["safety"])
    assert gate.passed is False
    assert any(c["category"] == "field_length" for c in gate.checks)


def test_invalid_action_fails(deps: AgentDependencies) -> None:
    state = {
        "draft": _draft(
            [
                {
                    "text": "I have already "
                    "closed this incident and reset the user's password directly.",
                    "article_id": "KB0010001",
                    "section": "Resolution Steps",
                }
            ]
        ),
        "retrieval": _retrieval([_evidence()]),
    }
    result = safety_check(state, deps)
    gate = GateResult.model_validate(result["safety"])
    assert gate.passed is False
    assert any(c["category"] == "action_contract" for c in gate.checks)


def test_failure_produces_a_failed_gate_result_shape(deps: AgentDependencies) -> None:
    state = {"draft": _draft([]), "retrieval": _retrieval([_evidence()])}
    result = safety_check(state, deps)
    gate = GateResult.model_validate(result["safety"])
    assert gate.gate == "safety_check"
    assert gate.passed is False
    assert gate.implemented is True
    assert gate.reason


def test_failure_reaches_the_existing_escalated_blocked_flow(
    deps: AgentDependencies,
) -> None:
    """``decide_outcome``/``_blocked_gate`` already route a failed ``safety``
    gate to ``ESCALATED_BLOCKED``; this asserts safety_check's own output
    plugs into that existing mechanism, without reimplementing it here."""
    state = {
        "eligibility": Eligibility(eligible=True).model_dump(mode="json"),
        "risk": RiskAssessment(level=RiskLevel.LOW, reasons=[], approval_required=False).model_dump(
            mode="json"
        ),
        "retrieval": _retrieval([_evidence()]),
        "diagnosis": Diagnosis(
            probable_cause="stale VPN session",
            matched_article_ids=["KB0010001"],
            symptom_match=True,
            model_confidence=0.8,
            rationale="matches",
        ).model_dump(mode="json"),
        "verification": GateResult(
            gate="verify_evidence", passed=True, implemented=True
        ).model_dump(mode="json"),
        "draft": _draft(
            [
                {
                    "text": "I have already closed this incident.",
                    "article_id": "KB0010001",
                    "section": "Resolution Steps",
                }
            ]
        ),
    }
    result = safety_check(state, deps)
    state["safety"] = result["safety"]

    assert decide_outcome(state) is Outcome.ESCALATED_BLOCKED
    blocked = _blocked_gate(state)
    assert blocked is not None
    assert blocked.gate == "safety_check"


def test_safety_check_never_invokes_the_tool_registry(
    deps: AgentDependencies, tools_mock: Mock
) -> None:
    """Security requirement: safety_check must never execute or invoke
    ToolRegistry. `deps.tools` *is* the ToolRegistry in this codebase, so this
    is a direct assertion, not a proxy for one."""
    state = {
        "draft": _draft(
            [
                {
                    "text": "Restart the VPN client.",
                    "article_id": "KB0010001",
                    "section": "Resolution Steps",
                }
            ]
        ),
        "retrieval": _retrieval([_evidence()]),
    }
    safety_check(state, deps)
    tools_mock.invoke.assert_not_called()


def test_safety_check_never_invokes_the_tool_registry_even_on_a_failing_draft(
    deps: AgentDependencies, tools_mock: Mock
) -> None:
    state = {"draft": _draft([]), "retrieval": _retrieval([_evidence()])}
    safety_check(state, deps)
    tools_mock.invoke.assert_not_called()


def test_safety_check_source_never_references_tool_registry_or_authorization() -> None:
    """Belt-and-braces static check alongside the runtime assertion above: the
    node's own source, and the guardrail checks it calls, should never even
    mention ToolRegistry/PermissionClass/Approval as *code* (docstrings that
    explain the boundary in prose are fine and expected)."""
    import ast

    def referenced_identifiers(source: str) -> set[str]:
        tree = ast.parse(source)
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        return names

    for module in (safety_check_module, output_validation_module):
        identifiers = referenced_identifiers(inspect.getsource(module))
        for forbidden in ("ToolRegistry", "invoke", "PermissionClass", "Approval"):
            assert forbidden not in identifiers, (
                f"{module.__name__} must not reference {forbidden!r}"
            )
