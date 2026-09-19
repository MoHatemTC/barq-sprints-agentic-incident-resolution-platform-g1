"""Worker repository: the worker's only door into PostgreSQL.

Every database write the worker performs goes through :class:`WorkerRepo`.
Two implementations share one contract (see tests/workers/repo_contract.py):

- :class:`PostgresRepo` — production, on top of the sync engine
  (:mod:`app.workers.sync_engine`) and Ahmed's ORM models.
- :class:`InMemoryRepo` — unit-test stand-in that mirrors the database CHECK
  constraints, so state-machine violations surface in fast tests.

Constraint map (why each method writes what it writes — see
docs/sprint2_worker_topology.md):

- ``ck_retry_state_exhausted_attempt_limit``: exhausted ⟺ attempt_count ==
  max_attempts. Terminal (poison) events are 'cancelled', never exhausted with
  a forged count.
- ``ck_retry_state_active_retry_remaining``: 'scheduled' requires
  attempt_count < max_attempts — the caller must never schedule past the last
  attempt.
- ``ck_retry_state_last_attempt_consistency`` / ``ck_retry_state_schedule_time``:
  replay resets attempt_count, last_attempt_at and next_retry_at together.
- ``ck_executions_terminal_state``: terminal statuses require ended_at AND a
  non-empty termination_cause; non-terminal require both NULL.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import Event, Execution, Failure, RetryState
from app.workers.sync_engine import (
    SyncPostgreSQLSettings,
    SyncSessionFactory,
    build_sync_database_url,
    create_sync_engine,
    create_sync_session_factory,
)

_QUEUED_STATUSES = ("accepted", "queued")
_CLAIMABLE_STATUSES = ("accepted", "queued", "running")
_PARKED_RETRY_STATES = ("exhausted", "cancelled")


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class WorkerRepo(Protocol):
    """The worker's state operations. All methods are synchronous and open
    their own short-lived session/transaction — safe to call from exception
    handlers after the main transaction has died."""

    def claim_for_running(self, execution_id: UUID) -> bool:
        """Atomically claim the execution for processing.

        Succeeds only from a claimable status ('accepted'/'queued'/'running' —
        the self-transition covers redelivery after a hard kill, which is safe
        because the queue guarantees a single live delivery). Returns False
        when the execution is terminal/unknown: the caller must no-op.
        """
        ...

    def get_status(self, execution_id: UUID) -> str | None: ...

    def ensure_retry_state(self, execution_id: UUID, max_attempts: int) -> None:
        """Insert the retry_state row if absent (accept_inbound_event does not
        create one, and max_attempts has no database default). If the row
        exists but is untouched ('ready', no attempts), it aligns to the
        caller's budget — closing the replay-CLI/worker max_retries drift.
        Rows with burned attempts keep their recorded budget."""
        ...

    def get_attempt_count(self, execution_id: UUID) -> int | None: ...

    def log_failure(
        self,
        execution_id: UUID,
        attempt: int,
        failure_type: str,
        message: str,
        retryable: bool,
        details: dict | None = None,
    ) -> UUID:
        """Append one failures row (the Execution Log obligation: every
        attempt, including failures) and return its id for last_failure_id."""
        ...

    def count_failures(self, execution_id: UUID) -> int: ...

    def schedule_retry(
        self,
        execution_id: UUID,
        attempt: int,
        backoff_seconds: float,
        next_retry_at: dt.datetime,
        last_failure_id: UUID,
    ) -> None:
        """Record a scheduled retry. Also returns the execution to 'queued':
        during backoff nobody works on the event."""
        ...

    def mark_exhausted(self, execution_id: UUID, max_attempts: int, last_failure_id: UUID) -> None:
        """Budget consumed: state='exhausted' with attempt_count == max_attempts
        (database-enforced pairing), execution failed."""
        ...

    def mark_cancelled(
        self,
        execution_id: UUID,
        attempt: int,
        last_failure_id: UUID,
        termination_cause: str,
    ) -> None:
        """Terminal failure without exhausting the budget ('cancelled'): the
        honest state for poison events that died early. attempt_count records
        the attempt that failed — history is never forged to max_attempts."""
        ...

    def mark_succeeded(
        self,
        execution_id: UUID,
        *,
        node_reached: str | None = None,
        termination_cause: str = "completed",
        model_name: str | None = None,
        agent_version: str | None = None,
    ) -> None: ...

    def get_termination_cause(self, execution_id: UUID) -> str | None: ...

    def get_retry_state(self, execution_id: UUID) -> dict | None: ...

    def reset_for_replay(self, execution_id: UUID, max_attempts: int) -> bool:
        """Unlock a parked (exhausted/cancelled) event for replay. Returns
        False when the event is not parked — never reset an in-flight event."""
        ...

    def find_execution_id(self, event_id: str) -> UUID | None:
        """Resolve the one execution accepted for an event (uq_executions_
        event_record_id guarantees at most one). The replay CLI's entry point."""
        ...

    def get_event_payload(self, event_id: str) -> dict | None:
        """Rebuild the contract-v1 webhook payload from the immutable events
        row — replay re-enqueues the ORIGINAL event, not the DLQ copy."""
        ...


