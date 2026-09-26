"""Human-in-the-Loop (HITL) approvals router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, MissingGreenlet, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_role, verify_bearer_token
from api.schemas.approvals import (
    ApprovalDecisionRequest,
    ApprovalResponse,
    fold_solution_into_evidence,
)
from app.api.dependencies import get_db_session
from app.db.models import Approval, Execution
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
    """Retrieve an approval record by its primary key ID."""
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


@router.post(
    "/approvals/{id}/decide",
    response_model=ApprovalResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit human operator approval decision",
    description=(
        "Record a human operator decision ('approved', 'rejected', 'cancelled', "
        "'expired') for an approval request. The id is an approval id or the id of "
        "the execution it belongs to. The decider and the authorisation both come "
        "from the operator token, never from the body or a header: 409 if that "
        "execution is already decided, 404 if the id resolves to neither."
    ),
)
async def decide_approval(
    id: UUID,
    payload: ApprovalDecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    claims: Annotated[dict[str, Any], Depends(verify_bearer_token)],
    _approver: Annotated[str, Depends(require_role("approver"))],
) -> ApprovalResponse:
    """Record an approval decision, refusing anything that is not a first decision.

    Who decided and whether they may are read from the verified operator token:
    ``decided_by`` is the token's subject and the role is its ``roles`` claim
    (#148). The body carries the decision, the reason and the evidence only.

    The path id is either an approval's primary key or the execution it belongs
    to, so a second decision for the same execution is 409 whatever id form the
    caller uses, an id that resolves to neither is 404, and there is no stub
    fallback that reports a saved decision nothing stored (#147).
    """
    decided_by = str(claims.get("sub") or "")
    created: Approval | None = None

    try:
        existing = await db.get(Approval, id)
        if existing is None:
            existing = (
                await db.execute(select(Approval).where(Approval.execution_id == id).limit(1))
            ).scalar_one_or_none()
        if existing is not None:
            logger.warning(
                "approval_already_decided",
                approval_id=str(existing.id),
                execution_id=str(existing.execution_id),
                decision=existing.decision,
            )
            raise ConflictError(
                f"Execution '{existing.execution_id}' has already been decided "
                f"('{existing.decision}') and approvals are immutable."
            )

        execution = await db.get(Execution, id)
        if execution is None:
            raise ResourceNotFoundError(f"No approval request or execution '{id}' found")

        created = Approval(
            id=uuid4(),
            execution_id=execution.execution_id,
            decision=payload.decision,
            decided_by=decided_by,
            reason=payload.reason,
            # The human solution rides in the immutable evidence JSONB (there is no
            # solution column), folded with the knowledge-capture tool name so the
            # registry's high-risk checker finds well-formed scope later.
            evidence=fold_solution_into_evidence(payload.evidence, payload.solution),
            decided_at=datetime.now(UTC),
        )
        db.add(created)
        await db.commit()
        await db.refresh(created)
        logger.info(
            "approval_created_for_execution",
            approval_id=str(created.id),
            execution_id=str(execution.execution_id),
            decision=payload.decision,
            decided_by=decided_by,
        )
    except (ConflictError, ResourceNotFoundError, MissingGreenlet):
        raise
    except IntegrityError as exc:
        # Lost the race against a concurrent decision: the unique index on
        # (execution_id, coalesced workflow_state_id) is what makes this
        # impossible to bypass, so report it the same way as the check above.
        raise ConflictError(
            f"Execution '{id}' has already been decided by a concurrent request."
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("database_operation_failed", approval_id=str(id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to record approval decision.") from exc

    return ApprovalResponse.model_validate(created)
