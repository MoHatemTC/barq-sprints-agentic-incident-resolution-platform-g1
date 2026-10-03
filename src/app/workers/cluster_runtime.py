"""Bridge immutable webhook events, governed graph runs and durable cache waiters."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from agent.config import get_agent_settings
from agent.dependencies import get_agent_dependencies
from agent.errors import HumanLockedError
from agent.guardrails.input_screening import screen_text
from agent.policy import assess_risk, check_eligibility, snapshot_incident
from agent.state import ClassificationResult, EventPayload, RiskLevel
from agent.tools import ToolCallContext
from app.models.execution_log import ExecutionAction, ExecutionLogCreatePayload, ExecutionStatus
from app.models.incident import AIProcessingState, IncidentUpdatePayload
from app.models.knowledge import Classification
from app.utils.async_bridge import run_blocking
from app.workers.db import WorkerRepo
from app.workers.producer import send_incident_event
from app.workers.retry_policy import TerminalError
from observability.redaction import redact_text

logger = structlog.getLogger(__name__)


def load_cluster_incident(payload: dict, execution_id: str, correlation_id: str) -> dict:
    # The webhook contains identifiers only. Never trust descriptions supplied
    # in a broker payload in place of the integration user's governed read.
    event = EventPayload.model_validate(payload)
    deps = get_agent_dependencies()
    raw = asyncio.run(
        deps.tools.invoke(
            "read_incident",
            context=ToolCallContext(execution_id=execution_id, correlation_id=correlation_id),
            arguments={"sys_id": event.sys_id},
        )
    )
    payload["prefetched_incident"] = raw
    incident = snapshot_incident(raw)
    if not incident.ai_enabled or incident.ai_human_lock is not False:
        return {}
    return incident.model_copy(
        update={
            "short_description": redact_text(incident.short_description),
            "description": redact_text(incident.description),
        }
    ).model_dump(mode="json")


def dispatch_cluster_waiters(repo: WorkerRepo) -> int:
    """Periodic at-least-once dispatch; a broker failure retains durable ready rows."""
    count = 0
    for waiter in repo.ready_cluster_waiters():
        send_incident_event(waiter["payload"], waiter["execution_id"], waiter["correlation_id"])
        count += 1
    return count


def cacheable_result(result: dict) -> bool:
    return bool(
        result.get("processing_state") == "complete"
        and result.get("write_back") == "written"
        and result.get("cache_draft")
    )


@dataclass(frozen=True, slots=True)
class ReuseVerdict:
    """Whether a follower may take a cluster's resolution without running the graph."""

    allowed: bool
    reason: str


def follower_reuse_verdict(
    raw_incident: Mapping[str, Any], solution: Mapping[str, Any]
) -> ReuseVerdict:
    """Decide, with no LLM call, whether this follower may reuse the leader's resolution.

    Clustering only says two incidents *read* alike. It says nothing about the follower's
    own priority, service tier, eligibility or text, and the PRD's safety rules are per
    incident: no high-risk action without a recorded approval (NFR-05), no work on an
    ineligible or locked incident (FR-03), injection screening before anything is applied
    (FR-18). So the follower is held to the same deterministic gates the graph applies
    (``check_eligibility``, pattern screening, ``assess_risk``) on its own record, and only
    a clean, low-risk follower skips the graph. Everything else returns ``allowed=False``
    and runs the full governed path, which still reuses the cached draft.
    """
    settings = get_agent_settings()
    try:
        snapshot = snapshot_incident(raw_incident)
    except Exception:  # noqa: BLE001 - an unreadable record is never auto-resolved
        return ReuseVerdict(False, "follower incident could not be read")

    eligibility = check_eligibility(
        snapshot,
        event_number=snapshot.number,
        supported_categories=settings.agent_supported_categories,
    )
    if not eligibility.eligible:
        return ReuseVerdict(False, "ineligible: " + "; ".join(eligibility.reasons))

    if screen_text(f"{snapshot.short_description}\n{snapshot.description}").flagged:
        return ReuseVerdict(False, "input screening flagged the incident text")

    try:
        label = Classification(str(solution.get("classification") or ""))
    except ValueError:
        return ReuseVerdict(False, "the leader recorded no usable classification")
    risk = assess_risk(
        snapshot,
        ClassificationResult(
            label=label, rationale="reused from the resolved cluster", model_confidence=1.0
        ),
        risk_priorities=settings.agent_risk_priorities,
    )
    if risk.level is not RiskLevel.LOW or risk.approval_required:
        return ReuseVerdict(False, f"risk {risk.level.value}: " + "; ".join(risk.reasons))
    return ReuseVerdict(True, "follower passed eligibility, screening and risk gates")


