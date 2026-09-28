"""Publish a human solution after its immutable approval has been committed."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent.dependencies import get_agent_dependencies
from agent.knowledge_capture import (
    KnowledgeCaptureResult,
    capture_human_resolution,
    make_next_article_number,
)
from agent.policy import _as_int
from agent.servicenow import IncidentGateway, build_servicenow_backend
from agent.state import IncidentSnapshot
from agent.tools import RefusalExplainer, build_servicenow_tool_registry
from agent.tools.permissions import PermissionClass
from agent.tools.registry import PostgreSQLApprovalChecker, ToolRegistration
from app.clients.qdrant import get_qdrant_client
from app.core.config import get_settings, kb_publisher_settings
from app.publishing.servicenow_kb import ServiceNowKBClient, make_kb_publish_handler
from app.workers.sync_engine import (
    build_sync_database_url,
    create_sync_engine,
    create_sync_session_factory,
)


async def capture_approved_solution(
    *, execution_id: str, incident: IncidentSnapshot, solution: str
) -> KnowledgeCaptureResult | None:
    """Use the real approval checker and publisher on the committed decision.

    Callers pass the incident directly rather than an interrupt payload. Capture used
    to be reachable only from the paused-approval route, which had a payload to hand
    over, and that coupling is what confined learning to one of the several ways a
    human can supply a resolution. A straight-through draft has no interrupt at all,
    so accepting one taught the platform nothing.

    The caller's Approval row must exist first: the high-risk registry checks that
    row and its ``publish_kb_article`` scope before ServiceNow is touched.
    """
    settings = get_settings()
    kb_sys_id = settings.servicenow_kb_id
    deps = get_agent_dependencies()
    engine = create_sync_engine(build_sync_database_url(settings))
    kb_client = ServiceNowKBClient(kb_publisher_settings(settings))
    qdrant = get_qdrant_client()
    try:
        gateway = IncidentGateway(build_servicenow_backend, deps.tracer)
        registry = build_servicenow_tool_registry(
            gateway,
            approval_checker=PostgreSQLApprovalChecker(create_sync_session_factory(engine)),
            refusal_explainer=RefusalExplainer(deps.llm),
            extra_registrations=[
                ToolRegistration(
                    "publish_kb_article",
                    PermissionClass.HIGH_RISK,
                    make_kb_publish_handler(kb_client, kb_sys_id),
                )
            ],
        )
        return await capture_human_resolution(
            execution_id=execution_id,
            incident=incident,
            solution_text=solution,
            deps=replace(deps, tools=registry),
            next_number=make_next_article_number(kb_client, kb_sys_id),
            qdrant_client=qdrant,
            kb_sys_id=kb_sys_id,
        )
    finally:
        await kb_client.aclose()
        qdrant.close()
        engine.dispose()


def snapshot_from_incident(incident: Any) -> IncidentSnapshot:
    """Adapt a ServiceNow ``Incident`` to the snapshot capture reasons over.

    Both decision routes hold a real ``Incident``; capture only needs the fields the
    article is composed from, so this is the one place that mapping lives instead of
    each route hand-rolling it.

    The client ``Incident`` model declares neither ``impact``, ``urgency`` nor
    ``service``: each is present only when the read that produced it fetched that
    field. Missing or blank numeric fields coerce to ``None`` with the same
    convention the graph's load path uses, so an accepted suggestion can always
    be captured.
    """
    return IncidentSnapshot(
        sys_id=incident.sys_id,
        number=incident.number,
        short_description=incident.short_description or "",
        description=incident.description or "",
        priority=_as_int(getattr(incident, "priority", None)),
        impact=_as_int(getattr(incident, "impact", None)),
        urgency=_as_int(getattr(incident, "urgency", None)),
        category=incident.category or "",
        # None means "no service was set", which the composer already maps to
        # "general".
        service=getattr(incident, "service", None),
        ai_enabled=bool(incident.ai_enabled),
        ai_human_lock=incident.ai_human_lock,
    )


__all__ = ["capture_approved_solution", "snapshot_from_incident"]
