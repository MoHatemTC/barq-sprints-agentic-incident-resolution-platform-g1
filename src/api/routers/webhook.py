"""Incident ingestion webhook router for ServiceNow outbound events."""
from app.exceptions.app_errors import ServiceUnavailableError
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
import secrets

from app.api.dependencies import get_app_settings, get_db_session, get_redis
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.exceptions.app_errors import AuthenticationError
from api.schemas.webhook import IncidentWebhookPayload, WebhookAcceptedResponse
from app.db.redis.keys import INCIDENT_EVENTS_QUEUE

from sqlalchemy.exc import IntegrityError
from app.db.schemas.in_bound_event import InboundEvent

logger = structlog.getLogger("api.webhook")

router = APIRouter(prefix="/api/v1/webhook", tags=["Webhook"])


@router.post(
    "/incident",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAcceptedResponse,
    summary="Ingest inbound ServiceNow incident event",
    description="Validates Outbound Event Contract v1, persists idempotently, and enqueues to Redis.",
)
async def ingest_incident_webhook(
    payload: IncidentWebhookPayload,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    redis_client: Redis = Depends(get_redis),
    settings: Settings = Depends(get_app_settings),
) -> WebhookAcceptedResponse:
    """Ingest, validate, persist, and queue an incoming ServiceNow incident event."""
    correlation_id = get_correlation_id()

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid Bearer token")

    token = auth_header[7:]
    if not secrets.compare_digest(token, settings.webhook_auth_token):
        raise AuthenticationError("Invalid Bearer token")


    event_record = InboundEvent(
        event_id=payload.event_id,
        sys_id=payload.sys_id,
        number=payload.number,
        event_type=payload.event_type,
        payload=payload.model_dump()
    )

    is_duplicate = False
    try:
        db.add(event_record)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        is_duplicate = True
        logger.info("Duplicate event")
    except Exception as exc:
        await db.rollback()
        raise ServiceUnavailableError("Database unavailable to persist incoming event.")
    
    if not is_duplicate:
        await redis_client.lpush(INCIDENT_EVENTS_QUEUE, json.dumps(payload.model_dump()))

    return WebhookAcceptedResponse(
        status="accepted",
        event_id=payload.event_id,
        correlation_id=correlation_id,
        idempotent_replay=is_duplicate,
    )
