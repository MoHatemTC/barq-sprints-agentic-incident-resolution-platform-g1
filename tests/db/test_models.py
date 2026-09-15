"""Structural tests for the PostgreSQL ORM schema without a live database."""

from collections.abc import Iterable

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.schema import Column, Table

from app.db.base import NAMING_CONVENTION, Base
from app.db.models import (
    Approval,
    Event,
    Execution,
    ExecutionNodeState,
    Failure,
    IdempotencyKey,
    RetryState,
)
from app.models.execution_log import ExecutionLogEntry
from app.models.knowledge import WorkflowState as KnowledgeWorkflowState
from db import models as compatibility_models

EXPECTED_TABLES = {
    "events",
    "idempotency_keys",
    "executions",
    "workflow_state",
    "approvals",
    "failures",
    "retry_state",
}

EXPECTED_COLUMNS = {
    "events": {
        "id",
        "event_id",
        "incident_sys_id",
        "incident_number",
        "event_type",
        "contract_version",
        "received_at",
    },
    "idempotency_keys": {"id", "event_id", "created_at"},
    "executions": {
        "execution_id",
        "event_record_id",
        "incident_sys_id",
        "status",
        "node_reached",
        "model_name",
        "agent_version",
        "started_at",
        "ended_at",
        "termination_cause",
        "updated_at",
    },
    "workflow_state": {
        "id",
        "execution_id",
        "sequence_number",
        "node_name",
        "attempt",
        "status",
        "started_at",
        "ended_at",
        "evidence",
        "decision",
        "state_snapshot",
    },
    "approvals": {
        "id",
        "execution_id",
        "workflow_state_id",
        "decision",
        "decided_by",
        "reason",
        "evidence",
        "decided_at",
    },
    "failures": {
        "failure_id",
        "execution_id",
        "workflow_state_id",
        "attempt",
        "failure_type",
        "error_code",
        "message",
        "details",
        "retryable",
        "occurred_at",
    },
    "retry_state": {
        "retry_state_id",
        "execution_id",
        "state",
        "attempt_count",
        "max_attempts",
        "next_retry_at",
        "last_attempt_at",
        "last_failure_id",
        "backoff_seconds",
        "created_at",
        "updated_at",
    },
}

NULLABLE_COLUMNS = {
    "events": set(),
    "idempotency_keys": set(),
    "executions": {
        "node_reached",
        "model_name",
        "agent_version",
        "ended_at",
        "termination_cause",
    },
    "workflow_state": {"ended_at", "decision", "state_snapshot"},
    "approvals": {"workflow_state_id", "reason", "evidence"},
    "failures": {"workflow_state_id", "error_code", "details"},
    "retry_state": {
        "next_retry_at",
        "last_attempt_at",
        "last_failure_id",
        "backoff_seconds",
    },
}


def _unique_column_sets(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _index_column_sets(indexes: Iterable[Index]) -> set[tuple[str, ...]]:
    return {tuple(column.name for column in index.columns) for index in indexes}


def _normalize_sql(expression: object) -> str:
    return " ".join(str(expression).split())


def _check_sql(table: Table, constraint_name: str) -> str:
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )
    return _normalize_sql(constraint.sqltext)


