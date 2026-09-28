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
    *, execution_id: str, interrupt_payload: dict[str, Any], solution: str
) -> KnowledgeCaptureResult | None:
    """Use the real approval checker and publisher on the committed decision.

    The endpoint invokes this only for an approved no-evidence or low-confidence
    interrupt. Its approval row must exist first: the high-risk registry checks
    that row and its ``publish_kb_article`` scope before ServiceNow is touched.
    """
    settings = get_settings()
    kb_sys_id = settings.servicenow_kb_id
    incident = IncidentSnapshot.model_validate(interrupt_payload["incident"])
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


__all__ = ["capture_approved_solution"]
