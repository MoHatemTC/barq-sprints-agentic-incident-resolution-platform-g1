"""Incident ingestion webhook router for ServiceNow outbound events."""

from __future__ import annotations

import asyncio
import secrets
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status

from api.schemas.webhook import IncidentWebhookPayload, WebhookAcceptedResponse
from app.api.dependencies import (
    get_app_settings,
    get_session_factory,
)
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.db.session import SessionFactory
from app.exceptions.app_errors import AuthenticationError, ServiceUnavailableError
from app.repositories.idempotency import (
    EventAcceptanceStatus,
    InboundEvent,
    accept_inbound_event,
)
from app.workers.producer import send_incident_event

logger = structlog.getLogger("api.webhook")

router = APIRouter(prefix="/api/v1/webhook", tags=["Webhook"])


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
    request: Request,
    session_factory: Annotated[SessionFactory, Depends(get_session_factory)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> WebhookAcceptedResponse:
    """Ingest, validate, persist, and queue an incoming ServiceNow incident event."""
    correlation_id = get_correlation_id()

    # 1. Bearer Token Authentication
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid Bearer token")

    token = auth_header[7:]
    if not secrets.compare_digest(token, settings.webhook_auth_token):
        raise AuthenticationError("Invalid Bearer token")

    # 2. Idempotent Database Persistence
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
            await asyncio.to_thread(
                send_incident_event,
                payload.model_dump(),
                str(acceptance.execution_id),
            )
        except Exception as exc:
            logger.error(
                "event_enqueue_failed",
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
