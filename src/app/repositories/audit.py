"""Read-only reconstruction of one execution and its PostgreSQL audit history."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db.models import Approval, Event, Execution, ExecutionNodeState, Failure, RetryState
from app.db.session import SessionFactory


@dataclass(frozen=True, slots=True)
class EventAuditContext:
    """Inbound event that caused an execution."""

    event_record_id: UUID
    event_id: str
    incident_number: str
    event_type: str
    contract_version: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class WorkflowAuditEntry:
    """One historical workflow-node attempt."""

    workflow_state_id: UUID
    sequence_number: int
    node_name: str
    attempt: int
    status: str
    started_at: datetime
    ended_at: datetime | None
    evidence: list[dict[str, Any]]
    decision: dict[str, Any] | None
    state_snapshot: dict[str, Any] | None

    @property
    def duration(self) -> timedelta | None:
        """Return elapsed time for completed attempts without persisting a derivative."""
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at


@dataclass(frozen=True, slots=True)
class ApprovalAuditEntry:
    """One immutable approval decision associated with an execution."""

    approval_id: UUID
    workflow_state_id: UUID | None
    decision: str
    decided_by: str
    reason: str | None
    evidence: dict[str, Any] | None
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class FailureAuditEntry:
    """One historical execution or workflow-node failure."""

    failure_id: UUID
    workflow_state_id: UUID | None
    attempt: int
    failure_type: str
    error_code: str | None
    message: str
    details: dict[str, Any] | None
    retryable: bool
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RetryStateAudit:
    """Current retry-control state for an execution, not retry history."""

    retry_state_id: UUID
    state: str
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime | None
    last_attempt_at: datetime | None
    last_failure_id: UUID | None
    backoff_seconds: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionAudit:
    """Complete reconstructable PostgreSQL audit view for one execution."""

    execution_id: UUID
    event_record_id: UUID
    incident_sys_id: str
    status: str
    node_reached: str | None
    model_name: str | None
    agent_version: str | None
    started_at: datetime
    ended_at: datetime | None
    termination_cause: str | None
    updated_at: datetime
    event: EventAuditContext
    workflow_history: tuple[WorkflowAuditEntry, ...]
    approvals: tuple[ApprovalAuditEntry, ...]
    failures: tuple[FailureAuditEntry, ...]
    retry_state: RetryStateAudit | None

    @property
    def duration(self) -> timedelta | None:
        """Return elapsed execution time only after the execution has ended."""
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at


async def reconstruct_execution(
    session_factory: SessionFactory,
    execution_id: UUID,
) -> ExecutionAudit | None:
    """Reconstruct one execution, returning ``None`` when it does not exist.

    Child collections are deliberately queried separately so multiple workflow,
    approval, and failure rows cannot multiply one another through a wide join.
    """
    async with session_factory() as session:
        execution_row = (
            await session.execute(
                select(
                    Execution.execution_id,
                    Execution.event_record_id,
                    Execution.incident_sys_id,
                    Execution.status,
                    Execution.node_reached,
                    Execution.model_name,
                    Execution.agent_version,
                    Execution.started_at,
                    Execution.ended_at,
                    Execution.termination_cause,
                    Execution.updated_at,
                    Event.event_id,
                    Event.incident_number,
                    Event.event_type,
                    Event.contract_version,
                    Event.received_at,
                )
                .join(Event, Event.id == Execution.event_record_id)
                .where(Execution.execution_id == execution_id)
            )
        ).one_or_none()
        if execution_row is None:
            return None

        workflow_rows = (
            await session.execute(
                select(
                    ExecutionNodeState.id,
                    ExecutionNodeState.sequence_number,
                    ExecutionNodeState.node_name,
                    ExecutionNodeState.attempt,
                    ExecutionNodeState.status,
                    ExecutionNodeState.started_at,
                    ExecutionNodeState.ended_at,
                    ExecutionNodeState.evidence,
                    ExecutionNodeState.decision,
                    ExecutionNodeState.state_snapshot,
                )
                .where(ExecutionNodeState.execution_id == execution_id)
                .order_by(
                    ExecutionNodeState.sequence_number,
                    ExecutionNodeState.attempt,
                    ExecutionNodeState.started_at,
                    ExecutionNodeState.id,
                )
            )
        ).all()
        approval_rows = (
            await session.execute(
                select(
                    Approval.id,
                    Approval.workflow_state_id,
                    Approval.decision,
                    Approval.decided_by,
                    Approval.reason,
                    Approval.evidence,
                    Approval.decided_at,
                )
                .where(Approval.execution_id == execution_id)
                .order_by(Approval.decided_at, Approval.id)
            )
        ).all()
        failure_rows = (
            await session.execute(
                select(
                    Failure.failure_id,
                    Failure.workflow_state_id,
                    Failure.attempt,
                    Failure.failure_type,
                    Failure.error_code,
                    Failure.message,
                    Failure.details,
                    Failure.retryable,
                    Failure.occurred_at,
                )
                .where(Failure.execution_id == execution_id)
                .order_by(Failure.occurred_at, Failure.failure_id)
            )
        ).all()
        retry_row = (
            await session.execute(
                select(
                    RetryState.retry_state_id,
                    RetryState.state,
                    RetryState.attempt_count,
                    RetryState.max_attempts,
                    RetryState.next_retry_at,
                    RetryState.last_attempt_at,
                    RetryState.last_failure_id,
                    RetryState.backoff_seconds,
                    RetryState.created_at,
                    RetryState.updated_at,
                ).where(RetryState.execution_id == execution_id)
            )
        ).one_or_none()

    workflow_history = tuple(
        WorkflowAuditEntry(
            workflow_state_id=row.id,
            sequence_number=row.sequence_number,
            node_name=row.node_name,
            attempt=row.attempt,
            status=row.status,
            started_at=row.started_at,
            ended_at=row.ended_at,
            evidence=deepcopy(row.evidence),
            decision=deepcopy(row.decision),
            state_snapshot=deepcopy(row.state_snapshot),
        )
        for row in workflow_rows
    )
    approvals = tuple(
        ApprovalAuditEntry(
            approval_id=row.id,
            workflow_state_id=row.workflow_state_id,
            decision=row.decision,
            decided_by=row.decided_by,
            reason=row.reason,
            evidence=deepcopy(row.evidence),
            decided_at=row.decided_at,
        )
        for row in approval_rows
    )
    failures = tuple(
        FailureAuditEntry(
            failure_id=row.failure_id,
            workflow_state_id=row.workflow_state_id,
            attempt=row.attempt,
            failure_type=row.failure_type,
            error_code=row.error_code,
            message=row.message,
            details=deepcopy(row.details),
            retryable=row.retryable,
            occurred_at=row.occurred_at,
        )
        for row in failure_rows
    )
    retry_state = (
        None
        if retry_row is None
        else RetryStateAudit(
            retry_state_id=retry_row.retry_state_id,
            state=retry_row.state,
            attempt_count=retry_row.attempt_count,
            max_attempts=retry_row.max_attempts,
            next_retry_at=retry_row.next_retry_at,
            last_attempt_at=retry_row.last_attempt_at,
            last_failure_id=retry_row.last_failure_id,
            backoff_seconds=retry_row.backoff_seconds,
            created_at=retry_row.created_at,
            updated_at=retry_row.updated_at,
        )
    )

    return ExecutionAudit(
        execution_id=execution_row.execution_id,
        event_record_id=execution_row.event_record_id,
        incident_sys_id=execution_row.incident_sys_id,
        status=execution_row.status,
        node_reached=execution_row.node_reached,
        model_name=execution_row.model_name,
        agent_version=execution_row.agent_version,
        started_at=execution_row.started_at,
        ended_at=execution_row.ended_at,
        termination_cause=execution_row.termination_cause,
        updated_at=execution_row.updated_at,
        event=EventAuditContext(
            event_record_id=execution_row.event_record_id,
            event_id=execution_row.event_id,
            incident_number=execution_row.incident_number,
            event_type=execution_row.event_type,
            contract_version=execution_row.contract_version,
            received_at=execution_row.received_at,
        ),
        workflow_history=workflow_history,
        approvals=approvals,
        failures=failures,
        retry_state=retry_state,
    )


__all__ = [
    "ApprovalAuditEntry",
    "EventAuditContext",
    "ExecutionAudit",
    "FailureAuditEntry",
    "RetryStateAudit",
    "WorkflowAuditEntry",
    "reconstruct_execution",
]
