"""Approval Brief Agent tests (S3.4)."""

from __future__ import annotations

import time

from agent import approval_brief
from agent.approval_brief import BRIEF_TIMEOUT_SECONDS, _fallback, render_brief
from agent.prompts import ApprovalBriefOutput
from tests.agent_support import build_fake_deps


def test_fallback_basic() -> None:
    """Fallback builds a structured brief from raw payload when LLM fails."""
    payload = {
        "outcome": "escalated_high_risk",
        "summary": "Risk assessed as high before retrieval",
        "work_note": "No action taken. Escalated for human decision.",
        "planned_action": "Write the composed work note to ServiceNow.",
        "incident": {"number": "INC0010001", "short_description": "VPN not connecting"},
    }
    result = _fallback(payload, reason="TimeoutError")
    assert result["incident_summary"] == "Risk assessed as high before retrieval"
    assert result["gate"] == "escalated_high_risk"
    assert result["planned_action"] == "Write the composed work note to ServiceNow."
    assert result["degraded"] is True
    assert result["degraded_reason"] == "TimeoutError"
    assert "Approve to apply" in result["judgment_required"]


def test_fallback_missing_fields() -> None:
    """Fallback handles missing fields gracefully."""
    payload = {}
    result = _fallback(payload, reason="ValueError")
    assert result["incident_summary"] == ""
    assert result["gate"] == "unknown"
    assert result["planned_action"] == ""
    assert result["degraded"] is True


def test_render_brief_uses_cache() -> None:
    """If a cached brief exists in the payload, return it without calling LLM."""
    cached = {
        "incident_summary": "Cached summary",
        "gate": "escalated",
        "planned_action": "Cached action",
        "judgment_required": "Cached judgment",
    }
    payload = {"brief": cached}
    deps = build_fake_deps()
    result = render_brief(payload, deps)
    assert result == cached
    # LLM should not have been called
    assert len(deps.llm.calls) == 0


def test_render_brief_calls_llm() -> None:
    """Without cache, render_brief calls the LLM and returns structured output."""
    payload = {
        "outcome": "escalated_high_risk",
        "summary": "High risk incident",
        "incident": {"number": "INC0010001", "short_description": "Test"},
    }
    deps = build_fake_deps()
    result = render_brief(payload, deps)
    assert result["degraded"] is False
    assert "incident_summary" in result
    assert "gate" in result
    assert len(deps.llm.calls) == 1
    assert deps.llm.calls[0]["purpose"] == "approval_brief"


def test_render_brief_degrades_on_llm_failure() -> None:
    """If LLM fails, render_brief falls back to structured fallback."""
    payload = {
        "outcome": "escalated_high_risk",
        "summary": "High risk incident",
        "incident": {"number": "INC0010001"},
    }
    deps = build_fake_deps()
    deps.llm.answers["approval_brief"] = RuntimeError("LLM failed")
    result = render_brief(payload, deps)
    assert result["degraded"] is True
    assert result["degraded_reason"] == "RuntimeError"
    assert result["incident_summary"] == "High risk incident"


def test_render_brief_timeout_constant() -> None:
    """Timeout constant is defined and reasonable."""
    assert isinstance(BRIEF_TIMEOUT_SECONDS, (int, float))
    assert BRIEF_TIMEOUT_SECONDS > 0


def test_render_brief_large_payload_truncation() -> None:
    """Large payloads are truncated to avoid token limits."""
    large_payload = {"data": "x" * 10000, "outcome": "escalated_high_risk"}
    deps = build_fake_deps()
    render_brief(large_payload, deps)
    # Should not crash, should call LLM with truncated payload
    assert len(deps.llm.calls) == 1
    prompt = deps.llm.calls[0]["prompt"]
    assert len(prompt) < 10000  # Truncated


class _SlowLLM:
    """An LLM client whose approval-brief call never returns in time."""

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self.calls: list[dict] = []

    def structured(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        time.sleep(self._delay)
        return ApprovalBriefOutput(
            incident_summary="never delivered",
            gate="escalated_high_risk",
            planned_action="none",
            judgment_required="none",
        )


def test_a_brief_that_overruns_its_budget_degrades_instead_of_hanging(monkeypatch) -> None:
    """A hung model call must not hold the pause open.

    ``render_brief`` runs inside ``act``, so before the budget was enforced a
    stalled LiteLLM connection blocked the run with the execution neither parked
    nor written. The brief is descriptive only, so overrunning it is worth
    exactly the fallback.
    """
    monkeypatch.setattr(approval_brief, "BRIEF_TIMEOUT_SECONDS", 0.2)
    deps = build_fake_deps(llm=_SlowLLM(delay=5.0))
    payload = {"outcome": "escalated_high_risk", "work_note": "P1 escalation"}

    started = time.monotonic()
    brief = render_brief(payload, deps)
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, "render_brief returned only because the budget was enforced"
    assert brief["degraded"] is True
    assert brief["degraded_reason"] == "BriefTimeout"
    # The fallback is still a usable brief built from the same payload.
    assert brief["gate"] == "escalated_high_risk"
    assert brief["planned_action"] == "P1 escalation"


def test_a_brief_within_budget_is_not_marked_degraded(monkeypatch) -> None:
    """The timeout must not turn a healthy brief into a fallback."""
    monkeypatch.setattr(approval_brief, "BRIEF_TIMEOUT_SECONDS", 10.0)
    deps = build_fake_deps(llm=_SlowLLM(delay=0.05))
    brief = render_brief({"outcome": "escalated_high_risk"}, deps)
    assert brief["degraded"] is False
    assert brief["incident_summary"] == "never delivered"
