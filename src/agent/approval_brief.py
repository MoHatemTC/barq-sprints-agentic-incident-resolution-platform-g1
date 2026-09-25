"""Approval Brief Agent — descriptive only, never steers resume routing (S3.4).

Reads the interrupt payload persisted at pause time. A failed or timed-out LLM
call degrades to a structured fallback built from the same payload so GET
/approvals still returns facts.
"""

from __future__ import annotations

import json
from typing import Any

from agent.dependencies import AgentDependencies
from agent.prompts import (
    APPROVAL_BRIEF_SYSTEM,
    ApprovalBriefOutput,
    approval_brief_prompt,
)

BRIEF_TIMEOUT_SECONDS = 20.0


def _fallback(payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "incident_summary": str(payload.get("incident_summary") or payload.get("summary") or ""),
        "gate": str(payload.get("outcome") or payload.get("gate") or "unknown"),
        "planned_action": str(payload.get("planned_action") or payload.get("work_note") or ""),
        "judgment_required": (
            "Approve to apply the planned ServiceNow write, or reject to record "
            "the refusal and leave the incident with a human."
        ),
        "degraded": True,
        "degraded_reason": reason,
    }


def render_brief(payload: dict[str, Any], deps: AgentDependencies) -> dict[str, Any]:
    """Synthesize a brief. Never raises; never inspects or sets routing fields."""
    cached = payload.get("brief")
    if isinstance(cached, dict) and cached.get("incident_summary"):
        return cached
    try:
        parsed = deps.llm.structured(
            purpose="approval_brief",
            system=APPROVAL_BRIEF_SYSTEM,
            prompt=approval_brief_prompt(json.dumps(payload, default=str, sort_keys=True)[:8000]),
            schema=ApprovalBriefOutput,
        )
        return parsed.model_dump(mode="json") | {"degraded": False}
    except Exception as exc:  # noqa: BLE001 — brief must degrade, never fail GET/resume
        return _fallback(payload, reason=type(exc).__name__)


__all__ = ["BRIEF_TIMEOUT_SECONDS", "render_brief"]
