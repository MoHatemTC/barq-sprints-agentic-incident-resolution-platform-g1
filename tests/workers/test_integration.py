"""Integration suite (S2.3): real Redis broker, real PostgreSQL, real worker.

Requires the docker services and runs the full joint definition-of-done with
S2.1 (webhook) and S2.2 (schema)::

    just test-integration        # stops the compose celery-worker first
    # or manually: docker compose stop celery-worker && pytest -m integration

The suite assumes EXCLUSIVE consumption: a compose worker running alongside
the test worker shares the queue with a different retry budget, which is
exactly the config-drift incident the drift-guard test below pins.

A real Celery worker subprocess consumes the actual events queue with a test-
scaled configuration (budget 3, base 0.2s, jitter off → deterministic 1:2
delays), so what is measured is the shipped machinery — broker round-trips,
ETA scheduling, on_failure hooks — not a mock. Deselected by default; run with
``pytest -m integration``.

PostgresRepo is exercised through these real flows (the in-memory twin covers
the same contract in fast tests). The worker subprocess logs to a tempfile
whose path is printed on teardown for debugging.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import subprocess
import sys
import tempfile
import time
from uuid import UUID, uuid4

import pytest
import redis as redis_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Base, Event, Execution, Failure
from app.db.redis.keys import INCIDENT_DLQ_QUEUE, INCIDENT_EVENTS_QUEUE
from app.db.session import create_db_engine, create_session_factory
from app.repositories.idempotency import (
    EventAcceptanceResult,
    EventAcceptanceStatus,
    InboundEvent,
    accept_inbound_event,
)
from app.workers.celery_app import celery_app
from app.workers.db import PostgresRepo
from app.workers.producer import send_incident_event
from app.workers.replay import load_dead_letters, replay_event
from app.workers.sync_engine import (
    build_sync_database_url,
    create_sync_engine,
    create_sync_session_factory,
)
from app.workers.tasks import GRAPH_STUB_SLEEP_SECONDS

pytestmark = pytest.mark.integration

# Test-scaled worker configuration: deterministic delays base*2**(attempt-1)
# = 0.2s, 0.4s (max_retries=3 → attempts 1..3, retries after 1 and 2).
WORKER_MAX_RETRIES = 3
WORKER_BACKOFF_BASE = 0.2
WORKER_BACKOFF_MAX = 0.5

# Every attempt burns the stub-graph sleep; an interval between two consecutive
# failures is therefore sleep + delay (+ scheduling overhead).
EXPECTED_INTERVALS = [
    GRAPH_STUB_SLEEP_SECONDS + WORKER_BACKOFF_BASE * 2**attempt
    for attempt in range(WORKER_MAX_RETRIES - 1)
]


def _wait_until(
    predicate,
    *,
    description: str,
    timeout: float = 30.0,
    interval: float = 0.05,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    pytest.fail(f"timed out after {timeout}s waiting for {description}")


def _make_payload(number: str, *, event_type: str = "incident.created") -> dict:
    """Contract-v1 webhook payload shape (what payload.model_dump() produces)."""
    return {
        "event_id": f"evt-{uuid4().hex[:24]}",
        "sys_id": uuid4().hex[:16],
        "number": number,
        "event_type": event_type,
        "contract_version": "v1",
    }


def _seed_event_with_execution(
    engine, payload: dict, *, execution_status: str = "accepted"
) -> UUID:
    """Insert the immutable event row plus its execution — the worker-level view
    of what the webhook's accept_inbound_event persists (that async path is
    exercised separately in the duplicate test)."""
    terminal = execution_status in ("succeeded", "failed", "blocked", "abandoned")
    with Session(engine) as session, session.begin():
        event = Event(
            event_id=payload["event_id"],
            incident_sys_id=payload["sys_id"],
            incident_number=payload["number"],
            event_type=payload["event_type"],
            contract_version="v1",
        )
        session.add(event)
        session.flush()
        execution = Execution(
            event_record_id=event.id,
            incident_sys_id=payload["sys_id"],
            status=execution_status,
            ended_at=dt.datetime.now(dt.UTC) if terminal else None,
            termination_cause="seeded terminal" if terminal else None,
        )
        session.add(execution)
        session.flush()
        return execution.execution_id


def _dlq_records_for(redis_client, event_id: str) -> list[dict]:
    return [
        record for record in load_dead_letters(redis_client) if record.get("event_id") == event_id
    ]


# ────────────────────────────── fixtures ──────────────────────────────


@pytest.fixture(scope="module")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="module")
def sync_engine(settings: Settings):
    engine = create_sync_engine(build_sync_database_url(settings))
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def schema(sync_engine):
    Base.metadata.create_all(sync_engine)


@pytest.fixture(scope="module")
def repo(schema, sync_engine) -> PostgresRepo:
    return PostgresRepo(create_sync_session_factory(sync_engine))


@pytest.fixture(scope="module")
def redis_client(settings: Settings):
    client = redis_lib.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=(
            settings.redis_password.get_secret_value()
            if settings.redis_password is not None
            else None
        ),
        decode_responses=True,
    )
    client.ping()
    yield client
    client.close()


@pytest.fixture()
def clean_queues(redis_client):
    redis_client.delete(INCIDENT_EVENTS_QUEUE, INCIDENT_DLQ_QUEUE)
    yield
    redis_client.delete(INCIDENT_EVENTS_QUEUE, INCIDENT_DLQ_QUEUE)


@pytest.fixture(scope="module")
def worker(settings: Settings, schema, tmp_path_factory):
    """Real Celery worker subprocess on the real broker, scaled for tests.

    Solo pool + concurrency 1: deterministic sequencing. Jitter off so the
    measured intervals are exactly sleep + base*2**(attempt-1).
    """
    env = {**os.environ}
    env.setdefault("WEBHOOK_AUTH_TOKEN", "integration-test-token")
    # Pin the subprocess to the SAME services the test process resolved from
    # .env, so both sides of the assertion can never drift apart.
    env.update(
        {
            "POSTGRES_HOST": settings.postgres_host,
            "POSTGRES_PORT": str(settings.postgres_port),
            "POSTGRES_DB": settings.postgres_db,
            "POSTGRES_USER": settings.postgres_user,
            "POSTGRES_PASSWORD": (
                settings.postgres_password.get_secret_value()
                if settings.postgres_password is not None
                else ""
            ),
            "REDIS_HOST": settings.redis_host,
            "REDIS_PORT": str(settings.redis_port),
            "REDIS_PASSWORD": (
                settings.redis_password.get_secret_value()
                if settings.redis_password is not None
                else ""
            ),
            "WORKER_MAX_RETRIES": str(WORKER_MAX_RETRIES),
            "WORKER_BACKOFF_BASE": str(WORKER_BACKOFF_BASE),
            "WORKER_BACKOFF_MAX": str(WORKER_BACKOFF_MAX),
            "WORKER_BACKOFF_JITTER": "false",
            "WORKER_REPO_BACKEND": "postgres",
        }
    )

    log_file = tempfile.NamedTemporaryFile(
        prefix="barq-worker-int-", suffix=".log", mode="w+", delete=False
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.workers.celery_app",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            f"--queues={INCIDENT_EVENTS_QUEUE}",
            "--loglevel=INFO",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
        ],
        env=env,
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_until(
            lambda: bool(celery_app.control.ping(timeout=0.5)),
            description="celery worker boot (inspect ping)",
            timeout=60,
        )
    except BaseException:
        process.kill()
        process.wait()
        log_file.flush()
        log_file.seek(0)
        print(log_file.read())
        log_file.close()
        raise
    yield process
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    print(f"[integration] worker log: {log_file.name}")
    log_file.close()


# ────────────────────────── worker-backed tests ──────────────────────────


def test_healthy_event_succeeds_end_to_end(worker, repo, sync_engine, clean_queues):
    payload = _make_payload("INC" + uuid4().hex[:8])
    execution_id = _seed_event_with_execution(sync_engine, payload)

    send_incident_event(payload, execution_id)

    _wait_until(
        lambda: repo.get_status(execution_id) == "succeeded",
        description="healthy event success",
    )
    assert repo.get_termination_cause(execution_id) == "completed"
    snapshot = repo.get_retry_state(execution_id)
    assert snapshot["state"] == "succeeded"
    assert repo.count_failures(execution_id) == 0


def test_transient_event_retries_with_exponential_backoff_then_dead_letters(
    worker, repo, redis_client, sync_engine, clean_queues
):
    payload = _make_payload("INCFAIL" + uuid4().hex[:8])
    execution_id = _seed_event_with_execution(sync_engine, payload)

    send_incident_event(payload, execution_id)

    _wait_until(
        lambda: (repo.get_retry_state(execution_id) or {}).get("state") == "exhausted",
        description="retry budget exhaustion",
    )

    # Empirical backoff measured from the DATABASE (failures.occurred_at),
    # not from mocked clocks: interval_n = stub sleep + delay_n + overhead.
    with Session(sync_engine) as session:
        occurred = session.scalars(
            select(Failure.occurred_at)
            .where(Failure.execution_id == execution_id)
            .order_by(Failure.occurred_at)
        ).all()
    assert len(occurred) == WORKER_MAX_RETRIES
    intervals = [(b - a).total_seconds() for a, b in zip(occurred, occurred[1:], strict=False)]
    for measured, expected in zip(intervals, EXPECTED_INTERVALS, strict=True):
        assert expected - 0.05 <= measured <= expected + 0.5, (
            f"interval {measured:.3f}s, expected ~{expected}s — backoff is not "
            "the configured exponential schedule"
        )
    assert intervals[1] > intervals[0], "second backoff must exceed the first"

    # Dead-letter end-to-end: record fields match DLQEventResponse.
    records = _dlq_records_for(redis_client, payload["event_id"])
    assert len(records) == 1
    record = records[0]
    assert record["payload"] == payload
    assert record["retry_count"] == WORKER_MAX_RETRIES
    assert "forced transient failure" in record["failure_reason"]
    dt.datetime.fromisoformat(record["failed_at"])  # parseable timestamp

    assert repo.get_status(execution_id) == "failed"
    assert repo.get_termination_cause(execution_id)  # non-empty (terminal CHECK)
    snapshot = repo.get_retry_state(execution_id)
    assert snapshot["attempt_count"] == WORKER_MAX_RETRIES
    assert snapshot["next_retry_at"] is None

    with Session(sync_engine) as session:
        retryable = session.scalars(
            select(Failure.retryable).where(Failure.execution_id == execution_id)
        ).all()
    assert all(retryable)


def test_duplicate_event_persists_exactly_one_execution(worker, repo, sync_engine, clean_queues):
    payload = _make_payload("INC" + uuid4().hex[:8])

    first, second = asyncio.run(_accept_twice(payload))

    assert first.status is EventAcceptanceStatus.ACCEPTED
    assert first.execution_id is not None
    assert second.status is EventAcceptanceStatus.DUPLICATE
    assert second.execution_id is None
    with Session(sync_engine) as session:
        execution_ids = session.scalars(
            select(Execution.execution_id)
            .join(Event, Execution.event_record_id == Event.id)
            .where(Event.event_id == payload["event_id"])
        ).all()
    assert len(execution_ids) == 1, "duplicate delivery must not create a second execution"

    # One enqueue for the new event → it completes.
    send_incident_event(payload, first.execution_id)
    _wait_until(
        lambda: repo.get_status(first.execution_id) == "succeeded",
        description="accepted event success",
    )

    # At-least-once redelivery of the same message: zombie guard → no-op.
    with Session(sync_engine) as session:
        ended_before = session.scalar(
            select(Execution.ended_at).where(Execution.execution_id == first.execution_id)
        )
    send_incident_event(payload, first.execution_id)
    time.sleep(1.5)
    assert repo.get_status(first.execution_id) == "succeeded"
    assert repo.count_failures(first.execution_id) == 0
    with Session(sync_engine) as session:
        ended_after = session.scalar(
            select(Execution.ended_at).where(Execution.execution_id == first.execution_id)
        )
    assert ended_after == ended_before, "redelivered message must not touch a done event"


async def _accept_twice(
    payload: dict,
) -> tuple[EventAcceptanceResult, EventAcceptanceResult]:
    """The real webhook persistence boundary (async, IntegrityError→DUPLICATE)."""
    engine = create_db_engine(get_settings())
    factory = create_session_factory(engine)
    inbound = InboundEvent(
        event_id=payload["event_id"],
        sys_id=payload["sys_id"],
        number=payload["number"],
        event_type=payload["event_type"],
    )
    try:
        first = await accept_inbound_event(factory, inbound)
        second = await accept_inbound_event(factory, inbound)
        return first, second
    finally:
        await engine.dispose()


def test_poison_event_is_isolated_from_healthy_events(worker, repo, sync_engine, clean_queues):
    poison_payload = _make_payload("INCBROKEN" + uuid4().hex[:8])
    healthy_payloads = [_make_payload("INC" + uuid4().hex[:8]) for _ in range(8)]
    poison_execution = _seed_event_with_execution(sync_engine, poison_payload)
    healthy_executions = [
        _seed_event_with_execution(sync_engine, payload) for payload in healthy_payloads
    ]

    send_incident_event(poison_payload, poison_execution)
    for payload, execution in zip(healthy_payloads, healthy_executions, strict=True):
        send_incident_event(payload, execution)

    _wait_until(
        lambda: all(repo.get_status(e) == "succeeded" for e in healthy_executions),
        description="all healthy events succeed behind one poison event",
    )
    assert repo.get_status(poison_execution) == "failed"
    assert "terminal failure" in (repo.get_termination_cause(poison_execution) or "")
    snapshot = repo.get_retry_state(poison_execution)
    assert snapshot["state"] == "cancelled"
    assert snapshot["attempt_count"] == 1, "honest first-attempt count, never forged to max"


def test_replay_of_exhausted_event_resets_budget_and_preserves_history(
    worker, repo, redis_client, sync_engine, clean_queues
):
    payload = _make_payload("INCFAIL" + uuid4().hex[:8])
    execution_id = _seed_event_with_execution(sync_engine, payload)
    send_incident_event(payload, execution_id)
    _wait_until(
        lambda: (repo.get_retry_state(execution_id) or {}).get("state") == "exhausted",
        description="first exhaustion",
    )
    assert repo.count_failures(execution_id) == WORKER_MAX_RETRIES
    assert len(_dlq_records_for(redis_client, payload["event_id"])) == 1

    outcome = replay_event(repo, redis_client, payload["event_id"], max_attempts=WORKER_MAX_RETRIES)

    assert outcome.replayed is True
    assert outcome.execution_id == execution_id
    assert outcome.removed_records == 1
    # The reset is proven by the re-run: the event burns a FRESH budget and the
    # failure history stays (Postgres is the durable truth, the DLQ record goes).
    _wait_until(
        lambda: (repo.get_retry_state(execution_id) or {}).get("state") == "exhausted",
        description="second exhaustion after replay",
    )
    assert repo.count_failures(execution_id) == 2 * WORKER_MAX_RETRIES
    assert len(_dlq_records_for(redis_client, payload["event_id"])) == 1


def test_replay_of_cancelled_event_resets_and_reruns(
    worker, repo, redis_client, sync_engine, clean_queues
):
    payload = _make_payload("INCBROKEN" + uuid4().hex[:8])
    execution_id = _seed_event_with_execution(sync_engine, payload)
    send_incident_event(payload, execution_id)
    _wait_until(
        lambda: (repo.get_retry_state(execution_id) or {}).get("state") == "cancelled",
        description="terminal cancellation",
    )
    assert repo.count_failures(execution_id) == 1

    outcome = replay_event(repo, redis_client, payload["event_id"], max_attempts=WORKER_MAX_RETRIES)
    assert outcome.replayed is True

    _wait_until(
        lambda: repo.count_failures(execution_id) == 2,
        description="replayed cancelled event to run again",
    )
    snapshot = repo.get_retry_state(execution_id)
    assert snapshot["state"] == "cancelled"
    assert snapshot["attempt_count"] == 1, "honest first-attempt count again"


def test_redelivery_after_hard_kill_reruns_via_running_self_transition(
    worker, repo, sync_engine, clean_queues
):
    payload = _make_payload("INC" + uuid4().hex[:8])
    execution_id = _seed_event_with_execution(sync_engine, payload)

    # Simulate a worker that claimed the event and was SIGKILLed mid-run: the
    # DB still says 'running', the message comes back (acks_late + reject).
    assert repo.claim_for_running(execution_id) is True
    assert repo.claim_for_running(execution_id) is True, (
        "running→running self-transition: the redelivery must be claimable"
    )
    send_incident_event(payload, execution_id)
    _wait_until(
        lambda: repo.get_status(execution_id) == "succeeded",
        description="redelivered event completes",
    )


# ────────────────────────── direct-repository tests ──────────────────────────


def test_claim_rejects_terminal_and_unknown_executions(repo, sync_engine):
    failed_execution = _seed_event_with_execution(
        sync_engine, _make_payload("INC" + uuid4().hex[:8]), execution_status="failed"
    )
    succeeded_execution = _seed_event_with_execution(
        sync_engine, _make_payload("INC" + uuid4().hex[:8]), execution_status="succeeded"
    )

    assert repo.claim_for_running(failed_execution) is False
    assert repo.claim_for_running(succeeded_execution) is False
    assert repo.claim_for_running(uuid4()) is False


def test_replay_refuses_in_flight_event_against_real_postgres(repo, sync_engine):
    execution_id = _seed_event_with_execution(sync_engine, _make_payload("INC" + uuid4().hex[:8]))
    repo.ensure_retry_state(execution_id, max_attempts=WORKER_MAX_RETRIES)
    repo.claim_for_running(execution_id)  # a worker holds this right now

    assert repo.reset_for_replay(execution_id, max_attempts=WORKER_MAX_RETRIES) is False


def test_ensure_retry_state_aligns_drifted_budget_on_untouched_rows(repo, sync_engine):
    """Live version of the 2026-09-17 incident: a compose worker (budget 5)
    and a test worker (budget 3) shared one queue and produced two CHECK
    violations on the same event. The guard aligns untouched ('ready', 0)
    rows to the caller's budget; rows with history are never rewritten."""
    drifted_execution = _seed_event_with_execution(
        sync_engine, _make_payload("INC" + uuid4().hex[:8])
    )
    repo.ensure_retry_state(drifted_execution, max_attempts=5)  # e.g. the replay CLI's budget
    repo.ensure_retry_state(drifted_execution, max_attempts=3)  # the consuming worker's budget
    assert repo.get_retry_state(drifted_execution)["max_attempts"] == 3

    history_execution = _seed_event_with_execution(
        sync_engine, _make_payload("INC" + uuid4().hex[:8])
    )
    repo.ensure_retry_state(history_execution, max_attempts=5)
    repo.claim_for_running(history_execution)
    failure_id = repo.log_failure(
        execution_id=history_execution,
        attempt=1,
        failure_type="llm_timeout",
        message="attempted under the old budget",
        retryable=True,
    )
    repo.schedule_retry(
        execution_id=history_execution,
        attempt=1,
        backoff_seconds=1.0,
        next_retry_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=1),
        last_failure_id=failure_id,
    )
    repo.ensure_retry_state(history_execution, max_attempts=3)
    snapshot = repo.get_retry_state(history_execution)
    assert snapshot["max_attempts"] == 5, "burned budget history is never rewritten"
    assert snapshot["state"] == "scheduled"
    assert snapshot["attempt_count"] == 1