class PostgresRepo:
    """Production backend on Ahmed's ORM models + the sync engine."""

    def __init__(self, session_factory: SyncSessionFactory) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_settings(cls, settings: SyncPostgreSQLSettings) -> PostgresRepo:
        engine = create_sync_engine(build_sync_database_url(settings))
        return cls(create_sync_session_factory(engine))

    def claim_for_running(self, execution_id: UUID) -> bool:
        stmt = (
            update(Execution)
            .where(
                Execution.execution_id == execution_id,
                Execution.status.in_(_CLAIMABLE_STATUSES),
            )
            .values(status="running")
            .returning(Execution.execution_id)
        )
        with self._session_factory() as session, session.begin():
            return session.execute(stmt).first() is not None

    def get_status(self, execution_id: UUID) -> str | None:
        with self._session_factory() as session:
            return session.scalar(
                select(Execution.status).where(Execution.execution_id == execution_id)
            )

    def ensure_retry_state(self, execution_id: UUID, max_attempts: int) -> None:
        """Insert the retry_state row if absent (accept_inbound_event does not
        create one, and max_attempts has no database default).

        Config-drift alignment: if the row exists but is still untouched —
        ``('ready', attempt_count = 0)``, e.g. written by a replay CLI running
        a different ``worker_max_retries`` — it adopts the caller's budget, so
        the caller's later exhaustion write can never violate
        ``ck_retry_state_exhausted_attempt_limit``. Rows with burned attempts
        keep their recorded budget: history is never rewritten."""
        stmt = (
            pg_insert(RetryState)
            .values(
                execution_id=execution_id,
                state="ready",
                attempt_count=0,
                max_attempts=max_attempts,
            )
            .on_conflict_do_update(
                index_elements=[RetryState.execution_id],
                set_={"max_attempts": max_attempts},
                where=(RetryState.state == "ready") & (RetryState.attempt_count == 0),
            )
        )
        with self._session_factory() as session, session.begin():
            session.execute(stmt)

    def get_attempt_count(self, execution_id: UUID) -> int | None:
        with self._session_factory() as session:
            return session.scalar(
                select(RetryState.attempt_count).where(RetryState.execution_id == execution_id)
            )

    def log_failure(
        self,
        execution_id: UUID,
        attempt: int,
        failure_type: str,
        message: str,
        retryable: bool,
        details: dict | None = None,
    ) -> UUID:
        failure = Failure(
            execution_id=execution_id,
            attempt=attempt,
            failure_type=failure_type,
            message=message,
            retryable=retryable,
            details=details,
        )
        with self._session_factory() as session, session.begin():
            session.add(failure)
            session.flush()
            return failure.failure_id

    def count_failures(self, execution_id: UUID) -> int:
        with self._session_factory() as session:
            failures = session.scalars(
                select(Failure.failure_id).where(Failure.execution_id == execution_id)
            ).all()
            return len(failures)

    def schedule_retry(
        self,
        execution_id: UUID,
        attempt: int,
        backoff_seconds: float,
        next_retry_at: dt.datetime,
        last_failure_id: UUID,
    ) -> None:
        with self._session_factory() as session, session.begin():
            session.execute(
                update(Execution)
                .where(Execution.execution_id == execution_id)
                .values(status="queued", ended_at=None, termination_cause=None)
            )
            session.execute(
                update(RetryState)
                .where(RetryState.execution_id == execution_id)
                .values(
                    attempt_count=attempt,
                    state="scheduled",
                    backoff_seconds=int(backoff_seconds),
                    next_retry_at=next_retry_at,
                    last_attempt_at=_utcnow(),
                    last_failure_id=last_failure_id,
                )
            )

    def mark_exhausted(self, execution_id: UUID, max_attempts: int, last_failure_id: UUID) -> None:
        self._mark_execution_failed(
            execution_id, last_failure_id, "max retries exhausted", "exhausted", max_attempts
        )

    def mark_cancelled(
        self,
        execution_id: UUID,
        attempt: int,
        last_failure_id: UUID,
        termination_cause: str,
    ) -> None:
        self._mark_execution_failed(
            execution_id,
            last_failure_id,
            termination_cause,
            "cancelled",
            None,
            attempt_count=attempt,
        )

    def _mark_execution_failed(
        self,
        execution_id: UUID,
        last_failure_id: UUID,
        termination_cause: str,
        retry_state: str,
        max_attempts: int | None,
        *,
        attempt_count: int | None = None,
    ) -> None:
        values: dict = {
            "state": retry_state,
            "next_retry_at": None,
            "last_attempt_at": _utcnow(),
            "last_failure_id": last_failure_id,
        }
        if max_attempts is not None:
            # exhausted ⟺ attempt_count == max_attempts (database-enforced).
            values["attempt_count"] = max_attempts
        elif attempt_count is not None:
            values["attempt_count"] = attempt_count
        with self._session_factory() as session, session.begin():
            session.execute(
                update(Execution)
                .where(Execution.execution_id == execution_id)
                .values(
                    status="failed",
                    ended_at=_utcnow(),
                    termination_cause=termination_cause,
                )
            )
            session.execute(
                update(RetryState).where(RetryState.execution_id == execution_id).values(**values)
            )

    def mark_succeeded(
        self,
        execution_id: UUID,
        *,
        node_reached: str | None = None,
        termination_cause: str = "completed",
        model_name: str | None = None,
        agent_version: str | None = None,
    ) -> None:
        # The optional arguments let the graph record what it decided on the
        # executions summary row. Without them the row reads "completed" for a
        # high-risk escalation, which is the one outcome an auditor most needs to
        # see. node_reached exists for this ("Latest workflow node entered").
        summary: dict[str, Any] = {
            "status": "succeeded",
            "ended_at": _utcnow(),
            "termination_cause": termination_cause,
        }
        if node_reached is not None:
            summary["node_reached"] = node_reached
        if model_name is not None:
            summary["model_name"] = model_name
        if agent_version is not None:
            summary["agent_version"] = agent_version
        with self._session_factory() as session, session.begin():
            session.execute(
                update(Execution).where(Execution.execution_id == execution_id).values(**summary)
            )

            session.execute(
                update(RetryState)
                .where(RetryState.execution_id == execution_id)
                .values(state="succeeded", next_retry_at=None)
            )

    def get_termination_cause(self, execution_id: UUID) -> str | None:
        with self._session_factory() as session:
            return session.scalar(
                select(Execution.termination_cause).where(Execution.execution_id == execution_id)
            )

    def get_retry_state(self, execution_id: UUID) -> dict | None:
        with self._session_factory() as session:
            row = session.execute(
                select(
                    RetryState.state,
                    RetryState.attempt_count,
                    RetryState.max_attempts,
                    RetryState.next_retry_at,
                ).where(RetryState.execution_id == execution_id)
            ).first()
            if row is None:
                return None
            return {
                "state": row.state,
                "attempt_count": row.attempt_count,
                "max_attempts": row.max_attempts,
                "next_retry_at": row.next_retry_at,
            }

    def reset_for_replay(self, execution_id: UUID, max_attempts: int) -> bool:
        claimed = (
            update(RetryState)
            .where(
                RetryState.execution_id == execution_id,
                RetryState.state.in_(_PARKED_RETRY_STATES),
            )
            .values(
                attempt_count=0,
                last_attempt_at=None,
                next_retry_at=None,
                state="ready",
                max_attempts=max_attempts,
            )
            .returning(RetryState.retry_state_id)
        )
        with self._session_factory() as session, session.begin():
            claimed_row = session.execute(claimed).first()
            if claimed_row is None:
                return False
            session.execute(
                update(Execution)
                .where(Execution.execution_id == execution_id)
                .values(status="queued", ended_at=None, termination_cause=None)
            )
            return True

    def find_execution_id(self, event_id: str) -> UUID | None:
        stmt = (
            select(Execution.execution_id)
            .join(Event, Execution.event_record_id == Event.id)
            .where(Event.event_id == event_id)
        )
        with self._session_factory() as session:
            return session.scalar(stmt)

    def get_event_payload(self, event_id: str) -> dict | None:
        stmt = select(
            Event.event_id,
            Event.incident_sys_id,
            Event.incident_number,
            Event.event_type,
            Event.contract_version,
        ).where(Event.event_id == event_id)
        with self._session_factory() as session:
            row = session.execute(stmt).first()
        if row is None:
            return None
        return {
            "event_id": row.event_id,
            "sys_id": row.incident_sys_id,
            "number": row.incident_number,
            "event_type": row.event_type,
            "contract_version": row.contract_version,
        }