def _foreign_key_signatures(
    table: Table,
) -> set[tuple[tuple[str, ...], tuple[str, ...], str | None, bool | None, str | None]]:
    return {
        (
            tuple(constraint.columns.keys()),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
            constraint.deferrable,
            constraint.initially,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _server_default(column: Column) -> str | None:
    if column.server_default is None:
        return None
    return _normalize_sql(column.server_default.arg)


def test_metadata_contains_exactly_the_seven_required_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_all_required_columns_and_nullability() -> None:
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        assert set(table.columns.keys()) == expected_columns
        assert {column.name for column in table.columns if column.nullable} == NULLABLE_COLUMNS[
            table_name
        ]


def test_meaningful_column_types_and_lengths() -> None:
    uuid_columns = [
        Event.__table__.c.id,
        IdempotencyKey.__table__.c.id,
        Execution.__table__.c.execution_id,
        Execution.__table__.c.event_record_id,
        ExecutionNodeState.__table__.c.id,
        ExecutionNodeState.__table__.c.execution_id,
        Approval.__table__.c.id,
        Approval.__table__.c.execution_id,
        Approval.__table__.c.workflow_state_id,
        Failure.__table__.c.failure_id,
        Failure.__table__.c.execution_id,
        Failure.__table__.c.workflow_state_id,
        RetryState.__table__.c.retry_state_id,
        RetryState.__table__.c.execution_id,
        RetryState.__table__.c.last_failure_id,
    ]
    assert all(isinstance(column.type, Uuid) for column in uuid_columns)

    jsonb_columns = [
        ExecutionNodeState.__table__.c.evidence,
        ExecutionNodeState.__table__.c.decision,
        ExecutionNodeState.__table__.c.state_snapshot,
        Approval.__table__.c.evidence,
        Failure.__table__.c.details,
    ]
    assert all(isinstance(column.type, JSONB) for column in jsonb_columns)

    assert isinstance(Event.__table__.c.event_id.type, String)
    assert Event.__table__.c.event_id.type.length == 64
    assert Event.__table__.c.incident_sys_id.type.length == 32
    assert isinstance(Execution.__table__.c.termination_cause.type, Text)
    assert isinstance(Failure.__table__.c.retryable.type, Boolean)
    assert isinstance(RetryState.__table__.c.attempt_count.type, Integer)


def test_server_defaults_are_complete_and_explicit() -> None:
    expected_defaults = {
        ("events", "id"): "gen_random_uuid()",
        ("events", "contract_version"): "'v1'",
        ("events", "received_at"): "now()",
        ("idempotency_keys", "id"): "gen_random_uuid()",
        ("idempotency_keys", "created_at"): "now()",
        ("executions", "execution_id"): "gen_random_uuid()",
        ("executions", "status"): "'accepted'",
        ("executions", "started_at"): "now()",
        ("executions", "updated_at"): "now()",
        ("workflow_state", "id"): "gen_random_uuid()",
        ("workflow_state", "attempt"): "1",
        ("workflow_state", "status"): "'started'",
        ("workflow_state", "started_at"): "now()",
        ("workflow_state", "evidence"): "'[]'::jsonb",
        ("approvals", "id"): "gen_random_uuid()",
        ("approvals", "decided_at"): "now()",
        ("failures", "failure_id"): "gen_random_uuid()",
        ("failures", "attempt"): "1",
        ("failures", "retryable"): "false",
        ("failures", "occurred_at"): "now()",
        ("retry_state", "retry_state_id"): "gen_random_uuid()",
        ("retry_state", "state"): "'ready'",
        ("retry_state", "attempt_count"): "0",
        ("retry_state", "created_at"): "now()",
        ("retry_state", "updated_at"): "now()",
    }
    actual_defaults = {
        (table.name, column.name): _server_default(column)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.server_default is not None
    }
    assert actual_defaults == expected_defaults

    uuid_primary_keys = [
        Event.__table__.c.id,
        IdempotencyKey.__table__.c.id,
        Execution.__table__.c.execution_id,
        ExecutionNodeState.__table__.c.id,
        Approval.__table__.c.id,
        Failure.__table__.c.failure_id,
        RetryState.__table__.c.retry_state_id,
    ]
    assert all(_server_default(column) == "gen_random_uuid()" for column in uuid_primary_keys)


def test_business_key_and_one_execution_uniqueness() -> None:
    assert ("event_id",) in _unique_column_sets(IdempotencyKey.__table__)
    assert ("event_id",) in _unique_column_sets(Event.__table__)
    assert ("event_record_id",) in _unique_column_sets(Execution.__table__)

    names = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
    }
    assert "uq_idempotency_keys_event_id" in names
    assert "uq_events_event_id" in names
    assert "uq_executions_event_record_id" in names


def test_required_and_query_driven_indexes_exist() -> None:
    # Unique business-key constraints create PostgreSQL indexes without redundant Index objects.
    assert ("event_id",) in _unique_column_sets(Event.__table__)
    assert ("event_id",) in _unique_column_sets(IdempotencyKey.__table__)
    assert ("incident_sys_id",) in _index_column_sets(Event.__table__.indexes)
    assert ("status",) in _index_column_sets(Execution.__table__.indexes)
    assert ("incident_sys_id", "started_at") in _index_column_sets(Execution.__table__.indexes)
    assert ("execution_id", "decided_at") in _index_column_sets(Approval.__table__.indexes)
    assert ("execution_id", "occurred_at") in _index_column_sets(Failure.__table__.indexes)
    assert ("next_retry_at",) in _index_column_sets(RetryState.__table__.indexes)


def test_all_timestamp_columns_are_timezone_aware() -> None:
    expected_timestamps = {
        ("events", "received_at"),
        ("idempotency_keys", "created_at"),
        ("executions", "started_at"),
        ("executions", "ended_at"),
        ("executions", "updated_at"),
        ("workflow_state", "started_at"),
        ("workflow_state", "ended_at"),
        ("approvals", "decided_at"),
        ("failures", "occurred_at"),
        ("retry_state", "next_retry_at"),
        ("retry_state", "last_attempt_at"),
        ("retry_state", "created_at"),
        ("retry_state", "updated_at"),
    }
    timestamps = {
        (table.name, column.name): column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime)
    }

    assert set(timestamps) == expected_timestamps
    assert all(column.type.timezone is True for column in timestamps.values())


