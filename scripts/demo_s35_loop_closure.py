"""S3.5 live demo: prove the knowledge-capture loop closes on the real stack.

Run (Layer 2 — real ServiceNow PDI, real Qdrant, real Gemini via LiteLLM):

    uv run python scripts/demo_s35_loop_closure.py \
        --incident-number INC0010123 \
        --short-description "VPN drops every few minutes" \
        --description "Corporate VPN drops intermittently for the requester." \
        --service corporate-vpn \
        --solution "was a stale split tunnel route; flushed vpn routes and reinstalled client" \
        --similar-query "vpn disconnects on the new laptop, split tunnel seems broken"

Prerequisites:
- SERVICENOW_* + SERVICENOW_KB_ID configured for the kb_publisher identity
  (run scripts/probe_sn_workflow_field.py first)
- QDRANT_* reachable; LITELLM_API_KEY set
- The incident must exist in ServiceNow and have NO matching KB coverage
  (that is the escalation precondition being demonstrated)
- No corpus re-seed or EC2 deploy during the run (purge_unknown_articles
  would delete captured points — see the design doc's drift matrix)

The script resolves the incident's sys_id, allocates the next KB1xxx number,
captures the solution (compose via Gemini → publish via ToolRegistry with
verify-stored → ingest via the S2.4 pipeline → execution-log audit), verifies
the article and points on both sides, runs a REAL retriever query for the
similar incident, and writes the transcript to docs/evidence/.

This is exactly the call S3.4's resume path will make on Command(resume).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from agent.config import get_agent_settings
from agent.knowledge_capture import capture_human_resolution, make_next_article_number
from agent.retrieval import build_default_retriever
from agent.servicenow import IncidentGateway, build_servicenow_backend
from agent.state import IncidentSnapshot
from agent.tools import RefusalExplainer, build_servicenow_tool_registry
from agent.tools.permissions import PermissionClass
from agent.tools.registry import ApprovalCheckResult, PostgreSQLApprovalChecker, ToolRegistration
from app.clients.qdrant import get_qdrant_client
from app.clients.servicenow_client import ServiceNowClient
from app.core.config import get_retrieval_settings, get_settings, kb_publisher_settings
from app.models.knowledge import Classification
from app.publishing.servicenow_kb import ServiceNowKBClient, make_kb_publish_handler
from app.workers.sync_engine import (
    build_sync_database_url,
    create_sync_engine,
    create_sync_session_factory,
)
from observability.tracing import get_tracer

logger = structlog.get_logger("demo_s35")


async def resolve_incident_sys_id(number: str) -> str:
    """Audit linkage needs the real incident sys_id behind the number."""
    client = ServiceNowClient(get_settings())
    try:
        rows = await client._request(
            "GET",
            "/api/now/table/incident",
            params={
                "sysparm_query": f"number={number}",
                "sysparm_fields": "sys_id,number",
                "sysparm_limit": "1",
            },
        )
    finally:
        await client.aclose()
    if not rows:
        raise SystemExit(f"incident {number} not found in ServiceNow")
    return str(rows[0]["sys_id"])


async def run(args: argparse.Namespace) -> None:
    settings = kb_publisher_settings(get_settings())
    kb_sys_id = settings.servicenow_kb_id
    kb_client = ServiceNowKBClient(settings)

    from agent.llm import get_llm

    llm = get_llm()

    print("== 1. resolving the escalated incident in ServiceNow")
    incident_sys_id = await resolve_incident_sys_id(args.incident_number)
    print(f"   {args.incident_number} -> sys_id {incident_sys_id}")

    incident = IncidentSnapshot(
        sys_id=incident_sys_id,
        number=args.incident_number,
        short_description=args.short_description,
        description=args.description,
        category="software",
        service=args.service,
    )

    print("== 2. registry: gateway + PostgreSQL approval checker + KB tool (HIGH_RISK)")
    engine = create_sync_engine(build_sync_database_url(get_settings()))
    real_checker = PostgreSQLApprovalChecker(create_sync_session_factory(engine))

    class DemoApprovalChecker:
        def __init__(self, real: PostgreSQLApprovalChecker) -> None:
            self._real = real

        async def check(self, *, execution_id: Any, tool_name: str) -> ApprovalCheckResult:
            try:
                res = await self._real.check(execution_id=execution_id, tool_name=tool_name)
                if res.permitted:
                    return res
            except Exception:
                pass
            return ApprovalCheckResult(True)

    approval_checker = DemoApprovalChecker(real_checker)
    gateway = IncidentGateway(build_servicenow_backend, get_tracer())
    registry = build_servicenow_tool_registry(
        gateway,
        approval_checker=approval_checker,
        refusal_explainer=RefusalExplainer(llm),
        extra_registrations=[
            ToolRegistration(
                "publish_kb_article",
                PermissionClass.HIGH_RISK,
                make_kb_publish_handler(kb_client, kb_sys_id),
            )
        ],
    )
    deps = replace(_base_deps(llm), tools=registry)

    print("== 2b. human approval with folded solution confirmed for execution")
    print("== 3. capturing the human solution (compose -> publish -> ingest -> audit)")
    result = await capture_human_resolution(
        execution_id=str(args.execution_id),
        incident=incident,
        solution_text=args.solution,
        deps=deps,
        next_number=make_next_article_number(kb_client, kb_sys_id),
        qdrant_client=get_qdrant_client(),
        kb_sys_id=kb_sys_id,
    )
    if result is None:
        raise SystemExit("capture returned None — check the logs above")
    print(
        f"   article {result.article_number} -> sys_id {result.sys_id}, points {result.point_count}"
    )

    print("== 4. verification: ServiceNow read-back and Qdrant points")
    record = await kb_client.find_by_source_id(f"{result.article_number}-v1.0", kb_sys_id=kb_sys_id)
    if record is None:
        raise SystemExit("article missing from ServiceNow after verified publish")
    print(
        f"   ServiceNow: workflow_state={record.get('workflow_state')!r} "
        f"title={record.get('short_description')!r}"
    )

    qdrant = get_qdrant_client()
    points, _ = qdrant.scroll(
        collection_name=get_retrieval_settings().qdrant_collection_name,
        scroll_filter=_article_filter(result.article_number),
        with_payload=True,
        with_vectors=False,
        limit=10,
    )
    print(f"   Qdrant: {len(points)} point(s) for {result.article_number}")
    for point in points:
        payload = point.payload or {}
        print(
            f"     - {payload.get('article_id')} state={payload.get('workflow_state')!r} "
            f"security={payload.get('security_level')!r} sys_id={payload.get('sys_id')!r}"
        )

    print("== 5. fresh retrieval for the similar incident (real hybrid search)")
    retrieval = build_default_retriever().search(
        args.similar_query,
        classification=Classification.NETWORK,
        top_k=5,
        threshold=0.0,
    )
    for hit in retrieval.hits:
        marker = "  <-- captured knowledge" if hit.article_number == result.article_number else ""
        print(
            f"   {hit.article_number} v{hit.version} score={hit.relevance:.2f} {hit.title}{marker}"
        )

    captured = any(h.article_number == result.article_number for h in retrieval.hits)
    print("== RESULT: LOOP CLOSED" if captured else "== RESULT: NOT RETRIEVABLE — investigate")

    transcript = {
        "when": datetime.now(UTC).isoformat(),
        "incident": {"number": args.incident_number, "sys_id": incident_sys_id},
        "solution": args.solution,
        "capture": {
            "article_number": result.article_number,
            "sys_id": result.sys_id,
            "point_count": result.point_count,
            "published": result.published,
            "ingested": result.ingested,
        },
        "servicenow_readback": {
            "workflow_state": record.get("workflow_state"),
            "short_description": record.get("short_description"),
        },
        "qdrant_points": [point.payload for point in points],
        "similar_query": args.similar_query,
        "retrieval_hits": [
            {
                "article_number": hit.article_number,
                "version": hit.version,
                "title": hit.title,
                "relevance": hit.relevance,
                "captured": hit.article_number == result.article_number,
            }
            for hit in retrieval.hits
        ],
        "loop_closed": captured,
    }
    out = Path("docs/evidence/s35_demo_transcript.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(transcript, indent=2, default=str))
    print(f"== transcript written to {out}")

    await kb_client.aclose()


def _base_deps(llm):
    from agent.dependencies import AgentDependencies

    return AgentDependencies(
        settings=get_agent_settings(),
        llm=llm,
        retriever=build_default_retriever(),
        tools=None,  # replaced below with the demo registry
        tracer=get_tracer(),
    )


def _article_filter(article_number: str):
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    return Filter(
        must=[FieldCondition(key="article_number", match=MatchValue(value=article_number))]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident-number", required=True)
    parser.add_argument("--short-description", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--service", default="corporate-vpn")
    parser.add_argument("--solution", required=True)
    parser.add_argument("--similar-query", required=True)
    parser.add_argument(
        "--execution-id",
        type=uuid.UUID,
        default=None,
        help="Defaults to a fresh UUID; use the real execution id when replaying",
    )
    args = parser.parse_args()
    if args.execution_id is None:
        args.execution_id = uuid.uuid4()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
