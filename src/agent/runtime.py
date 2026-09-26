"""Agent runtime bootstrap: one compiled graph per worker process (S2.5).

The Celery task calls :func:`invoke_incident_graph`. The first call in a process
builds the dependencies (LLM, embeddings, retriever, ServiceNow gateway, tracer),
the checkpointer and the compiled graph; later calls reuse them. Nothing is built
at import time, so prefork children create their own clients after the fork.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from agent.checkpointer import build_checkpointer
from agent.dependencies import AgentDependencies, get_agent_dependencies
from agent.graph import build_graph, run_graph
from agent.state import EventPayload
from app.workers.retry_policy import TerminalError


@dataclass(frozen=True)
class AgentRuntime:
    deps: AgentDependencies
    checkpointer: BaseCheckpointSaver[Any]
    graph: CompiledStateGraph[Any, Any, Any, Any]


def build_runtime(
    deps: AgentDependencies, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> AgentRuntime:
    saver = checkpointer or build_checkpointer(deps.settings.agent_checkpointer_backend)
    return AgentRuntime(deps=deps, checkpointer=saver, graph=build_graph(deps, checkpointer=saver))


@lru_cache
def get_runtime() -> AgentRuntime:
    return build_runtime(get_agent_dependencies())


def invoke_incident_graph(
    payload: dict[str, Any],
    *,
    execution_id: str,
    correlation_id: str,
    attempt: int,
    runtime: AgentRuntime | None = None,
    resume: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = runtime or get_runtime()
    try:
        event = EventPayload.model_validate(payload)
    except ValidationError as exc:
        raise TerminalError(
            f"event payload is not a valid v1 event: {exc.error_count()} errors"
        ) from exc
    return run_graph(
        runtime.graph,
        event,
        execution_id=execution_id,
        correlation_id=correlation_id,
        attempt=attempt,
        deps=runtime.deps,
        resume=resume,
    )


def resume_incident_graph(
    *,
    execution_id: str,
    decision: dict[str, Any],
    correlation_id: str,
    attempt: int = 1,
    runtime: AgentRuntime | None = None,
) -> dict[str, Any]:
    """Resume a paused thread with ``Command(resume=...)`` on the same checkpointer."""
    runtime = runtime or get_runtime()
    stored = runtime.deps.audit.get_interrupt(execution_id) or {}
    incident = stored.get("incident") or {}
    event = EventPayload(
        event_id=f"resume-{execution_id}",
        sys_id=str(incident.get("sys_id") or "0" * 32),
        number=str(incident.get("number") or "INC0"),
        event_type="incident.created",
    )
    return run_graph(
        runtime.graph,
        event,
        execution_id=execution_id,
        correlation_id=str(stored.get("correlation_id") or correlation_id),
        attempt=attempt,
        deps=runtime.deps,
        resume=decision,
    )


__all__ = [
    "AgentRuntime",
    "build_runtime",
    "get_runtime",
    "invoke_incident_graph",
    "resume_incident_graph",
]