class InMemoryRepo:
    """Unit-test stand-in mirroring the database CHECK constraints."""

    def __init__(self) -> None:
        self.executions: dict[UUID, dict] = {}
        self.retry_states: dict[UUID, dict] = {}
        self.failures: list[dict] = []
        self.event_index: dict[str, UUID] = {}
        self.event_payloads: dict[str, dict] = {}

    # -- test seeding -------------------------------------------------------
    def seed_execution(
        self,
        execution_id: UUID,
        *,
        status: str,
        event_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        self.executions[execution_id] = {
            "status": status,
            "ended_at": None,
            "termination_cause": None,
        }
        if event_id is not None:
            self.event_index[event_id] = execution_id
            if payload is not None:
                self.event_payloads[event_id] = payload

    def force_state_for_test(self, execution_id: UUID, state: str) -> None:
        self.retry_states[execution_id]["state"] = state

    # -- protocol -----------------------------------------------------------
    def claim_for_running(self, execution_id: UUID) -> bool:
        row = self.executions.get(execution_id)
        if row is None or row["status"] not in _CLAIMABLE_STATUSES:
            return False
        row["status"] = "running"
        return True

    def get_status(self, execution_id: UUID) -> str | None:
        row = self.executions.get(execution_id)
        return row["status"] if row else None

    def ensure_retry_state(self, execution_id: UUID, max_attempts: int) -> None:
        state = self.retry_states.get(execution_id)
        if state is None:
            self.retry_states[execution_id] = {
                "state": "ready",
                "attempt_count": 0,
                "max_attempts": max_attempts,
                "next_retry_at": None,
                "last_attempt_at": None,
                "last_failure_id": None,
            }
        elif state["state"] == "ready" and state["attempt_count"] == 0:
            # drift alignment: mirror the Postgres backend's untouched-row rule
            state["max_attempts"] = max_attempts

    def get_attempt_count(self, execution_id: UUID) -> int | None:
        state = self.retry_states.get(execution_id)
        return state["attempt_count"] if state else None

    def log_failure(
        self,
        execution_id: UUID,
        attempt: int,
        failure_type: str,
        message: str,
        retryable: bool,
        details: dict | None = None,
    ) -> UUID:
        failure_id = uuid4()
        self.failures.append(
            {
                "failure_id": failure_id,
                "execution_id": execution_id,
                "attempt": attempt,
                "failure_type": failure_type,
                "message": message,
                "retryable": retryable,
                "occurred_at": _utcnow(),
            }
        )
        return failure_id

    def count_failures(self, execution_id: UUID) -> int:
        return sum(1 for f in self.failures if f["execution_id"] == execution_id)

    def schedule_retry(
        self,
        execution_id: UUID,
        attempt: int,
        backoff_seconds: float,
        next_retry_at: dt.datetime,
        last_failure_id: UUID,
    ) -> None:
        row = self.retry_states[execution_id]
        row.update(
            attempt_count=attempt,
            state="scheduled",
            backoff_seconds=int(backoff_seconds),
            next_retry_at=next_retry_at,
            last_attempt_at=_utcnow(),
            last_failure_id=last_failure_id,
        )
        execution = self.executions[execution_id]
        execution["status"] = "queued"
        execution["ended_at"] = None
        execution["termination_cause"] = None

    def mark_exhausted(self, execution_id: UUID, max_attempts: int, last_failure_id: UUID) -> None:
        self._fail(
            execution_id, last_failure_id, "max retries exhausted", "exhausted", max_attempts
        )

    def mark_cancelled(
        self,
        execution_id: UUID,
        attempt: int,
        last_failure_id: UUID,
        termination_cause: str,
    ) -> None:
        self._fail(
            execution_id,
            last_failure_id,
            termination_cause,
            "cancelled",
            None,
            attempt_count=attempt,
        )

    def _fail(
        self,
        execution_id: UUID,
        last_failure_id: UUID,
        termination_cause: str,
        retry_state: str,
        max_attempts: int | None,
        *,
        attempt_count: int | None = None,
    ) -> None:
        execution = self.executions[execution_id]
        execution["status"] = "failed"
        execution["ended_at"] = _utcnow()
        execution["termination_cause"] = termination_cause
        row = self.retry_states[execution_id]
        row["state"] = retry_state
        row["next_retry_at"] = None
        row["last_attempt_at"] = _utcnow()
        row["last_failure_id"] = last_failure_id
        if max_attempts is not None:
            row["attempt_count"] = max_attempts
        elif attempt_count is not None:
            row["attempt_count"] = attempt_count

    def mark_succeeded(
        self,
        execution_id: UUID,
        *,
        node_reached: str | None = None,
        termination_cause: str = "completed",
        model_name: str | None = None,
        agent_version: str | None = None,
    ) -> None:
        execution = self.executions[execution_id]
        execution["status"] = "succeeded"
        execution["ended_at"] = _utcnow()
        execution["termination_cause"] = termination_cause
        if node_reached is not None:
            execution["node_reached"] = node_reached
        if model_name is not None:
            execution["model_name"] = model_name
        if agent_version is not None:
            execution["agent_version"] = agent_version

        row = self.retry_states[execution_id]
        row["state"] = "succeeded"
        row["next_retry_at"] = None

    def get_termination_cause(self, execution_id: UUID) -> str | None:
        row = self.executions.get(execution_id)
        return row["termination_cause"] if row else None

    def get_retry_state(self, execution_id: UUID) -> dict | None:
        return self.retry_states.get(execution_id)

    def reset_for_replay(self, execution_id: UUID, max_attempts: int) -> bool:
        row = self.retry_states.get(execution_id)
        if row is None or row["state"] not in _PARKED_RETRY_STATES:
            return False
        row.update(
            attempt_count=0,
            last_attempt_at=None,
            next_retry_at=None,
            state="ready",
            max_attempts=max_attempts,
        )
        execution = self.executions[execution_id]
        execution["status"] = "queued"
        execution["ended_at"] = None
        execution["termination_cause"] = None
        return True

    def find_execution_id(self, event_id: str) -> UUID | None:
        return self.event_index.get(event_id)

    def get_event_payload(self, event_id: str) -> dict | None:
        return self.event_payloads.get(event_id)


def build_worker_repo(settings: SyncPostgreSQLSettings | object) -> WorkerRepo:
    """Factory honouring ``worker_repo_backend``: 'memory' for tests, else Postgres."""
    backend = getattr(settings, "worker_repo_backend", "postgres")
    if backend == "memory":
        return InMemoryRepo()
    return PostgresRepo.from_settings(settings)  # type: ignore[arg-type]


__all__ = [
    "InMemoryRepo",
    "PostgresRepo",
    "WorkerRepo",
    "build_worker_repo",
]
