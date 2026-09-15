"""SQLAlchemy models for the seven PostgreSQL operational-state tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

_EXECUTION_TERMINAL_STATUSES = "'succeeded', 'failed', 'blocked', 'abandoned'"


class Event(Base):
    """Immutable copy of an accepted Sprint 1 four-field inbound event."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_events_event_id"),
        CheckConstraint(
            "event_type IN ('incident.created', 'incident.updated')",
            name="event_type",
        ),
        Index("ix_events_incident_sys_id", "incident_sys_id"),
        Index("ix_events_received_at", "received_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_sys_id: Mapped[str] = mapped_column(String(32), nullable=False)
    incident_number: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'v1'")
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    idempotency_key: Mapped[IdempotencyKey | None] = relationship(
        back_populates="event", uselist=False, passive_deletes=True
    )
    execution: Mapped[Execution | None] = relationship(
        back_populates="event", uselist=False, passive_deletes=True
    )


class IdempotencyKey(Base):
    """Authoritative database concurrency gate for an external event ID."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("event_id", name="uq_idempotency_keys_event_id"),)

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "events.event_id",
            name="fk_idempotency_keys_event_id_events",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    event: Mapped[Event] = relationship(back_populates="idempotency_key")


class Execution(Base):
    """Mutable summary of the one execution accepted for an event."""

    __tablename__ = "executions"
    __table_args__ = (
        UniqueConstraint("event_record_id", name="uq_executions_event_record_id"),
        CheckConstraint(
            "status IN ('accepted', 'queued', 'running', 'awaiting_approval', "
            "'succeeded', 'failed', 'blocked', 'abandoned')",
            name="status",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="time_order",
        ),
        CheckConstraint(
            f"((status IN ({_EXECUTION_TERMINAL_STATUSES}) "
            "AND ended_at IS NOT NULL "
            "AND termination_cause IS NOT NULL "
            "AND btrim(termination_cause) <> '') "
            f"OR (status NOT IN ({_EXECUTION_TERMINAL_STATUSES}) "
            "AND ended_at IS NULL "
            "AND termination_cause IS NULL))",
            name="terminal_state",
        ),
        Index("ix_executions_status", "status"),
        Index("ix_executions_incident_started", "incident_sys_id", "started_at"),
    )

    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_record_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "events.id",
            name="fk_executions_event_record_id_events",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    incident_sys_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'accepted'")
    )
    node_reached: Mapped[str | None] = mapped_column(
        String(100),
        comment="Latest workflow node entered; workflow_state is the authoritative history.",
    )
    model_name: Mapped[str | None] = mapped_column(String(100))
    agent_version: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termination_cause: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    # Phase 3: add a DB-side updated_at trigger so direct SQL updates are covered too.

    event: Mapped[Event] = relationship(back_populates="execution")
    node_states: Mapped[list[ExecutionNodeState]] = relationship(
        back_populates="execution", passive_deletes=True
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="execution", passive_deletes=True
    )
    failures: Mapped[list[Failure]] = relationship(back_populates="execution", passive_deletes=True)
    retry_state: Mapped[RetryState | None] = relationship(
        back_populates="execution", uselist=False, passive_deletes=True
    )


class ExecutionNodeState(Base):
    """Historical result of one workflow node attempt."""

    __tablename__ = "workflow_state"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "sequence_number",
            name="uq_workflow_state_execution_sequence",
        ),
        UniqueConstraint(
            "execution_id",
            "node_name",
            "attempt",
            name="uq_workflow_state_execution_node_attempt",
        ),
        UniqueConstraint(
            "execution_id",
            "id",
            name="uq_workflow_state_execution_id_id",
        ),
        CheckConstraint("sequence_number >= 1", name="positive_sequence"),
        CheckConstraint("attempt >= 1", name="positive_attempt"),
        CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'blocked', "
            "'awaiting_approval', 'skipped')",
            name="status",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="time_order"),
        CheckConstraint("jsonb_typeof(evidence) = 'array'", name="evidence_array"),
        CheckConstraint(
            "decision IS NULL OR jsonb_typeof(decision) = 'object'",
            name="decision_object",
        ),
        CheckConstraint(
            "state_snapshot IS NULL OR jsonb_typeof(state_snapshot) = 'object'",
            name="state_snapshot_object",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "executions.execution_id",
            name="fk_workflow_state_execution_id_executions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(100), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'started'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    state_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    execution: Mapped[Execution] = relationship(back_populates="node_states")
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="node_state",
        foreign_keys="Approval.workflow_state_id",
        passive_deletes="all",
    )
    failures: Mapped[list[Failure]] = relationship(
        back_populates="node_state",
        foreign_keys="Failure.workflow_state_id",
        passive_deletes="all",
    )


class Approval(Base):
    """Immutable final approval decision; pending requests are not stored here."""

    __tablename__ = "approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["execution_id", "workflow_state_id"],
            ["workflow_state.execution_id", "workflow_state.id"],
            name="fk_approvals_execution_workflow_state",
            ondelete="NO ACTION",
        ),
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'cancelled', 'expired')",
            name="decision",
        ),
        Index("ix_approvals_execution_decided", "execution_id", "decided_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "executions.execution_id",
            name="fk_approvals_execution_id_executions",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    workflow_state_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped[Execution] = relationship(back_populates="approvals")
    node_state: Mapped[ExecutionNodeState | None] = relationship(
        back_populates="approvals", foreign_keys=[workflow_state_id]
    )


class Failure(Base):
    """Append-oriented historical failure attached to an execution or node."""

    __tablename__ = "failures"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "failure_id",
            name="uq_failures_execution_id_failure_id",
        ),
        ForeignKeyConstraint(
            ["execution_id", "workflow_state_id"],
            ["workflow_state.execution_id", "workflow_state.id"],
            name="fk_failures_execution_workflow_state",
            ondelete="NO ACTION",
        ),
        CheckConstraint("attempt >= 1", name="positive_attempt"),
        Index("ix_failures_execution_occurred", "execution_id", "occurred_at"),
    )

    failure_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "executions.execution_id",
            name="fk_failures_execution_id_executions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    workflow_state_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    failure_type: Mapped[str] = mapped_column(String(100), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    execution: Mapped[Execution] = relationship(back_populates="failures")
    node_state: Mapped[ExecutionNodeState | None] = relationship(
        back_populates="failures", foreign_keys=[workflow_state_id]
    )
    retry_states: Mapped[list[RetryState]] = relationship(
        back_populates="last_failure",
        foreign_keys="RetryState.last_failure_id",
        passive_deletes="all",
    )


class RetryState(Base):
    """Mutable current retry-control state for one execution."""

    __tablename__ = "retry_state"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_retry_state_execution_id"),
        ForeignKeyConstraint(
            ["execution_id", "last_failure_id"],
            ["failures.execution_id", "failures.failure_id"],
            name="fk_retry_state_execution_last_failure",
            ondelete="NO ACTION",
        ),
        CheckConstraint(
            "state IN ('ready', 'scheduled', 'exhausted', 'succeeded', 'cancelled')",
            name="state",
        ),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="positive_max_attempts"),
        CheckConstraint("attempt_count <= max_attempts", name="attempt_limit"),
        CheckConstraint(
            "state NOT IN ('ready', 'scheduled') OR attempt_count < max_attempts",
            name="active_retry_remaining",
        ),
        CheckConstraint(
            "state <> 'exhausted' OR attempt_count = max_attempts",
            name="exhausted_attempt_limit",
        ),
        CheckConstraint(
            "(attempt_count = 0 AND last_attempt_at IS NULL) "
            "OR (attempt_count > 0 AND last_attempt_at IS NOT NULL)",
            name="last_attempt_consistency",
        ),
        CheckConstraint(
            "backoff_seconds IS NULL OR backoff_seconds >= 0",
            name="nonnegative_backoff",
        ),
        CheckConstraint(
            "(state = 'scheduled' AND next_retry_at IS NOT NULL) "
            "OR (state <> 'scheduled' AND next_retry_at IS NULL)",
            name="schedule_time",
        ),
        Index(
            "ix_retry_state_due",
            "next_retry_at",
            postgresql_where=text("state = 'scheduled'"),
        ),
    )

    retry_state_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "executions.execution_id",
            name="fk_retry_state_execution_id_executions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ready'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
    )
    backoff_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    # Phase 3: add a DB-side updated_at trigger so direct SQL updates are covered too.

    execution: Mapped[Execution] = relationship(back_populates="retry_state")
    last_failure: Mapped[Failure | None] = relationship(
        back_populates="retry_states", foreign_keys=[last_failure_id]
    )


__all__ = [
    "Approval",
    "Event",
    "Execution",
    "ExecutionNodeState",
    "Failure",
    "IdempotencyKey",
    "RetryState",
]
