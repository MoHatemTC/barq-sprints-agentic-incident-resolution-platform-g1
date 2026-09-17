"""Execution audit and trace query router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import MissingGreenlet, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import verify_bearer_token
from api.schemas.executions import (
    ExecutionNodeStateResponse,
    ExecutionResponse,
    IncidentExecutionsResponse,
    TraceResponse,
)
from app.api.dependencies import get_db_session
from app.db.models import Execution, ExecutionNodeState
from app.exceptions.app_errors import (
    ResourceNotFoundError,
    ServiceUnavailableError,
)

logger = structlog.getLogger("api.executions")

router = APIRouter(
    prefix="/api/v1",
    tags=["Executions"],
    dependencies=[Depends(verify_bearer_token)],
)


@router.get(
    "/executions/{execution_id}",
    response_model=ExecutionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get execution details by ID",
    description="Fetch the operational status and lifecycle attributes of a specific execution.",
)
async def get_execution(
    execution_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ExecutionResponse:
    """Retrieve execution record from PostgreSQL executions table."""
    try:
        execution = await db.get(Execution, execution_id)
    except MissingGreenlet:
        raise
    except SQLAlchemyError as exc:
        logger.error("database_query_failed", execution_id=str(execution_id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to retrieve execution.") from exc

    if not execution:
        raise ResourceNotFoundError(f"Execution '{execution_id}' not found")

    return ExecutionResponse.model_validate(execution)


@router.get(
    "/executions/{execution_id}/trace",
    response_model=TraceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get execution trace and node progression",
    description="Fetch the ordered sequence of workflow node states executed for this run.",
)
async def get_execution_trace(
    execution_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> TraceResponse:
    """Retrieve execution trace with historical workflow node attempts."""
    try:
        execution = await db.get(Execution, execution_id)
    except MissingGreenlet:
        raise
    except SQLAlchemyError as exc:
        logger.error("database_query_failed", execution_id=str(execution_id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to retrieve execution.") from exc

    if not execution:
        raise ResourceNotFoundError(f"Execution '{execution_id}' not found")

    try:
        query = (
            select(ExecutionNodeState)
            .where(ExecutionNodeState.execution_id == execution_id)
            .order_by(ExecutionNodeState.sequence_number.asc(), ExecutionNodeState.attempt.asc())
        )
        result = await db.execute(query)
        rows = result.scalars().all()
    except MissingGreenlet:
        raise
    except SQLAlchemyError as exc:
        logger.error("database_query_failed", execution_id=str(execution_id), error=str(exc))
        raise ServiceUnavailableError("Database unavailable to retrieve execution trace nodes.") from exc

    node_states = [ExecutionNodeStateResponse.model_validate(row) for row in rows]

    return TraceResponse(
        execution_id=execution.execution_id,
        incident_sys_id=execution.incident_sys_id,
        status=execution.status,
        node_states=node_states,
    )


@router.get(
    "/incidents/{sys_id}/executions",
    response_model=IncidentExecutionsResponse,
    status_code=status.HTTP_200_OK,
    summary="List all executions for a ServiceNow incident",
    description="Fetch all execution audit runs associated with a 32-hex incident sys_id.",
)
async def list_incident_executions(
    sys_id: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> IncidentExecutionsResponse:
    """Retrieve all executions for a given incident_sys_id."""
    try:
        query = (
            select(Execution)
            .where(Execution.incident_sys_id == sys_id)
            .order_by(Execution.started_at.desc())
        )
        result = await db.execute(query)
        rows = result.scalars().all()
    except MissingGreenlet:
        raise
    except SQLAlchemyError as exc:
        logger.error("database_query_failed", sys_id=sys_id, error=str(exc))
        raise ServiceUnavailableError("Database unavailable to list incident executions.") from exc

    executions = [ExecutionResponse.model_validate(row) for row in rows]

    return IncidentExecutionsResponse(
        incident_sys_id=sys_id,
        executions=executions,
    )
