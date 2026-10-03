"""Crash reaper, against the real migrated schema.

Reproduces the defect this exists to fix. On dev407364, 2026-09-28, the Celery
worker was SIGKILLed while an execution sat at the ``validate`` node. After the
worker restarted the row was still ``running`` at that node 90 seconds later and
never moved again; 96 such rows had already accumulated. ``abandoned`` was in
``ck_executions_status`` the whole time and nothing ever set it, because nothing in
the schema could distinguish a live run from a dead one.

These run against PostgreSQL rather than SQLite on purpose. The behaviour under test
is exactly the ``ck_executions_terminal_state`` check constraint — the reaper's write
must satisfy it or the sweep dies on the first orphan — and SQLite does not enforce
it. Running the migrations also keeps ``0003_execution_lease`` covered: if the
migration and the model ever disagree, this module cannot set up.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Event, Execution, RetryState
from app.workers.reaper import (
    DEFAULT_GRACE_SECONDS,
    RECLAIMABLE_STATUSES,
    reap_stale_executions,
)

NOW = dt.datetime(2026, 9, 28, 18, 0, 0, tzinfo=dt.UTC)
TIME_LIMIT = 150


def _database_url() -> URL:
    """The test database, over psycopg3 for this module's own session.

    Alembic insists on ``postgresql+asyncpg`` (``migrations/env.py``), so the two
    drivers coexist: this URL is for the reaper's session, and the asyncpg form is
    handed to Alembic in the fixture.
    """
    raw = os.getenv("BARQ_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set BARQ_TEST_DATABASE_URL to a barq_s2_2_test* database.")
    url = make_url(raw).set(drivername="postgresql+psycopg")
    if not url.database or not url.database.startswith("barq_s2_2_test"):
        pytest.fail("Refusing to reset a database not named barq_s2_2_test*.")
    return url


def _reset_schema(url: URL) -> None:
    engine = create_engine(url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def reaper_session() -> Iterator[Session]:
    """A session on a schema built by running the real migrations.

    Synchronous throughout (psycopg3): the reaper itself is sync, and the async
    driver is not needed to set a schema up.
    """
    url = _database_url()
    _reset_schema(url)

    repo_root = Path(__file__).resolve().parents[1]
    previous = os.environ.get("BARQ_DATABASE_URL")
    os.environ["BARQ_DATABASE_URL"] = url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    try:
        command.upgrade(Config(str(repo_root / "alembic.ini")), "head")
        engine = create_engine(url, future=True)
        with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as session:
            yield session
        engine.dispose()
    finally:
        _reset_schema(url)
        if previous is None:
            os.environ.pop("BARQ_DATABASE_URL", None)
        else:
            os.environ["BARQ_DATABASE_URL"] = previous


@pytest.fixture(autouse=True)
def _clean(reaper_session: Session) -> Iterator[None]:
    """Empty the tables before each test.

    The migrated schema is module-scoped (running Alembic per test would be wasteful),
    so each test starts from an empty slate rather than inheriting the previous test's
    rows — the reaper sweeps the whole table, so leftovers would change its verdict.
    The rollback first matters too: a failed flush leaves the shared session in
    PendingRollbackError, which would otherwise cascade into every later test and
    hide the real failure behind a confusing one.
    """
    reaper_session.rollback()
    reaper_session.execute(
        sa.text(
            "TRUNCATE workflow_state, failures, retry_state, executions, events "
            "RESTART IDENTITY CASCADE"
        )
    )
    reaper_session.commit()
    yield


def _execution(session: Session, **overrides: object) -> Execution:
    # executions.event_record_id is a real foreign key, so the parent row has to
    # exist. Using the real schema rather than SQLite is exactly what catches this.
    event_id = uuid4()
    session.add(
        Event(
            id=event_id,
            event_id=f"evt-{event_id.hex[:12]}",
            incident_sys_id="b" * 32,
            incident_number="INC0099999",
            event_type="incident.created",
            contract_version="v1",
        )
    )
    session.flush()
    values: dict = {
        "execution_id": uuid4(),
        "event_record_id": event_id,
        "incident_sys_id": "a" * 32,
        "status": "running",
        # Pinned to the injected clock rather than the server default, so
        # ck_executions_time_order (ended_at >= started_at) is evaluated against a
        # timeline this module controls end to end.
        "started_at": NOW,
    }
    values.update(overrides)  # tests override status to model a paused/finished run
    row = Execution(**values)  # type: ignore[arg-type]
    session.add(row)
    session.commit()
    return row


def _retry_state(session: Session, execution_id: object) -> None:
    # ck_retry_state_last_attempt_consistency ties attempt_count to last_attempt_at,
    # so an attempt_count of 1 has to carry a timestamp.
    session.add(
        RetryState(
            execution_id=execution_id,
            state="ready",
            attempt_count=1,
            max_attempts=3,
            last_attempt_at=NOW,
        )
    )
    session.commit()


def _reap(session: Session, **kwargs: object):
    return reap_stale_executions(session, time_limit_seconds=TIME_LIMIT, now=NOW, **kwargs)  # type: ignore[arg-type]


def test_the_lease_columns_exist(reaper_session: Session) -> None:
    """``0003_execution_lease`` applied: the reaper has a clock to read."""
    engine = reaper_session.get_bind()
    columns = {c["name"] for c in sa.inspect(engine).get_columns("executions")}
    assert {"heartbeat_at", "worker_id"} <= columns


def test_an_overdue_run_is_abandoned(reaper_session: Session) -> None:
    row = _execution(
        reaper_session,
        heartbeat_at=NOW - dt.timedelta(seconds=TIME_LIMIT + DEFAULT_GRACE_SECONDS + 60),
        worker_id="host-a:42",
    )
    _retry_state(reaper_session, row.execution_id)

    report = _reap(reaper_session)
    reaper_session.commit()

    assert row.execution_id in report.reclaimed
    reaper_session.refresh(row)
    assert row.status == "abandoned"
    assert row.ended_at is not None
    # ck_executions_terminal_state demands a non-empty cause with a terminal status;
    # committing above proves the reaper's write satisfies it on real Postgres.
    assert row.termination_cause
    assert "crash_reaped" in row.termination_cause
    # The holder is named, so the abandonment says which worker died.
    assert row.worker_id == "host-a:42"
    assert row.heartbeat_at is None


def test_the_retry_row_is_closed_too(reaper_session: Session) -> None:
    """A reclaimed run must not sit in retry_state looking retryable forever.

    Also the regression guard for the sweep surviving its own write: an
    unacceptable state value here fails the check constraint and rolls the whole
    sweep back, so the reaper would never reclaim anything.
    """
    row = _execution(
        reaper_session,
        heartbeat_at=NOW - dt.timedelta(seconds=TIME_LIMIT + DEFAULT_GRACE_SECONDS + 30),
    )
    _retry_state(reaper_session, row.execution_id)

    _reap(reaper_session)
    reaper_session.commit()

    # RetryState's primary key is retry_state_id; execution_id is a unique column,
    # so it has to be selected on rather than fetched by primary key.
    state = reaper_session.scalar(
        sa.select(RetryState).where(RetryState.execution_id == row.execution_id)
    )
    assert state is not None
    # 'cancelled' is the only honest value the constraint allows: 'abandoned' is not
    # in ck_retry_state_state, and writing it aborted the sweep on its first orphan.
    assert state.state == "cancelled"
    assert state.next_retry_at is None


def test_a_live_run_is_left_alone(reaper_session: Session) -> None:
    """A run inside the window is making progress, however slowly."""
    row = _execution(
        reaper_session, heartbeat_at=NOW - dt.timedelta(seconds=30), worker_id="host-b:7"
    )

    report = _reap(reaper_session)
    reaper_session.commit()

    assert report.reclaimed == ()
    reaper_session.refresh(row)
    assert row.status == "running"
    assert row.ended_at is None


def test_a_run_exactly_on_the_window_is_kept(reaper_session: Session) -> None:
    """The window is a strict bound: at the limit the run is still assumed live."""
    row = _execution(
        reaper_session,
        heartbeat_at=NOW - dt.timedelta(seconds=TIME_LIMIT + DEFAULT_GRACE_SECONDS),
    )

    assert _reap(reaper_session).reclaimed == ()
    reaper_session.commit()
    assert row.status == "running"


def test_a_null_heartbeat_is_treated_as_stale(reaper_session: Session) -> None:
    """Rows predating the lease columns hold no known lease, so they are reclaimable."""
    row = _execution(reaper_session, heartbeat_at=None)

    assert row.execution_id in _reap(reaper_session).reclaimed
    reaper_session.commit()
    assert row.status == "abandoned"


def test_a_paused_run_is_never_reaped(reaper_session: Session) -> None:
    """``awaiting_approval`` is waiting for a human, which can take days.

    Reaping it would destroy a HITL thread that is working exactly as designed.
    """
    row = _execution(
        reaper_session,
        status="awaiting_approval",
        heartbeat_at=NOW - dt.timedelta(days=7),
    )

    assert _reap(reaper_session).reclaimed == ()
    reaper_session.commit()
    assert row.status == "awaiting_approval"
    assert "awaiting_approval" not in RECLAIMABLE_STATUSES


def test_a_finished_run_is_never_reaped(reaper_session: Session) -> None:
    row = _execution(
        reaper_session,
        status="succeeded",
        # A completed run: started, then finished an hour later, then sat untouched.
        # ck_executions_time_order needs ended_at >= started_at.
        started_at=NOW - dt.timedelta(hours=2),
        ended_at=NOW - dt.timedelta(hours=1),
        termination_cause="suggested",
        heartbeat_at=NOW - dt.timedelta(hours=1),
    )

    assert _reap(reaper_session).reclaimed == ()
    reaper_session.commit()
    assert row.status == "succeeded"


def test_a_sweep_with_nothing_to_do_reports_nothing(reaper_session: Session) -> None:
    report = _reap(reaper_session)
    assert report.reclaimed == ()
    assert report.examined == 0


def test_one_sweep_reclaims_every_stale_row_not_just_the_first(reaper_session: Session) -> None:
    stale = [
        _execution(
            reaper_session,
            heartbeat_at=NOW - dt.timedelta(seconds=TIME_LIMIT + DEFAULT_GRACE_SECONDS + 60),
        )
        for _ in range(3)
    ]
    _execution(reaper_session, heartbeat_at=NOW - dt.timedelta(seconds=10))  # live, must survive

    report = _reap(reaper_session)
    reaper_session.commit()

    assert set(report.reclaimed) == {r.execution_id for r in stale}
    assert report.examined == 3


def test_a_first_crash_is_queued_again_once(reaper_session: Session) -> None:
    """Seen live 2026-10-03: the memory killer stopped a worker mid-ticket and the ticket
    stayed New with no team and no message. The first crash puts the run back in the
    queue (with its original event), the second one abandons it."""
    from app.db.models import Failure
    from app.workers.reaper import WORKER_LOST, event_payloads

    stale = NOW - dt.timedelta(seconds=TIME_LIMIT + DEFAULT_GRACE_SECONDS + 60)
    row = _execution(reaper_session, heartbeat_at=stale)
    _retry_state(reaper_session, row.execution_id)

    first = _reap(reaper_session, requeue_once=True)
    payloads = event_payloads(reaper_session, first.requeued)
    reaper_session.commit()
    reaper_session.refresh(row)
    assert first.requeued == (row.execution_id,)
    assert first.reclaimed == ()
    assert row.status == "queued"
    assert row.ended_at is None and row.termination_cause is None
    assert payloads[row.execution_id]["number"] == "INC0099999"
    assert payloads[row.execution_id]["event_type"] == "incident.created"
    crashes = reaper_session.scalars(
        sa.select(Failure).where(Failure.execution_id == row.execution_id)
    ).all()
    assert [failure.failure_type for failure in crashes] == [WORKER_LOST]

    # The retried attempt dies too: this time it is abandoned, not queued again.
    reaper_session.execute(
        sa.update(Execution)
        .where(Execution.execution_id == row.execution_id)
        .values(status="running", heartbeat_at=stale)
    )
    reaper_session.commit()
    second = _reap(reaper_session, requeue_once=True)
    reaper_session.commit()
    reaper_session.refresh(row)
    assert second.requeued == ()
    assert second.reclaimed == (row.execution_id,)
    assert row.status == "abandoned"


def test_without_requeue_a_crash_is_abandoned_as_before(reaper_session: Session) -> None:
    row = _execution(
        reaper_session,
        heartbeat_at=NOW - dt.timedelta(seconds=TIME_LIMIT + DEFAULT_GRACE_SECONDS + 60),
    )
    report = _reap(reaper_session)
    reaper_session.commit()
    assert report.requeued == ()
    assert report.reclaimed == (row.execution_id,)
