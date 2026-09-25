"""Approval Brief Agent tests (S3.4)."""

from __future__ import annotations

from agent.approval_brief import BRIEF_TIMEOUT_SECONDS, _fallback, render_brief
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
