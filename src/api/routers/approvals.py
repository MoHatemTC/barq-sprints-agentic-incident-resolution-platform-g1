"""Human-in-the-Loop (HITL) approvals router (S3.4 interrupt/resume)."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select, update
from sqlalchemy.exc import MissingGreenlet, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent.audit_store import GraphAuditStore, build_audit_store
from agent.runtime import resume_incident_graph
from api.auth import verify_bearer_token
from api.schemas.approvals import (
    ApprovalDecisionRequest,
    ApprovalResponse,
)
from app.api.dependencies import get_db_session
from app.db.models import Approval, Execution, RetryState
from app.exceptions.app_errors import (
    ConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)

logger = structlog.getLogger("api.approvals")

router = APIRouter(
    prefix="/api/v1",
    tags=["Approvals"],
    dependencies=[Depends(verify_bearer_token)],
)


@lru_cache
def get_audit_store() -> GraphAuditStore:
    """One store per process: building one per request would open an engine each time."""
    return build_audit_store()


@router.get(
    "/approvals",
    response_model=list[ApprovalResponse],
    status_code=status.HTTP_200_OK,
    summary="List all approval decisions",
    description=(
        "Fetch recorded human approval decisions, "
        "optionally filtered by execution ID or deciding operator."
    ),
)
async def list_approvals(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    execution_id: Annotated[
        UUID | None,
        Query(
            description="Optional filter by execution ID",
        ),
    ] = None,
    decided_by: Annotated[
        str | None,
        Query(
            description="Optional filter by deciding operator user ID",
        ),
    ] = None,
) -> list[ApprovalResponse]:
    """Retrieve all approval records from PostgreSQL approvals table."""
    query = select(Approval).order_by(Approval.decided_at.desc())
    if execution_id is not None:
        query = query.where(Approval.execution_id == execution_id)
    if decided_by is not None:
        query = query.where(Approval.decided_by == decided_by)

    try:
        result = await db.execute(query)
        approvals = result.scalars().all()
    except MissingGreenlet:
        raise
    except SQLAlchemyError as exc:
        logger.exception("database_query_failed", error=str(exc))
        raise ServiceUnavailableError("Database unavailable to query approvals.") from exc

    return [ApprovalResponse.model_validate(row) for row in approvals]


@router.get(
    "/approvals/{id}",
    response_model=ApprovalResponse,
    status_code=status.HTTP_200_OK,
    summary="Get approval details by ID",
    description="Fetch a specific human approval record by its unique ID.",
)
async def get_approval(
    id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApprovalResponse:
    """Retrieve an approval record by its primary key ID, else the paused interrupt."""
    try:
        approval = await db.get(Approval, id)
    except MissingGreenlet:
        raise
    except SQLAlchemyError as exc:
        logger.exception("database_query_failed", approval_id=str(id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to retrieve approval.") from exc

    if not approval:
        raise ResourceNotFoundError(f"Approval '{id}' not found")

    return ApprovalResponse.model_validate(approval)


@router.get(
    "/approvals/pending/{execution_id}",
    response_model=ApprovalResponse,
    status_code=status.HTTP_200_OK,
    summary="Read the approval brief awaiting a decision",
    description=(
        "Return the persisted interrupt payload for a paused execution: the "
        "Approval Brief Agent's summary alongside the raw facts it was written "
        "from (NFR-07). Nothing here has been decided yet."
    ),
)
async def get_pending_approval(execution_id: UUID) -> ApprovalResponse:
    """The reviewer's view of a paused thread (S3.4): brief + raw evidence."""
    try:
        payload = get_audit_store().get_interrupt(str(execution_id))
    except SQLAlchemyError as exc:
        logger.exception("interrupt_read_failed", execution_id=str(execution_id), error=str(exc))
        raise ServiceUnavailableError("Audit store unavailable to read the interrupt.") from exc

    if payload is None:
        raise ResourceNotFoundError(f"No paused interrupt found for execution '{execution_id}'")

    return _paused_response(execution_id, payload)


