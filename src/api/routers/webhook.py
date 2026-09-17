"""Incident ingestion webhook router for ServiceNow outbound events."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis

from api.auth import verify_bearer_token
from api.schemas.webhook import IncidentWebhookPayload, WebhookAcceptedResponse
from app.api.dependencies import (
    get_redis,
    get_session_factory,
)
from app.core.correlation import get_correlation_id
from app.db.redis.keys import INCIDENT_EVENTS_QUEUE
from app.db.session import SessionFactory
from app.exceptions.app_errors import ServiceUnavailableError
from app.repositories.idempotency import (
    EventAcceptanceStatus,
    InboundEvent,
    accept_inbound_event,
)

logger = structlog.getLogger("api.webhook")

router = APIRouter(
    prefix="/api/v1/webhook",
    tags=["Webhook"],
    dependencies=[Depends(verify_bearer_token)],
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
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> WebhookAcceptedResponse:
    """Ingest, validate, persist, and queue an incoming ServiceNow incident event."""
    correlation_id = get_correlation_id()

    # 1. Idempotent Database Persistence
    inbound = InboundEvent(
        event_id=payload.event_id,
        sys_id=payload.sys_id,
        number=payload.number,
        event_type=payload.event_type,
    )

    try:
        acceptance = await accept_inbound_event(session_factory, inbound)
    except Exception as exc:
        logger.error(
            "database_persistence_failed",
            event_id=payload.event_id,
            error=str(exc),
        )
        raise ServiceUnavailableError("Database unavailable to persist incoming event.") from exc

    is_duplicate = acceptance.status == EventAcceptanceStatus.DUPLICATE

    # 3. Redis Enqueue for New Events Only
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
            await redis_client.lpush(INCIDENT_EVENTS_QUEUE, payload.model_dump_json())
        except Exception as exc:
            logger.error(
                "redis_enqueue_failed",
                event_id=payload.event_id,
                error=str(exc),
            )
            raise ServiceUnavailableError("Event queue unavailable.") from exc

    # 4. Immediate HTTP 202 Response
    return WebhookAcceptedResponse(
        status="accepted",
        event_id=payload.event_id,
        correlation_id=correlation_id,
        idempotent_replay=is_duplicate,
    )
