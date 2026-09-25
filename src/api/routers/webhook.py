"""Incident ingestion webhook router for ServiceNow outbound events."""

from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy import delete
from sqlalchemy.exc import MissingGreenlet, SQLAlchemyError

from api.auth import verify_webhook_oauth_token
from api.schemas.webhook import IncidentWebhookPayload, WebhookAcceptedResponse
from app.api.dependencies import get_session_factory
from app.core.correlation import get_correlation_id
from app.db.models import Event
from app.db.session import SessionFactory
from app.exceptions.app_errors import ServiceUnavailableError
from app.repositories.idempotency import (
    EventAcceptanceStatus,
    InboundEvent,
    accept_inbound_event,
)
from app.workers.producer import send_incident_event
from observability.tracing import get_tracer

logger = structlog.getLogger("api.webhook")

# The bearer check is a router-level dependency, so FastAPI resolves it before the
# handler body runs. That ordering is load-bearing: an unauthenticated caller must
# never reach the tracer and create a trace.
router = APIRouter(
    prefix="/api/v1/webhook",
    tags=["Webhook"],
    dependencies=[Depends(verify_webhook_oauth_token)],
)


@router.post(
    "/incident",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAcceptedResponse,
    summary="Ingest inbound ServiceNow incident event",
    description=(
        "Validates Outbound Event Contract v1, persists idempotently, and enqueues to Redis."
    ),
)
async def ingest_incident_webhook(
    payload: IncidentWebhookPayload,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
) -> WebhookAcceptedResponse:
    """Ingest, validate, persist, and queue an incoming ServiceNow incident event."""
    correlation_id = get_correlation_id()
    tracer = get_tracer()
    with (
        tracer.span(
            "webhook.receipt",
            correlation_id=correlation_id,
            input={
                "event_id": payload.event_id,
                "number": payload.number,
                "event_type": payload.event_type,
            },
            metadata={"incident_number": payload.number, "event_id": payload.event_id},
        ) as receipt,
        tracer.trace_attributes(correlation_id=correlation_id, incident_number=payload.number),
    ):
        response = await _ingest(payload, session_factory, correlation_id)
        receipt.update(output=response.model_dump(mode="json"))
        return response


async def _ingest(
    payload: IncidentWebhookPayload,
    session_factory: SessionFactory,
    correlation_id: str,
) -> WebhookAcceptedResponse:
    tracer = get_tracer()

    # 1. Idempotent Database Persistence
    inbound = InboundEvent(
        event_id=payload.event_id,
        sys_id=payload.sys_id,
        number=payload.number,
        event_type=payload.event_type,
    )

    try:
        acceptance = await accept_inbound_event(session_factory, inbound)
    except MissingGreenlet:
        raise
    except (SQLAlchemyError, ConnectionError, TimeoutError, OSError) as exc:
        logger.exception(
            "database_persistence_failed",
            event_id=payload.event_id,
            error=str(exc),
        )
        raise ServiceUnavailableError("Database unavailable to persist incoming event.") from exc

    is_duplicate = acceptance.status == EventAcceptanceStatus.DUPLICATE

    # 3. Enqueue for New Events Only — via the worker producer, the single
    # owner of the Celery envelope format. to_thread keeps the sync broker
    # publish off the event loop (NFR-01: 500ms p95).
    if is_duplicate:
        logger.info(
            "duplicate_incident_event_ignored",
            event_id=payload.event_id,
        )
    else:
        logger.info(
            "incident_event_accepted",
            event_id=payload.event_id,
            execution_id=str(acceptance.execution_id),
        )
        try:
            with tracer.span(
                "queue.enqueue",
                metadata={
                    "execution_id": str(acceptance.execution_id),
                    "incident_number": payload.number,
                },
            ):
                await asyncio.to_thread(
                    send_incident_event,
                    payload.model_dump(),
                    str(acceptance.execution_id),
                    correlation_id=correlation_id,
                )
        except Exception as exc:
            logger.exception(
                "event_enqueue_failed",
                event_id=payload.event_id,
                error=str(exc),
            )
            # Compensate database persistence so client retry after 503
            # is not blocked as a false duplicate
            if acceptance.event_record_id:
                try:
                    async with session_factory() as cleanup_session:
                        await cleanup_session.execute(
                            delete(Event).where(Event.id == acceptance.event_record_id)
                        )
                        await cleanup_session.commit()
                    logger.info(
                        "database_claim_compensated",
                        event_id=payload.event_id,
                        event_record_id=str(acceptance.event_record_id),
                    )
                except Exception as cleanup_exc:
                    logger.warning(
                        "database_compensation_failed",
                        event_id=payload.event_id,
                        error=str(cleanup_exc),
                    )
            raise ServiceUnavailableError("Event queue unavailable.") from exc

    # 4. Immediate HTTP 202 Response
    return WebhookAcceptedResponse(
        status="accepted",
        event_id=payload.event_id,
        correlation_id=correlation_id,
        idempotent_replay=is_duplicate,
    )
