"""Knowledge capture (S3.5) — close the learning loop on the approval resume path.

When a human resolves an escalated incident with their own solution text, this
pipeline turns it into retrievable knowledge: compose (one LLM call) → publish
through the ToolRegistry (server-enforced, audited) → ingest via the existing
S2.4 pipeline (deterministic point IDs) → audit via the registered execution-log
tool. Fail rules are absolute: a refused or failed publish means zero Qdrant
writes; a failed ingest after a *verified* publish is drift — recorded loudly,
recoverable by re-running (idempotent), never a crash of the resolution itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from agent.article_composer import compose_article
from agent.dependencies import AgentDependencies
from agent.state import IncidentSnapshot
from agent.tools.registry import RegistryRefusalError, ToolCallContext
from app.models.execution_log import ExecutionAction, ExecutionLogCreatePayload, ExecutionStatus
from app.models.knowledge import Article
from app.retrieval.ingest import ingest_articles
from app.workers.retry_policy import RetryableError, TerminalError
from observability.redaction import redact_text

logger = structlog.get_logger(__name__)

#: Initial compose attempt plus exactly one retry for transient proxy errors.
COMPOSE_ATTEMPTS = 2

MIN_SOLUTION_CHARS = 3


@dataclass(frozen=True)
class KnowledgeCaptureResult:
    """What the loop produced, auditable end to end."""

    execution_id: str
    article_number: str
    sys_id: str
    point_count: int
    published: bool = True
    ingested: bool = True


def make_next_article_number(client: Any, kb_sys_id: str) -> Callable[[], Awaitable[str]]:
    """Allocator for the reserved KB1001–KB1999 human-captured range.

    Queries ``u_source_id`` with the ``KB1`` prefix (not ``KB10`` — KB11xx rows
    must count) and returns max+1, so gaps in the range are harmless. Concurrent
    captures can race; accepted at demo scale and documented in the design doc.
    """

    async def next_number() -> str:
        source_ids = await client.find_source_ids_by_prefix("KB1", kb_sys_id=kb_sys_id)
        numbers = [
            int(source_id.split("-")[0][2:])
            for source_id in source_ids
            if source_id.split("-")[0][2:].isdigit()
        ]
        return f"KB{max(numbers, default=1000) + 1}"

    return next_number


async def capture_human_resolution(
    *,
    execution_id: str,
    incident: IncidentSnapshot,
    solution_text: str,
    deps: AgentDependencies,
    next_number: Callable[[], Awaitable[str]],
    qdrant_client: Any,
    kb_sys_id: str,
) -> KnowledgeCaptureResult | None:
    """Capture a human's solution as a KB article and ingest it for retrieval.

    Returns ``None`` when nothing was captured (garbage solution, compose
    failure, publish refused/failed) — the human's resolution itself is never
    affected. The returned result reports exactly how far the pipeline got.
    """
    if not solution_text or len(solution_text.strip()) < MIN_SOLUTION_CHARS:
        logger.info(
            "knowledge_capture_skipped_empty_solution",
            execution_id=execution_id,
        )
        return None

    # 1. Compose. The endpoint already redacted the persisted solution; redact
    #    again defensively so the model never sees raw credentials either.
    article_number = await next_number()
    article = await _compose_with_retry(
        execution_id=execution_id,
        incident=incident,
        solution_text=redact_text(solution_text),
        deps=deps,
        article_number=article_number,
    )
    if article is None:
        return None

    # 2. Publish through the registry — server-enforced and audited. Refusal or
    #    failure means no Qdrant write, ever.
    try:
        sys_id = await deps.tools.invoke(
            "publish_kb_article",
            context=ToolCallContext(execution_id=execution_id),
            arguments={"article": article},
        )
    except RegistryRefusalError as exc:
        logger.error(
            "knowledge_capture_publish_refused",
            execution_id=execution_id,
            article_number=article.article_id,
            reason=exc.reason_code,
        )
        return None
    except Exception as exc:
        logger.error(
            "knowledge_capture_publish_failed",
            execution_id=execution_id,
            article_number=article.article_id,
            error=str(exc),
        )
        return None

    article.sys_id = str(sys_id)

    # 3. Ingest via the existing pipeline; deterministic point IDs make any
    #    re-run idempotent. A failure here is drift: ServiceNow has the article,
    #    Qdrant does not. Record it loudly; the resolution flow continues.
    point_count = 0
    ingested = False
    try:
        point_count = await asyncio.to_thread(ingest_articles, [article], qdrant_client)
        ingested = True
    except Exception as exc:
        logger.error(
            "knowledge_capture_drift",
            execution_id=execution_id,
            article_number=article.article_id,
            sys_id=article.sys_id,
            error=str(exc),
            remediation="re-run capture for this execution; ingestion is idempotent",
        )

    # 4. Audit through the registry, linking execution → article → points.
    await _audit(
        deps=deps,
        execution_id=execution_id,
        incident=incident,
        article=article,
        point_count=point_count,
        ingested=ingested,
    )

    logger.info(
        "knowledge_capture_completed",
        execution_id=execution_id,
        article_number=article.article_id,
        sys_id=article.sys_id,
        point_count=point_count,
        ingested=ingested,
    )
    return KnowledgeCaptureResult(
        execution_id=execution_id,
        article_number=article.article_number,
        sys_id=article.sys_id,
        point_count=point_count,
        published=True,
        ingested=ingested,
    )


async def _compose_with_retry(
    *,
    execution_id: str,
    incident: IncidentSnapshot,
    solution_text: str,
    deps: AgentDependencies,
    article_number: str,
) -> Article | None:
    for attempt in range(1, COMPOSE_ATTEMPTS + 1):
        try:
            # The worker client is synchronous; keep the event loop free.
            return await asyncio.to_thread(
                compose_article,
                incident,
                solution_text,
                deps=deps,
                article_number=article_number,
            )
        except RetryableError as exc:
            if attempt == COMPOSE_ATTEMPTS:
                logger.error(
                    "knowledge_capture_compose_failed",
                    execution_id=execution_id,
                    error=str(exc),
                    attempts=attempt,
                )
                return None
            logger.warning("knowledge_capture_compose_retry", error=str(exc))
        except (TerminalError, ValueError) as exc:
            logger.error(
                "knowledge_capture_compose_failed",
                execution_id=execution_id,
                error=str(exc),
                attempts=attempt,
            )
            return None
    return None


async def _audit(
    *,
    deps: AgentDependencies,
    execution_id: str,
    incident: IncidentSnapshot,
    article: Article,
    point_count: int,
    ingested: bool,
) -> None:
    payload = ExecutionLogCreatePayload(
        incident_sys_id=incident.sys_id,
        execution_id=execution_id,
        agent="knowledge_capture",
        action=ExecutionAction.EXECUTE,
        status=ExecutionStatus.SUCCEEDED if ingested else ExecutionStatus.BLOCKED,
        result=(
            f"knowledge capture: article {article.article_id} published "
            f"(sys_id {article.sys_id}); {point_count} qdrant points ingested"
            + ("" if ingested else " — INGESTION DRIFT, re-run capture to repair")
        ),
    )
    try:
        await deps.tools.invoke(
            "write_execution_log",
            context=ToolCallContext(execution_id=execution_id),
            arguments={"sys_id": incident.sys_id, "payload": payload},
        )
    except Exception as exc:
        # Audit failure must never fail the capture; the structured log above
        # already carries the full linkage.
        logger.error("knowledge_capture_audit_failed", execution_id=execution_id, error=str(exc))


__all__ = [
    "KnowledgeCaptureResult",
    "capture_human_resolution",
    "make_next_article_number",
]
