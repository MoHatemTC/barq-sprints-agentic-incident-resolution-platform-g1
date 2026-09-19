"""Create the PostgreSQL operational-state schema.

Revision ID: 0001_postgresql_state_schema
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_postgresql_state_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("incident_sys_id", sa.String(length=32), nullable=False),
        sa.Column("incident_number", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "contract_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('incident.created', 'incident.updated')",
            name=op.f("ck_events_event_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_events_event_id")),
    )
    op.create_index("ix_events_incident_sys_id", "events", ["incident_sys_id"])
    op.create_index("ix_events_received_at", "events", ["received_at"])

    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.event_id"],
            name=op.f("fk_idempotency_keys_event_id_events"),
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("event_id", name=op.f("uq_idempotency_keys_event_id")),
    )

    op.create_table(
        "executions",
        sa.Column(
            "execution_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_record_id", sa.Uuid(), nullable=False),
        sa.Column("incident_sys_id", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'accepted'"),
            nullable=False,
        ),
        sa.Column(
            "node_reached",
            sa.String(length=100),
            nullable=True,
            comment="Latest workflow node entered; workflow_state is the authoritative history.",
        ),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("termination_cause", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'queued', 'running', 'awaiting_approval', "
            "'succeeded', 'failed', 'blocked', 'abandoned')",
            name=op.f("ck_executions_status"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_executions_time_order"),
        ),
        sa.CheckConstraint(
            "((status IN ('succeeded', 'failed', 'blocked', 'abandoned') "
            "AND ended_at IS NOT NULL "
            "AND termination_cause IS NOT NULL "
            "AND btrim(termination_cause) <> '') "
            "OR (status NOT IN ('succeeded', 'failed', 'blocked', 'abandoned') "
            "AND ended_at IS NULL "
            "AND termination_cause IS NULL))",
            name=op.f("ck_executions_terminal_state"),
        ),
        sa.ForeignKeyConstraint(
            ["event_record_id"],
            ["events.id"],
            name=op.f("fk_executions_event_record_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("execution_id", name=op.f("pk_executions")),
        sa.UniqueConstraint("event_record_id", name=op.f("uq_executions_event_record_id")),
    )
    op.create_index("ix_executions_status", "executions", ["status"])
    op.create_index(
        "ix_executions_incident_started",
        "executions",
        ["incident_sys_id", "started_at"],
    )

    op.create_table(
        "workflow_state",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=100), nullable=False),
        sa.Column(
            "attempt",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'started'"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "state_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name=op.f("ck_workflow_state_positive_sequence"),
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_workflow_state_positive_attempt")),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed', 'blocked', "
            "'awaiting_approval', 'skipped')",
            name=op.f("ck_workflow_state_status"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("ck_workflow_state_time_order"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'",
            name=op.f("ck_workflow_state_evidence_array"),
        ),
        sa.CheckConstraint(
            "decision IS NULL OR jsonb_typeof(decision) = 'object'",
            name=op.f("ck_workflow_state_decision_object"),
        ),
        sa.CheckConstraint(
            "state_snapshot IS NULL OR jsonb_typeof(state_snapshot) = 'object'",
            name=op.f("ck_workflow_state_state_snapshot_object"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.execution_id"],
            name=op.f("fk_workflow_state_execution_id_executions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_state")),
        sa.UniqueConstraint(
            "execution_id",
            "sequence_number",
            name=op.f("uq_workflow_state_execution_sequence"),
        ),
        sa.UniqueConstraint(
            "execution_id",
            "node_name",
            "attempt",
            name=op.f("uq_workflow_state_execution_node_attempt"),
        ),
        sa.UniqueConstraint(
            "execution_id",
            "id",
            name=op.f("uq_workflow_state_execution_id_id"),
        ),
    )

    op.create_table(
        "approvals",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_state_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected', 'cancelled', 'expired')",
            name=op.f("ck_approvals_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.execution_id"],
            name=op.f("fk_approvals_execution_id_executions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "workflow_state_id"],
            ["workflow_state.execution_id", "workflow_state.id"],
            name=op.f("fk_approvals_execution_workflow_state"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index(
        "ix_approvals_execution_decided",
        "approvals",
        ["execution_id", "decided_at"],
    )

    op.create_table(
        "failures",
        sa.Column(
            "failure_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_state_id", sa.Uuid(), nullable=True),
        sa.Column(
            "attempt",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("failure_type", sa.String(length=100), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "retryable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_failures_positive_attempt")),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.execution_id"],
            name=op.f("fk_failures_execution_id_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "workflow_state_id"],
            ["workflow_state.execution_id", "workflow_state.id"],
            name=op.f("fk_failures_execution_workflow_state"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("failure_id", name=op.f("pk_failures")),
        sa.UniqueConstraint(
            "execution_id",
            "failure_id",
            name=op.f("uq_failures_execution_id_failure_id"),
        ),
    )
    op.create_index(
        "ix_failures_execution_occurred",
        "failures",
        ["execution_id", "occurred_at"],
    )

    op.create_table(
        "retry_state",
        sa.Column(
            "retry_state_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default=sa.text("'ready'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_id", sa.Uuid(), nullable=True),
        sa.Column("backoff_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('ready', 'scheduled', 'exhausted', 'succeeded', 'cancelled')",
            name=op.f("ck_retry_state_state"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_retry_state_nonnegative_attempt_count"),
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name=op.f("ck_retry_state_positive_max_attempts"),
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name=op.f("ck_retry_state_attempt_limit"),
        ),
        sa.CheckConstraint(
            "state NOT IN ('ready', 'scheduled') OR attempt_count < max_attempts",
            name=op.f("ck_retry_state_active_retry_remaining"),
        ),
        sa.CheckConstraint(
            "state <> 'exhausted' OR attempt_count = max_attempts",
            name=op.f("ck_retry_state_exhausted_attempt_limit"),
        ),
        sa.CheckConstraint(
            "(attempt_count = 0 AND last_attempt_at IS NULL) "
            "OR (attempt_count > 0 AND last_attempt_at IS NOT NULL)",
            name=op.f("ck_retry_state_last_attempt_consistency"),
        ),
        sa.CheckConstraint(
            "backoff_seconds IS NULL OR backoff_seconds >= 0",
            name=op.f("ck_retry_state_nonnegative_backoff"),
        ),
        sa.CheckConstraint(
            "(state = 'scheduled' AND next_retry_at IS NOT NULL) "
            "OR (state <> 'scheduled' AND next_retry_at IS NULL)",
            name=op.f("ck_retry_state_schedule_time"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.execution_id"],
            name=op.f("fk_retry_state_execution_id_executions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id", "last_failure_id"],
            ["failures.execution_id", "failures.failure_id"],
            name=op.f("fk_retry_state_execution_last_failure"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("retry_state_id", name=op.f("pk_retry_state")),
        sa.UniqueConstraint("execution_id", name=op.f("uq_retry_state_execution_id")),
    )
    op.create_index(
        "ix_retry_state_due",
        "retry_state",
        ["next_retry_at"],
        postgresql_where=sa.text("state = 'scheduled'"),
    )

    op.execute(
        """
        CREATE FUNCTION barq_reject_approval_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'approvals are immutable: % is not allowed', TG_OP
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approvals_immutable
        BEFORE UPDATE OR DELETE ON approvals
        FOR EACH ROW
        EXECUTE FUNCTION barq_reject_approval_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION barq_set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_executions_set_updated_at
        BEFORE UPDATE ON executions
        FOR EACH ROW
        EXECUTE FUNCTION barq_set_updated_at()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_retry_state_set_updated_at
        BEFORE UPDATE ON retry_state
        FOR EACH ROW
        EXECUTE FUNCTION barq_set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_retry_state_set_updated_at ON retry_state")
    op.execute("DROP TRIGGER IF EXISTS trg_executions_set_updated_at ON executions")
    op.execute("DROP TRIGGER IF EXISTS trg_approvals_immutable ON approvals")
    op.execute("DROP FUNCTION IF EXISTS barq_set_updated_at()")
    op.execute("DROP FUNCTION IF EXISTS barq_reject_approval_mutation()")

    op.drop_index("ix_retry_state_due", table_name="retry_state")
    op.drop_table("retry_state")
    op.drop_index("ix_failures_execution_occurred", table_name="failures")
    op.drop_table("failures")
    op.drop_index("ix_approvals_execution_decided", table_name="approvals")
    op.drop_table("approvals")
    op.drop_table("workflow_state")
    op.drop_index("ix_executions_incident_started", table_name="executions")
    op.drop_index("ix_executions_status", table_name="executions")
    op.drop_table("executions")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_events_received_at", table_name="events")
    op.drop_index("ix_events_incident_sys_id", table_name="events")
    op.drop_table("events")
