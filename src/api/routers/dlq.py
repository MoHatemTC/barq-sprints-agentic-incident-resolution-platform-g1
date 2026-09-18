"""Dead-Letter Queue (DLQ) router for failed event inspection and operator replay."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Path, status

from api.auth import require_role, verify_bearer_token
from api.schemas.dlq import DLQEventResponse, DLQReplayResponse
from app.core.correlation import get_correlation_id

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
async def list_dlq_events() -> list[DLQEventResponse]:
    """List dead-letter events.

    Under Sprint 2 contract stub semantics, returns an empty list when operating without
    dead-lettered events accumulated by background workers.
    """
    logger.info("dlq_events_queried")
    return []


@router.post(
    "/{event_id}/replay",
    response_model=DLQReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay a dead-lettered event",
    description="Replay a poisoned or failed event from the DLQ. Enforces Operator Role RBAC.",
    dependencies=[Depends(require_role("operator"))],
)
async def replay_dlq_event(
    event_id: str = Path(..., description="The unique event ID of the dead-lettered event to replay"),
) -> DLQReplayResponse:
    """Replay a DLQ event back into the processing queue.

    Enforces Operator Role RBAC ('X-User-Role: operator'). Returns HTTP 403 Forbidden for
    non-operator callers.
    """
    correlation_id = get_correlation_id()
    logger.info("dlq_event_replay_accepted", event_id=event_id, correlation_id=correlation_id)

    return DLQReplayResponse(
        event_id=event_id,
        status="accepted",
        correlation_id=correlation_id,
        message=f"Event '{event_id}' accepted for DLQ replay.",
    )
