"""Dependency container for the graph (S2.5).

Nodes receive an :class:`AgentDependencies` and never construct clients, so tests
swap any of them. :func:`get_agent_dependencies` is the process-wide provider the
Celery worker uses; clients are created lazily, after the prefork fork.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache

from agent.config import AgentSettings, get_agent_settings
from agent.llm import LLMClient, get_llm
from agent.retrieval import Retriever, build_default_retriever
from agent.servicenow import IncidentGateway, build_servicenow_backend
from observability.tracing import Tracer, get_tracer


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentDependencies:
    settings: AgentSettings
    llm: LLMClient
    retriever: Retriever
    servicenow: IncidentGateway
    tracer: Tracer
    clock: Callable[[], datetime] = field(default=utc_now)


@lru_cache
def get_agent_dependencies() -> AgentDependencies:
    tracer = get_tracer()
    return AgentDependencies(
        settings=get_agent_settings(),
        llm=get_llm(),
        retriever=build_default_retriever(),
        servicenow=IncidentGateway(build_servicenow_backend, tracer),
        tracer=tracer,
    )


__all__ = ["AgentDependencies", "get_agent_dependencies", "utc_now"]
