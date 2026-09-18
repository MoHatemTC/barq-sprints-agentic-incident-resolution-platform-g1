"""Dead-Letter Queue (DLQ) router for failed event inspection and operator replay.

The only correct replay path is :func:`app.workers.replay.replay_event`.  It:

1. Reads the ORIGINAL payload from the immutable ``events`` table in Postgres.
2. Guards against replaying live/running events (returns ``replayed=False``).
3. Atomically resets Postgres retry-state  ``exhausted|cancelled → ready``
   and execution ``status → queued``.
4. Removes all DLQ Redis records for the event.
5. Re-enqueues via :func:`app.workers.producer.send_incident_event`, which
   produces a valid Celery task envelope (never raw JSON).

The old Sprint-2.1 implementation skipped steps 1–3 and 5, causing
``KeyError: 'properties'`` crashes in the Celery worker.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
from typing import Annotated

import redis as sync_redis_lib
import structlog
from fastapi import APIRouter, Depends, Path, Request, status
from redis.asyncio import Redis

from api.auth import require_role, verify_bearer_token
from api.schemas.dlq import DLQEventResponse, DLQReplayResponse
from app.api.dependencies import get_app_settings, get_redis
from app.core.config import Settings
from app.core.correlation import get_correlation_id
from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.exceptions.app_errors import ConflictError, ResourceNotFoundError, ServiceUnavailableError
from app.workers.db import build_worker_repo
from app.workers.replay import ReplayOutcome, replay_event

logger = structlog.getLogger("api.dlq")

router = APIRouter(
    prefix="/api/v1/dlq",
    tags=["Dead-Letter Queue"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_sync_redis(settings: Settings) -> sync_redis_lib.Redis:
    """Thin synchronous Redis client used by the blocking replay_event worker
    call that runs in a thread pool via asyncio.to_thread."""
    password = None
    if settings.redis_password is not None:
        password = settings.redis_password.get_secret_value()
    return sync_redis_lib.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        decode_responses=True,
        socket_connect_timeout=5,
    )


async def _run_replay_in_thread(
    settings: Settings,
    event_id: str,
) -> ReplayOutcome:
    """Run the synchronous replay_event function in a thread pool so the
    async event-loop is not blocked by Postgres/Redis I/O."""

    def _blocking() -> ReplayOutcome:
        repo = build_worker_repo(settings)
        sync_redis = _build_sync_redis(settings)
        try:
            return replay_event(
                repo=repo,
                redis_client=sync_redis,
                event_id=event_id,
                max_attempts=settings.worker_max_retries,
            )
        finally:
            sync_redis.close()

    return await asyncio.to_thread(_blocking)


# ---------------------------------------------------------------------------
# GET /api/v1/dlq  — list dead-lettered events
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# POST /api/v1/dlq/{event_id}/replay  — replay one dead-lettered event
# ---------------------------------------------------------------------------


@router.post(
    "/{event_id}/replay",
    response_model=DLQReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay a dead-lettered event",
    description=(
        "Replay a poisoned or failed event from the DLQ. "
        "Resets PostgreSQL retry-state, removes DLQ Redis record(s), "
        "and re-enqueues via a valid Celery task envelope. "
        "Enforces Operator Role RBAC."
    ),
    dependencies=[Depends(require_role("operator"))],
)
async def replay_dlq_event(
    request: Request,
    event_id: str = Path(
        ..., description="The unique event ID of the dead-lettered event to replay"
    ),
) -> DLQReplayResponse:
    """Replay one DLQ event back into the Celery processing pipeline.

    Edge-cases handled:
    - 404  — event_id unknown (never ingested or never reached Postgres).
    - 409  — event is not parked: still queued/running/succeeded — refuse.
    - 503  — Postgres or Redis unreachable.
    """
    correlation_id = get_correlation_id()
    logger.info("dlq_event_replay_requested", event_id=event_id, correlation_id=correlation_id)

    settings: Settings = get_app_settings(request)

    try:
        outcome: ReplayOutcome = await _run_replay_in_thread(settings, event_id)
    except Exception as exc:
        logger.error(
            "dlq_replay_infrastructure_error",
            event_id=event_id,
            error=str(exc),
            correlation_id=correlation_id,
        )
        raise ServiceUnavailableError(
            "Infrastructure error during DLQ replay — Postgres or Redis unreachable."
        ) from exc

    if not outcome.replayed:
        reason = outcome.reason or "unknown"
        logger.warning(
            "dlq_replay_refused",
            event_id=event_id,
            reason=reason,
            correlation_id=correlation_id,
        )
        # Distinguish "event doesn't exist" from "event exists but is not parked"
        if "unknown event_id" in reason or "no execution exists" in reason:
            raise ResourceNotFoundError(
                f"Event '{event_id}' not found in the incident database. "
                "Only events that have been processed by the worker can be replayed."
            )
        # Event exists but is live / already succeeded — conflict
        raise ConflictError(
            f"Event '{event_id}' cannot be replayed: {reason}. "
            "Only events in 'exhausted' or 'cancelled' state can be replayed."
        )

    logger.info(
        "dlq_event_replayed",
        event_id=event_id,
        execution_id=str(outcome.execution_id),
        removed_records=outcome.removed_records,
        correlation_id=correlation_id,
    )

    return DLQReplayResponse(
        event_id=event_id,
        status="replayed",
        correlation_id=correlation_id,
        message=(
            f"Event '{event_id}' replayed: PostgreSQL state reset to queued, "
            f"{outcome.removed_records} DLQ record(s) removed, "
            "re-enqueued via Celery task envelope."
        ),
    )