@router.post(
    "/approvals/{id}/decide",
    response_model=ApprovalResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit human operator approval decision and resume graph (S3.4)",
    description=(
        "Resume a paused LangGraph thread with Command(resume=...), "
        "then record the approval decision for audit. The id is the execution_id."
    ),
)
async def decide_approval(
    id: UUID,
    payload: ApprovalDecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApprovalResponse:
    """Submit an approval decision and resume the exact paused execution.

    The id is the execution_id of a paused thread. Ordering matters: the decision
    is applied to the graph *before* it is recorded, because a decision that could
    not be applied must stay retryable — recording it first would make the retry
    hit the immutability conflict instead of resuming.
    """
    execution_id_str = str(id)

    # 1. Immutability check, then the execution this decision belongs to.
    try:
        existing = await db.get(Approval, id)
        if existing:
            logger.warning(
                "approval_already_decided",
                approval_id=str(id),
                decision=existing.decision,
            )
            raise ConflictError(
                f"Approval '{id}' has already been decided "
                f"('{existing.decision}') and is immutable."
            )
        execution = await db.get(Execution, id)
    except (ConflictError, MissingGreenlet):
        raise
    except SQLAlchemyError as exc:
        logger.exception("database_operation_failed", approval_id=str(id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to record approval decision.") from exc

    # 2. Resume the paused thread, if this execution is the one that paused.
    try:
        interrupt_payload = get_audit_store().get_interrupt(execution_id_str)
    except SQLAlchemyError as exc:
        logger.exception("interrupt_read_failed", execution_id=str(id), error=str(exc))
        raise ServiceUnavailableError("Audit store unavailable; decision not applied.") from exc

    resumed: dict[str, Any] | None = None
    if interrupt_payload is not None:
        decision = {
            "decision": payload.decision,
            "decided_by": payload.decided_by,
            "reason": payload.reason,
        }
        try:
            correlation_id = str(
                interrupt_payload.get("correlation_id") or f"resume-{execution_id_str}"
            )
            resumed = resume_incident_graph(
                execution_id=execution_id_str,
                decision=decision,
                correlation_id=correlation_id,
                attempt=1,
            )
            logger.info(
                "graph_resumed",
                execution_id=execution_id_str,
                decision=payload.decision,
                outcome=resumed.get("outcome"),
            )
        except Exception as exc:  # noqa: BLE001 — anything failing here must surface as 503
            logger.exception("graph_resume_failed", execution_id=str(id), error=str(exc))
            raise ServiceUnavailableError(f"Failed to resume graph: {str(exc)}") from exc

    # 3. Record the decision (immutable) and close the execution row it resumed.
    try:
        resolved_approval: Approval | None = None
        if execution is not None:
            now = datetime.now(UTC)
            resolved_approval = Approval(
                id=uuid4(),
                execution_id=execution.execution_id,
                decision=payload.decision,
                decided_by=payload.decided_by,
                reason=payload.reason,
                evidence=payload.evidence,
                decided_at=now,
            )
            db.add(resolved_approval)
            if resumed is not None:
                await _close_resumed_execution(db, id, resumed, now)
            await db.commit()
            await db.refresh(resolved_approval)
            logger.info(
                "approval_recorded",
                approval_id=str(resolved_approval.id),
                execution_id=str(id),
                decision=payload.decision,
            )
    except (ConflictError, MissingGreenlet):
        raise
    except SQLAlchemyError as exc:
        logger.exception("approval_record_failed", execution_id=str(id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to record approval decision.") from exc

    if resolved_approval is not None:
        response = ApprovalResponse.model_validate(resolved_approval)
        if interrupt_payload is not None:
            # The row is the audit record; the brief and the raw facts it was
            # written from ride along so the caller never has to re-fetch them.
            response.brief = interrupt_payload.get("brief")
            response.facts = interrupt_payload
        return response

    logger.info(
        "approval_decision_stubbed",
        approval_id=str(id),
        decision=payload.decision,
        decided_by=payload.decided_by,
    )
    return _decided_response(id, payload, interrupt_payload)


async def _close_resumed_execution(
    db: AsyncSession,
    execution_id: UUID,
    resumed: dict[str, Any],
    ended_at: datetime,
) -> None:
    """Move the executions row off ``awaiting_approval`` once the graph finished.

    Without this the row stays parked forever: the resume runs on the API's own
    connection, not through the Celery task that owns the usual status writes.
    The terminal-state constraint needs ``ended_at`` and ``termination_cause``
    together, so the row is only closed when the graph actually reported an
    outcome.
    """
    outcome = resumed.get("outcome")
    if not outcome:
        return
    lifecycle = resumed.get("lifecycle")
    cause = f"{lifecycle}:{outcome}" if lifecycle and lifecycle != "direct" else str(outcome)
    values: dict[str, Any] = {
        "status": "succeeded",
        "ended_at": ended_at,
        "termination_cause": cause,
    }
    if resumed.get("node_reached"):
        values["node_reached"] = str(resumed["node_reached"])
    if resumed.get("agent_version"):
        values["agent_version"] = str(resumed["agent_version"])
    if resumed.get("model_name"):
        values["model_name"] = str(resumed["model_name"])
    await db.execute(
        update(Execution).where(Execution.execution_id == execution_id).values(**values)
    )
    await db.execute(
        update(RetryState)
        .where(RetryState.execution_id == execution_id)
        .values(state="succeeded", next_retry_at=None)
    )


def _decided_response(
    id: UUID, payload: ApprovalDecisionRequest, interrupt_payload: dict[str, Any] | None
) -> ApprovalResponse:
    return ApprovalResponse(
        id=id,
        execution_id=id,
        workflow_state_id=None,
        decision=payload.decision,
        decided_by=payload.decided_by,
        reason=payload.reason,
        evidence=payload.evidence,
        decided_at=datetime.now(UTC),
        status="decided",
        brief=(interrupt_payload or {}).get("brief"),
        facts=interrupt_payload,
    )


def _paused_response(id: UUID, payload: dict[str, Any]) -> ApprovalResponse:
    return ApprovalResponse(
        id=id,
        execution_id=id,
        workflow_state_id=None,
        decision=None,
        decided_by=None,
        reason=None,
        evidence=None,
        decided_at=None,
        status="awaiting_approval",
        brief=payload.get("brief"),
        facts=payload,
    )