def test_expected_foreign_keys_and_delete_behaviors_exist() -> None:
    assert _foreign_key_signatures(IdempotencyKey.__table__) == {
        (("event_id",), ("events.event_id",), "CASCADE", True, "DEFERRED")
    }
    assert _foreign_key_signatures(Execution.__table__) == {
        (("event_record_id",), ("events.id",), "CASCADE", None, None)
    }
    assert _foreign_key_signatures(ExecutionNodeState.__table__) == {
        (("execution_id",), ("executions.execution_id",), "CASCADE", None, None)
    }
    assert _foreign_key_signatures(Approval.__table__) == {
        (("execution_id",), ("executions.execution_id",), "RESTRICT", None, None),
        (
            ("execution_id", "workflow_state_id"),
            ("workflow_state.execution_id", "workflow_state.id"),
            "NO ACTION",
            None,
            None,
        ),
    }
    assert _foreign_key_signatures(Failure.__table__) == {
        (("execution_id",), ("executions.execution_id",), "CASCADE", None, None),
        (
            ("execution_id", "workflow_state_id"),
            ("workflow_state.execution_id", "workflow_state.id"),
            "NO ACTION",
            None,
            None,
        ),
    }
    assert _foreign_key_signatures(RetryState.__table__) == {
        (("execution_id",), ("executions.execution_id",), "CASCADE", None, None),
        (
            ("execution_id", "last_failure_id"),
            ("failures.execution_id", "failures.failure_id"),
            "NO ACTION",
            None,
            None,
        ),
    }


def test_expected_relationships_are_configured() -> None:
    expected_relationships = {
        Event: {"idempotency_key", "execution"},
        IdempotencyKey: {"event"},
        Execution: {"event", "node_states", "approvals", "failures", "retry_state"},
        ExecutionNodeState: {"execution", "approvals", "failures"},
        Approval: {"execution", "node_state"},
        Failure: {"execution", "node_state", "retry_states"},
        RetryState: {"execution", "last_failure"},
    }

    for model, relationship_names in expected_relationships.items():
        assert set(inspect(model).relationships.keys()) == relationship_names


def test_workflow_order_and_node_attempt_are_unique() -> None:
    uniques = _unique_column_sets(ExecutionNodeState.__table__)
    assert ("execution_id", "sequence_number") in uniques
    assert ("execution_id", "node_name", "attempt") in uniques
    assert ("execution_id", "id") in uniques

    failure_uniques = _unique_column_sets(Failure.__table__)
    assert ("execution_id", "failure_id") in failure_uniques


def test_event_and_execution_check_constraints() -> None:
    assert _check_sql(Event.__table__, "ck_events_event_type") == (
        "event_type IN ('incident.created', 'incident.updated')"
    )
    assert _check_sql(Execution.__table__, "ck_executions_status") == (
        "status IN ('accepted', 'queued', 'running', 'awaiting_approval', "
        "'succeeded', 'failed', 'blocked', 'abandoned')"
    )
    assert _check_sql(Execution.__table__, "ck_executions_time_order") == (
        "ended_at IS NULL OR ended_at >= started_at"
    )
    assert _check_sql(Execution.__table__, "ck_executions_terminal_state") == (
        "((status IN ('succeeded', 'failed', 'blocked', 'abandoned') "
        "AND ended_at IS NOT NULL AND termination_cause IS NOT NULL "
        "AND btrim(termination_cause) <> '') OR "
        "(status NOT IN ('succeeded', 'failed', 'blocked', 'abandoned') "
        "AND ended_at IS NULL AND termination_cause IS NULL))"
    )


