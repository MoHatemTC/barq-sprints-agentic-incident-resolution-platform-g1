"""Dead-Letter Queue (DLQ) router for failed event inspection and operator replay."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Path, status
from redis.asyncio import Redis

from api.auth import require_role, verify_bearer_token
from api.schemas.dlq import DLQEventResponse, DLQReplayResponse
from app.api.dependencies import get_redis
from app.core.correlation import get_correlation_id
from app.db.redis.keys import INCIDENT_DLQ_QUEUE, INCIDENT_EVENTS_QUEUE
from app.exceptions.app_errors import ServiceUnavailableError

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
async def replay_dlq_event(
    event_id: str = Path(
        ..., description="The unique event ID of the dead-lettered event to replay"
    ),
    redis_client: Annotated[Redis | None, Depends(get_redis)] = None,
) -> DLQReplayResponse:
    """Replay a DLQ event back into the processing queue.

    Enforces Operator Role RBAC ('X-User-Role: operator'). Pops the event from the dead-letter
    queue and re-enqueues it into the active incident events queue.
    """
    correlation_id = get_correlation_id()
    logger.info("dlq_event_replay_accepted", event_id=event_id, correlation_id=correlation_id)

    replayed = False
    if redis_client is not None:
        try:
            lrange_res = redis_client.lrange(INCIDENT_DLQ_QUEUE, 0, -1)
            raw_events = await lrange_res if inspect.isawaitable(lrange_res) else (lrange_res or [])
            if isinstance(raw_events, list):
                for raw in raw_events:
                    raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    data = json.loads(raw_str) if isinstance(raw_str, str) else raw_str
                    if isinstance(data, dict) and str(data.get("event_id")) == event_id:
                        original_payload = data.get("payload", data)
                        payload_json = (
                            json.dumps(original_payload)
                            if not isinstance(original_payload, str)
                            else original_payload
                        )

                        # Atomic Redis transaction: LPUSH and LREM execute
                        # together atomically (MULTI/EXEC)
                        if hasattr(redis_client, "pipeline"):
                            pipe = redis_client.pipeline(transaction=True)
                            if inspect.isawaitable(pipe):
                                pipe = await pipe
                            pipe.lpush(INCIDENT_EVENTS_QUEUE, payload_json)
                            pipe.lrem(INCIDENT_DLQ_QUEUE, 1, raw_str)
                            exec_res = pipe.execute()
                            if inspect.isawaitable(exec_res):
                                await exec_res
                        else:
                            lpush_res = redis_client.lpush(INCIDENT_EVENTS_QUEUE, payload_json)
                            if inspect.isawaitable(lpush_res):
                                await lpush_res
                            lrem_res = redis_client.lrem(INCIDENT_DLQ_QUEUE, 1, raw_str)
                            if inspect.isawaitable(lrem_res):
                                await lrem_res

                        replayed = True
                        logger.info("dlq_event_re_enqueued", event_id=event_id)
                        break
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            logger.exception("dlq_replay_redis_error", event_id=event_id, error=str(exc))
            raise ServiceUnavailableError(
                "Redis queue service unavailable for DLQ replay."
            ) from exc

    message = (
        f"Event '{event_id}' replayed from DLQ into active queue."
        if replayed
        else f"Event '{event_id}' accepted for DLQ replay."
    )

    return DLQReplayResponse(
        event_id=event_id,
        status="accepted",
        correlation_id=correlation_id,
        message=message,
    )
