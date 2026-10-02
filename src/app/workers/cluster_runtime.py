"""Bridge immutable webhook events, governed graph runs and durable cache waiters."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from agent.dependencies import get_agent_dependencies
from agent.policy import snapshot_incident
from agent.state import EventPayload
from agent.tools import ToolCallContext
from app.models.execution_log import ExecutionAction, ExecutionLogCreatePayload, ExecutionStatus
from app.models.incident import AIProcessingState, IncidentUpdatePayload
from app.workers.db import WorkerRepo
from app.workers.producer import send_incident_event
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


def _safe_async_run(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def apply_follower_cluster_resolution(
    payload: dict[str, Any],
    solution: dict[str, Any],
    execution_id: str,
    correlation_id: str,
) -> None:
    """Apply the leader's resolution to a follower incident without executing LLMs (0x LLM)."""
    deps = get_agent_dependencies()
    if not deps.settings.agent_write_back_enabled:
        return

    sys_id = payload.get("sys_id")
    if not sys_id:
        return

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
            _safe_async_run(
                deps.tools.invoke(
                    "write_ai_fields",
                    context=tool_context,
                    arguments={"sys_id": sys_id, "payload": update_payload},
                )
            )
        except Exception as exc:
            logger.warning(
                "follower_cluster_write_back_failed",
                execution_id=execution_id,
                sys_id=sys_id,
                error=str(exc),
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
            _safe_async_run(
                deps.tools.invoke(
                    "write_execution_log",
                    context=tool_context,
                    arguments={"sys_id": sys_id, "payload": log_payload},
                )
            )
        except Exception:
            pass