def test_workflow_check_constraints() -> None:
    checks = {
        constraint.name: _normalize_sql(constraint.sqltext)
        for constraint in ExecutionNodeState.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_workflow_state_positive_sequence": "sequence_number >= 1",
        "ck_workflow_state_positive_attempt": "attempt >= 1",
        "ck_workflow_state_status": (
            "status IN ('started', 'succeeded', 'failed', 'blocked', "
            "'awaiting_approval', 'skipped')"
        ),
        "ck_workflow_state_time_order": "ended_at IS NULL OR ended_at >= started_at",
        "ck_workflow_state_evidence_array": "jsonb_typeof(evidence) = 'array'",
        "ck_workflow_state_decision_object": (
            "decision IS NULL OR jsonb_typeof(decision) = 'object'"
        ),
        "ck_workflow_state_state_snapshot_object": (
            "state_snapshot IS NULL OR jsonb_typeof(state_snapshot) = 'object'"
        ),
    }


def test_approval_and_failure_check_constraints() -> None:
    assert _check_sql(Approval.__table__, "ck_approvals_decision") == (
        "decision IN ('approved', 'rejected', 'cancelled', 'expired')"
    )
    assert _check_sql(Failure.__table__, "ck_failures_positive_attempt") == "attempt >= 1"


def test_retry_check_constraints_and_partial_index() -> None:
    checks = {
        constraint.name: _normalize_sql(constraint.sqltext)
        for constraint in RetryState.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_retry_state_state": (
            "state IN ('ready', 'scheduled', 'exhausted', 'succeeded', 'cancelled')"
        ),
        "ck_retry_state_nonnegative_attempt_count": "attempt_count >= 0",
        "ck_retry_state_positive_max_attempts": "max_attempts >= 1",
        "ck_retry_state_attempt_limit": "attempt_count <= max_attempts",
        "ck_retry_state_active_retry_remaining": (
            "state NOT IN ('ready', 'scheduled') OR attempt_count < max_attempts"
        ),
        "ck_retry_state_exhausted_attempt_limit": (
            "state <> 'exhausted' OR attempt_count = max_attempts"
        ),
        "ck_retry_state_last_attempt_consistency": (
            "(attempt_count = 0 AND last_attempt_at IS NULL) "
            "OR (attempt_count > 0 AND last_attempt_at IS NOT NULL)"
        ),
        "ck_retry_state_nonnegative_backoff": ("backoff_seconds IS NULL OR backoff_seconds >= 0"),
        "ck_retry_state_schedule_time": (
            "(state = 'scheduled' AND next_retry_at IS NOT NULL) "
            "OR (state <> 'scheduled' AND next_retry_at IS NULL)"
        ),
    }

    due_index = next(
        index for index in RetryState.__table__.indexes if index.name == "ix_retry_state_due"
    )
    predicate = due_index.dialect_options["postgresql"]["where"]
    assert _normalize_sql(predicate) == "state = 'scheduled'"


def test_retry_policy_is_required_not_hard_coded() -> None:
    max_attempts = RetryState.__table__.c.max_attempts
    assert max_attempts.nullable is False
    assert max_attempts.server_default is None


def test_node_reached_is_documented_as_a_denormalized_summary() -> None:
    assert Execution.__table__.c.node_reached.comment == (
        "Latest workflow node entered; workflow_state is the authoritative history."
    )


def test_metadata_naming_convention_is_stable() -> None:
    assert NAMING_CONVENTION == {
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


def test_ticket_compatibility_module_reexports_canonical_models() -> None:
    assert compatibility_models.Event is Event
    assert compatibility_models.Execution is Execution
    assert compatibility_models.ExecutionNodeState is ExecutionNodeState
    assert compatibility_models.Approval is Approval
    assert compatibility_models.Failure is Failure
    assert compatibility_models.IdempotencyKey is IdempotencyKey
    assert compatibility_models.RetryState is RetryState


def test_existing_pydantic_models_remain_non_orm_models() -> None:
    assert not hasattr(ExecutionLogEntry, "__table__")
    assert not hasattr(KnowledgeWorkflowState, "__table__")
