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
from agent.tools import RefusalExplainer, ToolRegistry, build_servicenow_tool_registry
from agent.tools.registry import PostgreSQLApprovalChecker
from app.core.config import get_settings
from app.workers.sync_engine import (
    build_sync_database_url,
    create_sync_engine,
    create_sync_session_factory,
)
from observability.tracing import Tracer, get_tracer


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentDependencies:
    settings: AgentSettings
    llm: LLMClient
    retriever: Retriever
    tools: ToolRegistry
    tracer: Tracer
    clock: Callable[[], datetime] = field(default=utc_now)


def build_production_tool_registry(
    gateway: IncidentGateway,
    llm: LLMClient,
) -> ToolRegistry:
    """Compose the trusted tool boundary with worker-safe production providers."""
    app_settings = get_settings()
    engine = create_sync_engine(build_sync_database_url(app_settings))
    approval_checker = PostgreSQLApprovalChecker(create_sync_session_factory(engine))
    return build_servicenow_tool_registry(
        gateway,
        approval_checker=approval_checker,
        refusal_explainer=RefusalExplainer(llm),
    )


@lru_cache
def get_agent_dependencies() -> AgentDependencies:
    tracer = get_tracer()
    llm = get_llm()
    gateway = IncidentGateway(build_servicenow_backend, tracer)
    return AgentDependencies(
        settings=get_agent_settings(),
        llm=llm,
        retriever=build_default_retriever(),
        tools=build_production_tool_registry(gateway, llm),
        tracer=tracer,
    )


__all__ = [
    "AgentDependencies",
    "build_production_tool_registry",
    "get_agent_dependencies",
    "utc_now",
]
