"""Execution audit reconstruction against isolated real PostgreSQL."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Approval, Event, Execution, ExecutionNodeState, Failure, RetryState
from app.db.session import SessionFactory, create_session_factory
from app.repositories.audit import ExecutionAudit, reconstruct_execution
from tests.db.test_migrations_postgresql import _reset_public_schema, _test_database_url


@pytest.fixture(scope="module")
def audit_database_url() -> Iterator[URL]:
    """Apply the baseline only to a database protected by the test-name guard."""
    url = _test_database_url()
    asyncio.run(_reset_public_schema(url))

    repository_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(repository_root / "alembic.ini"))
    previous_url = os.environ.get("BARQ_DATABASE_URL")
    os.environ["BARQ_DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        command.upgrade(alembic_config, "head")
        yield url
    finally:
        asyncio.run(_reset_public_schema(url))
        if previous_url is None:
            os.environ.pop("BARQ_DATABASE_URL", None)
        else:
            os.environ["BARQ_DATABASE_URL"] = previous_url


@pytest.fixture
async def session_factory(audit_database_url: URL) -> AsyncIterator[SessionFactory]:
    """Give each audit test fresh rows and connections bound to its event loop."""
    engine = create_async_engine(audit_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "TRUNCATE TABLE "
                    "retry_state, failures, approvals, workflow_state, executions, "
                    "idempotency_keys, events CASCADE"
                )
            )
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def test_reconstructs_complete_execution_in_order_without_multiplication(
    session_factory: SessionFactory,
) -> None:
    event_record_id = uuid4()
    execution_id = uuid4()
    node_ids = (uuid4(), uuid4(), uuid4())
    approval_ids = (uuid4(), uuid4())
    failure_ids = (uuid4(), uuid4())
    retry_state_id = uuid4()
    started_at = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)

    async with session_factory() as session, session.begin():
        session.add(
            Event(
                id=event_record_id,
                event_id="audit-event-complete",
                incident_sys_id="0123456789abcdef0123456789abcdef",
                incident_number="INC0012300",
                event_type="incident.created",
                contract_version="v1",
                received_at=started_at - timedelta(minutes=1),
            )
        )
        session.add(
            Execution(
                execution_id=execution_id,
                event_record_id=event_record_id,
                incident_sys_id="0123456789abcdef0123456789abcdef",
                status="failed",
                node_reached="diagnose",
                model_name="audit-model-v1",
                agent_version="agent-2.0.0",
                started_at=started_at,
                ended_at=started_at + timedelta(minutes=15),
                termination_cause="retry budget exhausted",
                updated_at=started_at + timedelta(minutes=15),
            )
        )
        await session.flush()

        first_node = ExecutionNodeState(
            id=node_ids[0],
            execution_id=execution_id,
            sequence_number=1,
            node_name="classify",
            attempt=1,
            status="succeeded",
            started_at=started_at + timedelta(minutes=1),
            ended_at=started_at + timedelta(minutes=2),
            evidence=[{"source": "incident", "score": 0.95}],
            decision={"category": "database"},
            state_snapshot={"incident_state": "new"},
        )
        second_node = ExecutionNodeState(
            id=node_ids[1],
            execution_id=execution_id,
            sequence_number=2,
            node_name="diagnose",
            attempt=1,
            status="failed",
            started_at=started_at + timedelta(minutes=3),
            ended_at=started_at + timedelta(minutes=5),
            evidence=[{"query": "primary diagnosis"}],
            decision={"retry": True},
            state_snapshot={"attempt": 1},
        )
        third_node = ExecutionNodeState(
            id=node_ids[2],
            execution_id=execution_id,
            sequence_number=3,
            node_name="diagnose",
            attempt=2,
            status="failed",
            started_at=started_at + timedelta(minutes=6),
            ended_at=started_at + timedelta(minutes=8),
            evidence=[{"query": "fallback diagnosis"}],
            decision={"retry": False},
            state_snapshot={"attempt": 2},
        )
        session.add_all([third_node, first_node, second_node])
        await session.flush()

        early_approval = Approval(
            id=approval_ids[0],
            execution_id=execution_id,
            workflow_state_id=node_ids[0],
            decision="approved",
            decided_by="change-manager",
            reason="Classification confirmed",
            evidence={"ticket": "CHG001"},
            decided_at=started_at + timedelta(minutes=2, seconds=30),
        )
        late_approval = Approval(
            id=approval_ids[1],
            execution_id=execution_id,
            workflow_state_id=node_ids[2],
            decision="rejected",
            decided_by="incident-commander",
            reason="Stop automated retries",
            evidence={"channel": "operations"},
            decided_at=started_at + timedelta(minutes=9),
        )
        session.add_all([late_approval, early_approval])

        first_failure = Failure(
            failure_id=failure_ids[0],
            execution_id=execution_id,
            workflow_state_id=node_ids[1],
            attempt=1,
            failure_type="model_timeout",
            error_code="MODEL_TIMEOUT",
            message="Primary diagnosis timed out",
            details={"timeout_seconds": 30},
            retryable=True,
            occurred_at=started_at + timedelta(minutes=5),
        )
        final_failure = Failure(
            failure_id=failure_ids[1],
            execution_id=execution_id,
            workflow_state_id=node_ids[2],
            attempt=2,
            failure_type="diagnosis_failed",
            error_code=None,
            message="Fallback diagnosis produced no safe action",
            details={"candidate_count": 0},
            retryable=False,
            occurred_at=started_at + timedelta(minutes=8),
        )
        session.add_all([final_failure, first_failure])
        await session.flush()

        session.add(
            RetryState(
                retry_state_id=retry_state_id,
                execution_id=execution_id,
                state="exhausted",
                attempt_count=2,
                max_attempts=2,
                next_retry_at=None,
                last_attempt_at=started_at + timedelta(minutes=8),
                last_failure_id=failure_ids[1],
                backoff_seconds=30,
                created_at=started_at + timedelta(minutes=3),
                updated_at=started_at + timedelta(minutes=8),
            )
        )

    audit = await reconstruct_execution(session_factory, execution_id)

    assert isinstance(audit, ExecutionAudit)
    assert audit.execution_id == execution_id
    assert audit.event_record_id == event_record_id
    assert audit.incident_sys_id == "0123456789abcdef0123456789abcdef"
    assert audit.status == "failed"
    assert audit.node_reached == "diagnose"
    assert audit.model_name == "audit-model-v1"
    assert audit.agent_version == "agent-2.0.0"
    assert audit.started_at == started_at
    assert audit.ended_at == started_at + timedelta(minutes=15)
    assert audit.duration == timedelta(minutes=15)
    assert audit.termination_cause == "retry budget exhausted"
    assert audit.started_at.utcoffset() == timedelta(0)

    assert audit.event.event_record_id == event_record_id
    assert audit.event.event_id == "audit-event-complete"
    assert audit.event.incident_number == "INC0012300"
    assert audit.event.event_type == "incident.created"
    assert audit.event.contract_version == "v1"
    assert audit.event.received_at == started_at - timedelta(minutes=1)

    assert [entry.workflow_state_id for entry in audit.workflow_history] == list(node_ids)
    assert [entry.sequence_number for entry in audit.workflow_history] == [1, 2, 3]
    assert [entry.node_name for entry in audit.workflow_history] == [
        "classify",
        "diagnose",
        "diagnose",
    ]
    assert [entry.attempt for entry in audit.workflow_history] == [1, 1, 2]
    assert audit.workflow_history[0].duration == timedelta(minutes=1)
    assert audit.workflow_history[0].evidence == [{"source": "incident", "score": 0.95}]
    assert audit.workflow_history[1].decision == {"retry": True}
    assert audit.workflow_history[2].state_snapshot == {"attempt": 2}

    assert [entry.approval_id for entry in audit.approvals] == list(approval_ids)
    assert audit.approvals[0].decision == "approved"
    assert audit.approvals[0].decided_by == "change-manager"
    assert audit.approvals[1].reason == "Stop automated retries"
    assert audit.approvals[1].evidence == {"channel": "operations"}

    assert [entry.failure_id for entry in audit.failures] == list(failure_ids)
    assert audit.failures[0].attempt == 1
    assert audit.failures[0].retryable is True
    assert audit.failures[0].details == {"timeout_seconds": 30}
    assert audit.failures[1].failure_type == "diagnosis_failed"
    assert audit.failures[1].retryable is False

    assert audit.retry_state is not None
    assert audit.retry_state.retry_state_id == retry_state_id
    assert audit.retry_state.state == "exhausted"
    assert audit.retry_state.attempt_count == 2
    assert audit.retry_state.max_attempts == 2
    assert audit.retry_state.next_retry_at is None
    assert audit.retry_state.last_failure_id == failure_ids[1]
    assert audit.retry_state.backoff_seconds == 30


async def test_returns_none_for_unknown_execution(session_factory: SessionFactory) -> None:
    assert await reconstruct_execution(session_factory, uuid4()) is None


async def test_reconstructs_running_execution_with_nullable_and_absent_state(
    session_factory: SessionFactory,
) -> None:
    event_record_id = uuid4()
    execution_id = uuid4()
    workflow_state_id = uuid4()
    started_at = datetime(2026, 1, 11, 9, 0, tzinfo=UTC)

    async with session_factory() as session, session.begin():
        session.add(
            Event(
                id=event_record_id,
                event_id="audit-event-running",
                incident_sys_id="fedcba9876543210fedcba9876543210",
                incident_number="INC0012301",
                event_type="incident.updated",
                contract_version="v1",
                received_at=started_at - timedelta(seconds=5),
            )
        )
        session.add(
            Execution(
                execution_id=execution_id,
                event_record_id=event_record_id,
                incident_sys_id="fedcba9876543210fedcba9876543210",
                status="running",
                node_reached="retrieve",
                model_name=None,
                agent_version="agent-2.0.0",
                started_at=started_at,
                ended_at=None,
                termination_cause=None,
            )
        )
        await session.flush()
        session.add(
            ExecutionNodeState(
                id=workflow_state_id,
                execution_id=execution_id,
                sequence_number=1,
                node_name="retrieve",
                attempt=1,
                status="started",
                started_at=started_at + timedelta(seconds=1),
                ended_at=None,
                evidence=[],
                decision=None,
                state_snapshot=None,
            )
        )

    audit = await reconstruct_execution(session_factory, execution_id)

    assert audit is not None
    assert audit.status == "running"
    assert audit.ended_at is None
    assert audit.termination_cause is None
    assert audit.duration is None
    assert len(audit.workflow_history) == 1
    assert audit.workflow_history[0].workflow_state_id == workflow_state_id
    assert audit.workflow_history[0].ended_at is None
    assert audit.workflow_history[0].duration is None
    assert audit.workflow_history[0].decision is None
    assert audit.workflow_history[0].state_snapshot is None
    assert audit.approvals == ()
    assert audit.failures == ()
    assert audit.retry_state is None
