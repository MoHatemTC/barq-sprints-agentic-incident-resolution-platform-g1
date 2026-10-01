"""Bridge immutable webhook events, governed graph runs and durable cache waiters."""

from __future__ import annotations

import asyncio

from agent.dependencies import get_agent_dependencies
from agent.policy import snapshot_incident
from agent.state import EventPayload
from agent.tools import ToolCallContext
from app.workers.db import WorkerRepo
from app.workers.producer import send_incident_event
from observability.redaction import redact_text


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
    payload["_prefetched_incident"] = raw
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
