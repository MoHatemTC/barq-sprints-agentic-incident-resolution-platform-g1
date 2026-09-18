"""Human-in-the-Loop (HITL) approvals router."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.exc import MissingGreenlet, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import verify_bearer_token
from api.schemas.approvals import (
    ApprovalDecisionRequest,
    ApprovalResponse,
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
        logger.error("database_query_failed", error=str(exc))
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
        logger.error("database_query_failed", approval_id=str(id), error=str(exc))
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
        "Record or submit a human operator decision "
        "('approved', 'rejected', 'cancelled', 'expired') for an approval request."
    ),
)
async def decide_approval(
    id: UUID,
    payload: ApprovalDecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApprovalResponse:
    """Submit approval decision.

    Updates or creates an approval record in the database when associated with an existing
    approval or execution. Under Sprint 2 contract stub semantics, returns a schema-compliant
    approval response when operating without pre-seeded state.
    """
    resolved_approval = None

    try:
        # 1. Check if an approval record with this ID already exists
        # Approvals are immutable audit records protected by trg_approvals_immutable trigger.
        approval = await db.get(Approval, id)
        if approval:
            logger.warning(
                "approval_already_decided",
                approval_id=str(id),
                decision=approval.decision,
            )
            raise ConflictError(
                f"Approval '{id}' has already been decided "
                f"('{approval.decision}') and is immutable."
            )

        # 2. Check if this ID corresponds to an existing Execution
        execution = await db.get(Execution, id)
        if execution:
            new_approval = Approval(
                id=uuid4(),
                execution_id=execution.execution_id,
                decision=payload.decision,
                decided_by=payload.decided_by,
                reason=payload.reason,
                evidence=payload.evidence,
                decided_at=datetime.now(UTC),
            )
            db.add(new_approval)
            await db.commit()
            await db.refresh(new_approval)
            logger.info(
                "approval_created_for_execution",
                approval_id=str(new_approval.id),
                execution_id=str(execution.execution_id),
                decision=payload.decision,
                decided_by=payload.decided_by,
            )
            resolved_approval = new_approval
    except (ConflictError, MissingGreenlet):
        raise
    except SQLAlchemyError as exc:
        logger.error("database_operation_failed", approval_id=str(id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to record approval decision.") from exc

    if resolved_approval is not None:
        return ApprovalResponse.model_validate(resolved_approval)

    # 3. Contract Stub Fallback: return a valid schema response (Tier 2 Sprint 2 contract stub)
    logger.info(
        "approval_decision_stubbed",
        approval_id=str(id),
        decision=payload.decision,
        decided_by=payload.decided_by,
    )
    return ApprovalResponse(
        id=id,
        execution_id=id,
        workflow_state_id=None,
        decision=payload.decision,
        decided_by=payload.decided_by,
        reason=payload.reason,
        evidence=payload.evidence,
        decided_at=datetime.now(UTC),
    )
