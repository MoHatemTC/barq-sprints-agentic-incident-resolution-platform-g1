"""Dead-Letter Queue (DLQ) router for failed event inspection and operator replay."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Path, status
from redis.asyncio import Redis

from api.auth import require_role, verify_bearer_token
from api.schemas.dlq import DLQEventResponse, DLQReplayResponse
from app.api.dependencies import (
    get_app_settings,
    get_redis,
    get_sync_redis,
    get_sync_worker_repo,
)
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.exceptions.app_errors import (
    ConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.workers.db import WorkerRepo
from app.workers.replay import replay_event

logger = structlog.getLogger("api.dlq")

router = APIRouter(
    prefix="/api/v1/dlq",
    tags=["Dead-Letter Queue"],
)


@router.get(
    "",
    response_model=list[DLQEventResponse],
    status_code=status.HTTP_200_OK,
    summary="List all dead-lettered events",
    description="Retrieve all poisoned or retry-exhausted incident events currently in the DLQ.",
    dependencies=[Depends(verify_bearer_token)],
)
async def list_dlq_events(
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> list[DLQEventResponse]:
    """List dead-letter events currently accumulated in the DLQ."""
    logger.info("dlq_events_queried")
    raw_events = []
    if redis_client is not None:
        try:
            res = redis_client.lrange(INCIDENT_DLQ_QUEUE, 0, -1)
            raw_events = await res if inspect.isawaitable(res) else (res or [])
        except Exception as exc:
            logger.warning("dlq_redis_read_failed", error=str(exc))

    events: list[DLQEventResponse] = []
    if isinstance(raw_events, list):
        for raw in raw_events:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                data = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(data, dict):
                    continue
                event_id = str(data.get("event_id", ""))
                if not event_id:
                    continue

                failed_at = data.get("failed_at")
                if isinstance(failed_at, str):
                    try:
                        failed_at_dt = datetime.fromisoformat(failed_at)
                    except ValueError:
                        failed_at_dt = datetime.now(UTC)
                elif isinstance(failed_at, datetime):
                    failed_at_dt = failed_at
                else:
                    failed_at_dt = datetime.now(UTC)

                payload = data.get("payload")
                if not isinstance(payload, dict):
                    payload = {
                        k: v
                        for k, v in data.items()
                        if k not in ("failure_reason", "retry_count", "failed_at")
                    }

                events.append(
                    DLQEventResponse(
                        event_id=event_id,
                        payload=payload,
                        failure_reason=str(data.get("failure_reason", "unknown")),
                        retry_count=int(data.get("retry_count", 0)),
                        failed_at=failed_at_dt,
                    )
                )
            except Exception as exc:
                logger.warning("dlq_event_parse_error", error=str(exc))

    return events


@router.post(
    "/{event_id}/replay",
    response_model=DLQReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay a dead-lettered event",
    description="Replay a poisoned or failed event from the DLQ. Enforces Operator Role RBAC.",
    dependencies=[Depends(require_role("operator"))],
)
def replay_dlq_event(
    event_id: Annotated[
        str,
        Path(description="The unique event ID of the dead-lettered event to replay"),
    ],
    repo: Annotated[WorkerRepo, Depends(get_sync_worker_repo)],
    redis_client: Annotated[Any, Depends(get_sync_redis)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DLQReplayResponse:
    """Replay a DLQ event back into the processing queue.

    Enforces Operator Role RBAC ('X-User-Role: operator').
    Executes synchronously in AnyIO worker threadpool without blocking the event loop.
    Resets PostgreSQL retry-state, clears DLQ Redis records, and safely re-enqueues
    using the validated Celery task envelope.
    """
    correlation_id = get_correlation_id()
    logger.info("dlq_event_replay_accepted", event_id=event_id, correlation_id=correlation_id)

    try:
        outcome = replay_event(
            repo=repo,
            redis_client=redis_client,
            event_id=event_id,
            max_attempts=settings.worker_max_retries,
        )
    except (ResourceNotFoundError, ConflictError, ServiceUnavailableError):
        raise
    except Exception as exc:
        logger.error("dlq_replay_failed", event_id=event_id, error=str(exc))
        raise ServiceUnavailableError("Redis queue service unavailable for DLQ replay.") from exc

    if not outcome.replayed:
        reason = outcome.reason or "unknown"
        if "unknown event_id" in reason:
            raise ResourceNotFoundError(f"DLQ event not found: {event_id}")
        if "no execution exists" in reason:
            raise ResourceNotFoundError(f"No execution found for event: {event_id}")
        raise ConflictError(reason)

    return DLQReplayResponse(
        event_id=event_id,
        status="accepted",
        correlation_id=correlation_id,
        message=f"Event '{event_id}' replayed from DLQ into active queue.",
    )
