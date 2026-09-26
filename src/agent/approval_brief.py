"""Approval Brief Agent — descriptive only, never steers resume routing (S3.4).

Reads the interrupt payload persisted at pause time. A failed or timed-out LLM
call degrades to a structured fallback built from the same payload so GET
/approvals still returns facts.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent.dependencies import AgentDependencies
from agent.prompts import (
    APPROVAL_BRIEF_SYSTEM,
    ApprovalBriefOutput,
    approval_brief_prompt,
)

BRIEF_TIMEOUT_SECONDS = 20.0


class BriefTimeout(Exception):
    """The brief model call overran its budget; the caller degrades."""


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


def _synthesise(payload: dict[str, Any], deps: AgentDependencies) -> ApprovalBriefOutput:
    """Call the model under :data:`BRIEF_TIMEOUT_SECONDS`.

    A brief is descriptive only, so a call that overruns its budget has already
    cost the operator more waiting than the brief is worth. The model call runs on
    a worker thread so the budget is enforceable: without it a hung LiteLLM
    connection would block ``act`` forever, with the run neither paused nor
    written, and the executor is shut down without waiting so the abandoned call
    cannot hold the caller. Its late result, if it ever arrives, is discarded —
    which costs nothing, because the fallback brief is already correct.
    """
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="approval-brief")
    try:
        pending = pool.submit(
            deps.llm.structured,
            purpose="approval_brief",
            system=APPROVAL_BRIEF_SYSTEM,
            prompt=approval_brief_prompt(json.dumps(payload, default=str, sort_keys=True)[:8000]),
            schema=ApprovalBriefOutput,
        )
        return pending.result(timeout=BRIEF_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise BriefTimeout(f"brief exceeded {BRIEF_TIMEOUT_SECONDS}s") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def render_brief(payload: dict[str, Any], deps: AgentDependencies) -> dict[str, Any]:
    """Synthesize a brief. Never raises; never inspects or sets routing fields."""
    cached = payload.get("brief")
    if isinstance(cached, dict) and cached.get("incident_summary"):
        return cached
    try:
        parsed = _synthesise(payload, deps)
        return parsed.model_dump(mode="json") | {"degraded": False}
    except Exception as exc:  # noqa: BLE001 — brief must degrade, never fail GET/resume
        return _fallback(payload, reason=type(exc).__name__)


__all__ = ["BRIEF_TIMEOUT_SECONDS", "BriefTimeout", "render_brief"]