def apply_follower_cluster_resolution(
    payload: dict[str, Any],
    solution: dict[str, Any],
    execution_id: str,
    correlation_id: str,
    raw_incident: Mapping[str, Any] | None = None,
) -> list[str]:
    """Apply the leader's resolution to a follower incident without executing LLMs (0x LLM)."""
    deps = get_agent_dependencies()
    if not deps.settings.agent_write_back_enabled:
        return []

    sys_id = payload.get("sys_id")
    if not sys_id:
        return []

    work_note = (
        solution.get("work_note")
        or solution.get("summary")
        or "AI Suggested Response applied from resolved incident cluster."
    )
    resolution = (
        solution.get("resolution")
        or solution.get("suggestion")
        or (solution.get("cache_draft") or {}).get("rendered")
    )
    update_fields: dict[str, Any] = {
        "work_notes": work_note,
        "ai_processing_state": AIProcessingState.COMPLETE,
        "ai_agent_version": deps.settings.agent_version,
        "ai_human_review_required": False,
        "ai_processing_end": datetime.now(UTC),
    }
    if solution.get("classification"):
        update_fields["ai_classification"] = solution["classification"]
    if solution.get("confidence") is not None:
        update_fields["ai_confidence"] = solution["confidence"]
    if solution.get("suggestion") or resolution:
        update_fields["ai_suggestion"] = solution.get("suggestion") or resolution
    if resolution:
        update_fields["ai_resolution"] = resolution
    if solution.get("model_name"):
        update_fields["ai_model_name"] = solution["model_name"]

    update_payload = IncidentUpdatePayload(**update_fields)
    tool_context = ToolCallContext(execution_id=execution_id, correlation_id=correlation_id)
    with deps.tracer.span(
        "cluster_cache.resolve_follower",
        as_type="span",
        metadata={
            "execution_id": execution_id,
            "sys_id": sys_id,
            "cluster_role": "follower",
            "llm_calls": 0,
        },
    ):
        try:
            run_blocking(
                deps.tools.invoke(
                    "write_ai_fields",
                    context=tool_context,
                    arguments={"sys_id": sys_id, "payload": update_payload},
                )
            )
        except HumanLockedError as exc:
            # Same translation the graph applies: an analyst took over, so this run ends
            # without a write and without a retry.
            raise TerminalError(str(exc)) from exc
        # Any other failure propagates. Swallowing it here marked the follower
        # ``succeeded`` and its cluster membership applied with nothing written to
        # ServiceNow.

        steps: list[str] = []
        if raw_incident is not None and resolution:
            # The follower passed its own gates (follower_reuse_verdict), so it is
            # worked exactly like a low-risk incident the graph resolved itself.
            from agent.nodes.act import fulfil_applied_fix

            confidence = solution.get("confidence")
            steps = fulfil_applied_fix(
                deps,
                snapshot_incident(raw_incident),
                resolution=str(resolution),
                confidence=float(confidence) if isinstance(confidence, int | float) else None,
                context=tool_context,
            )

        log_payload = ExecutionLogCreatePayload(
            incident_sys_id=sys_id,
            execution_id=execution_id,
            agent=deps.settings.agent_version,
            action=ExecutionAction.PROPOSE,
            status=ExecutionStatus.SUCCEEDED,
            result=str(solution.get("summary") or "Resolved via cluster cache"),
        )
        try:
            run_blocking(
                deps.tools.invoke(
                    "write_execution_log",
                    context=tool_context,
                    arguments={"sys_id": sys_id, "payload": log_payload},
                )
            )
        except Exception as exc:  # noqa: BLE001 - the incident write already landed
            logger.warning(
                "follower_cluster_execution_log_failed",
                execution_id=execution_id,
                sys_id=sys_id,
                error=redact_text(str(exc))[:500],
            )
    return steps
