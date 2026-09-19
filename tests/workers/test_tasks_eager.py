"""Task state-machine tests (eager mode, in-memory repo — no live services).

These tests drive the real task logic (_run_incident) with a fake Celery task
context, so every state transition, the DLQ record contract and the retry
delay consistency are verified deterministically. The integration suite then
re-runs the critical paths against real Redis + Postgres.

The FakeTask mimics the only Celery surface the task logic uses:
  * ``request.retries`` — the current attempt counter (0-based)
  * ``retry(exc=..., countdown=...)`` — Celery's explicit retry, which raises
    ``celery.exceptions.Retry`` carrying the scheduled countdown.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest import mock
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded

from api.schemas.dlq import DLQEventResponse
from app.workers import tasks as tasks_module
from app.workers.db import InMemoryRepo
from app.workers.db import InMemoryRepo as _BaseInMemoryRepo
from app.workers.retry_policy import RetryableError, RetryConfig, TerminalError
from app.workers.tasks import _run_incident, record_dead_letter

EXECUTION_ID = uuid4()
PAYLOAD_OK = {
    "event_id": "evt-ok-0001",
    "sys_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "number": "INC0010001",
    "event_type": "incident.created",
}
PAYLOAD_TRANSIENT = {**PAYLOAD_OK, "event_id": "evt-fail-0001", "number": "INCFAIL0001"}
PAYLOAD_TERMINAL = {**PAYLOAD_OK, "event_id": "evt-broken-001", "number": "INCBROKEN001"}

CFG = RetryConfig(max_retries=3, backoff_base=1.0, backoff_max=60.0, jitter=False)


class FakeRequest:
    def __init__(self, retries: int = 0) -> None:
        self.retries = retries


class FakeTask:
    """Mimics the Celery task surface used by the task logic."""

    def __init__(self, retries: int = 0) -> None:
        self.request = FakeRequest(retries)
        self.last_countdown: float | None = None

    def retry(self, exc: Exception | None = None, countdown: float | None = None):
        self.last_countdown = countdown
        raise Retry(exc=exc, when=None)


def make_repo(status: str = "queued") -> InMemoryRepo:
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status=status)
    return repo


class TestSuccessPath:
    def test_success_marks_terminal_completeness(self) -> None:
        repo = make_repo()

        result = _run_incident(FakeTask(), PAYLOAD_OK, str(EXECUTION_ID), CFG, repo)

        assert result["status"] == "succeeded"
        assert repo.get_status(EXECUTION_ID) == "succeeded"
        assert repo.get_termination_cause(EXECUTION_ID) == "completed"
        assert repo.get_retry_state(EXECUTION_ID)["state"] == "succeeded"
        assert repo.count_failures(EXECUTION_ID) == 0

    def test_zombie_redelivery_is_noop(self) -> None:
        """At-least-once delivery: a message redelivered after the event
        already succeeded must not re-run the graph."""
        repo = make_repo(status="succeeded")

        result = _run_incident(FakeTask(), PAYLOAD_OK, str(EXECUTION_ID), CFG, repo)

        assert result["status"] == "already_done"
        assert repo.count_failures(EXECUTION_ID) == 0

    def test_retry_state_created_on_first_pickup(self) -> None:
        """accept_inbound_event does not create retry_state; the worker must."""
        repo = make_repo()
        assert repo.get_retry_state(EXECUTION_ID) is None

        _run_incident(FakeTask(), PAYLOAD_OK, str(EXECUTION_ID), CFG, repo)

        assert repo.get_retry_state(EXECUTION_ID) is not None


class TestRetryablePath:
    def test_intermediate_attempt_logs_and_schedules(self) -> None:
        repo = make_repo()

        with pytest.raises(Retry):
            _run_incident(FakeTask(retries=0), PAYLOAD_TRANSIENT, str(EXECUTION_ID), CFG, repo)

        assert repo.count_failures(EXECUTION_ID) == 1
        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["state"] == "scheduled"
        assert snapshot["attempt_count"] == 1
        # During backoff nobody works on the event.
        assert repo.get_status(EXECUTION_ID) == "queued"

    def test_delay_recorded_matches_countdown_scheduled(self) -> None:
        """The DB next_retry_at and Celery's countdown must be the same delay —
        one source of truth (explicit self.retry, no autoretry_for)."""
        repo = make_repo()
        task = FakeTask(retries=0)

        with pytest.raises(Retry):
            _run_incident(task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), CFG, repo)

        expected = CFG.backoff_base * (2**0)  # attempt 1, jitter off
        assert task.last_countdown == expected
        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["next_retry_at"] is not None
        stored = snapshot["next_retry_at"]
        if stored.tzinfo is not None:
            stored = stored.replace(tzinfo=None)
        delta = stored - datetime.now(UTC).replace(tzinfo=None)
        assert 0 <= delta.total_seconds() <= 2.0  # ~1s delay, clock tolerance

    def test_intermediate_attempts_each_logged(self) -> None:
        """Pitfall #13 pinned: every intermediate attempt gets a failures row.
        max_retries=3 -> exactly 3 rows across attempts 1..3."""
        repo = make_repo()
        for retries in range(3):  # attempts 1, 2, 3
            task = FakeTask(retries=retries)
            with pytest.raises((Retry, RetryableError)):
                _run_incident(task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), CFG, repo)

        assert repo.count_failures(EXECUTION_ID) == 3

    def test_final_attempt_sets_exhausted_never_scheduled(self) -> None:
        """ck_retry_state_active_retry_remaining: attempt_count == max must
        never be 'scheduled'. The final attempt dead-letters instead."""
        repo = make_repo()
        task = FakeTask(retries=2)  # attempt 3 == max_retries

        with pytest.raises(RetryableError):
            _run_incident(task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), CFG, repo)

        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["state"] == "exhausted"
        assert snapshot["attempt_count"] == 3
        assert snapshot["next_retry_at"] is None
        assert repo.get_status(EXECUTION_ID) == "failed"

    def test_soft_time_limit_treated_as_retryable_and_logged(self) -> None:
        """A hang is transient AND must be logged like any attempt."""
        repo = make_repo()
        task = FakeTask(retries=0)

        with pytest.raises(Retry):
            with mock.patch.object(
                tasks_module,
                "invoke_graph",
                side_effect=SoftTimeLimitExceeded(),
            ):
                _run_incident(task, PAYLOAD_OK, str(EXECUTION_ID), CFG, repo)

        assert repo.count_failures(EXECUTION_ID) == 1
        assert repo.get_status(EXECUTION_ID) == "queued"


class TestTerminalPath:
    def test_terminal_error_marks_cancelled_not_exhausted(self) -> None:
        """ck_retry_state_exhausted_attempt_limit forbids exhausted below max.
        A poison event dying on attempt 1 is 'cancelled' with honest history."""
        repo = make_repo()

        with pytest.raises(TerminalError):
            _run_incident(FakeTask(retries=0), PAYLOAD_TERMINAL, str(EXECUTION_ID), CFG, repo)

        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["state"] == "cancelled"
        assert snapshot["attempt_count"] == 1
        assert snapshot["next_retry_at"] is None
        assert repo.get_status(EXECUTION_ID) == "failed"
        assert repo.count_failures(EXECUTION_ID) == 1

    def test_unknown_exception_fails_closed_to_terminal(self) -> None:
        repo = make_repo()

        with pytest.raises(RuntimeError):
            with mock.patch.object(
                tasks_module,
                "invoke_graph",
                side_effect=RuntimeError("deterministic bug"),
            ):
                _run_incident(FakeTask(retries=0), PAYLOAD_OK, str(EXECUTION_ID), CFG, repo)

        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["state"] == "cancelled"
        assert repo.get_status(EXECUTION_ID) == "failed"


class TestDeadLetterRecord:
    def test_record_matches_dlq_api_contract(self) -> None:
        """The Redis DLQ record is consumed by S2.1's DLQ endpoints: field
        names must equal DLQEventResponse exactly."""
        sink = MagicMock()
        repo = make_repo()

        record_dead_letter(
            repo,
            sink,
            PAYLOAD_TRANSIENT,
            str(EXECUTION_ID),
            RetryableError("LLM timeout"),
            attempt=3,
        )

        sink.lpush.assert_called_once()
        queue, raw = sink.lpush.call_args[0]
        record = json.loads(raw)
        assert set(record.keys()) == set(DLQEventResponse.model_fields.keys())
        assert record["event_id"] == PAYLOAD_TRANSIENT["event_id"]
        assert record["payload"] == PAYLOAD_TRANSIENT
        assert record["failure_reason"] == "LLM timeout"
        assert record["retry_count"] == 3
        assert record["failed_at"] is not None


class TestMaxRetriesOneEdgeCase:
    """Gap 1: max_retries=1 means ONE attempt, ZERO retries.

    An operator might set WORKER_MAX_RETRIES=1 thinking "one retry." They
    actually get: first attempt fails → attempt(1) < max_retries(1) is False →
    immediate exhaustion, no retry at all. This test pins that surprising
    behaviour so nobody changes it accidentally."""

    def test_single_attempt_budget_exhausts_immediately(self) -> None:
        """With max_retries=1, the very first retryable error exhausts the
        budget — no retry is ever scheduled."""
        cfg = RetryConfig(max_retries=1, backoff_base=1.0, backoff_max=60.0, jitter=False)
        repo = make_repo()
        task = FakeTask(retries=0)  # attempt 1 (retries + 1)

        with pytest.raises(RetryableError):
            _run_incident(task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), cfg, repo)

        # Budget consumed on the first and only attempt.
        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["state"] == "exhausted"
        assert snapshot["attempt_count"] == 1  # honest count
        assert snapshot["next_retry_at"] is None
        assert repo.get_status(EXECUTION_ID) == "failed"
        assert repo.count_failures(EXECUTION_ID) == 1
        # FakeTask.retry was NEVER called (no Retry raised).
        assert task.last_countdown is None


class TestDLQRedisFailureGuard:
    """Gap 7: record_dead_letter must survive a Redis blip.

    If Redis is down when on_failure fires, the DLQ push fails — but the DB
    reconciliation must still run and the worker must NOT crash."""

    def test_redis_down_does_not_crash_and_db_reconciliation_runs(self) -> None:
        repo = make_repo()
        broken_redis = MagicMock()
        broken_redis.lpush.side_effect = ConnectionError("Redis is down")

        # Should NOT raise — the try/except catches the Redis failure.
        record_dead_letter(
            repo,
            broken_redis,
            PAYLOAD_TRANSIENT,
            str(EXECUTION_ID),
            RetryableError("LLM timeout"),
            attempt=3,
        )

        # Redis was attempted.
        broken_redis.lpush.assert_called_once()
        # DB reconciliation still ran: the execution is now terminal.
        assert repo.get_status(EXECUTION_ID) == "failed"
        assert repo.get_termination_cause(EXECUTION_ID) is not None

    def test_healthy_redis_still_writes_dlq_record(self) -> None:
        """Sanity check: when Redis is healthy, the record lands."""
        repo = make_repo()
        healthy_redis = MagicMock()

        record_dead_letter(
            repo,
            healthy_redis,
            PAYLOAD_TRANSIENT,
            str(EXECUTION_ID),
            RetryableError("LLM timeout"),
            attempt=3,
        )

        healthy_redis.lpush.assert_called_once()


class TestFaultInjectionScheduleRetry:
    """Gap 3: if schedule_retry raises (Postgres dies between log_failure and
    schedule_retry), the exception escapes → on_failure fires → DLQ record must
    still land. The worker must not crash in a way that loses the event.

    The other agent's correction is important here: acks_late does NOT redeliver
    on task exceptions — Celery acks after the task function finishes (including
    raises). Recovery is DLQ + replay, not auto-redelivery."""

    def test_schedule_retry_failure_still_raises_to_on_failure(self) -> None:
        """When schedule_retry raises, the exception propagates out of
        _run_incident. Celery's on_failure hook then fires, which writes the
        DLQ record. This test pins that the original exception escapes."""

        class BrokenScheduleRepo(_BaseInMemoryRepo):
            def schedule_retry(self, **kwargs):
                raise ConnectionError("Postgres connection lost")

        repo = BrokenScheduleRepo()
        repo.seed_execution(EXECUTION_ID, status="queued")
        task = FakeTask(retries=0)

        # The Postgres failure escapes as ConnectionError (which classify()
        # treats as retryable, but _run_incident already passed the retry
        # branch — the re-raise is the raw ConnectionError from schedule_retry).
        with pytest.raises(ConnectionError, match="Postgres connection lost"):
            _run_incident(task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), CFG, repo)

        # The failure row WAS logged (separate transaction, before schedule_retry).
        assert repo.count_failures(EXECUTION_ID) == 1


class TestBackoffSecondsTruncation:
    """Gap 6: retry_state.backoff_seconds is Integer in the schema (Ahmed's
    column). int(0.2) = 0. The actual Celery countdown uses the float, so
    behaviour is correct — but the DB record is wrong for sub-second delays.

    This test documents the truncation as deliberate rather than a bug.
    TODO(Ahmed): consider Numeric for backoff_seconds in retry_state."""

    def test_subsecond_backoff_truncates_to_zero_in_db(self) -> None:
        cfg = RetryConfig(max_retries=3, backoff_base=0.2, backoff_max=60.0, jitter=False)
        repo = make_repo()
        task = FakeTask(retries=0)  # attempt 1

        with pytest.raises(Retry):
            _run_incident(task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), cfg, repo)

        # Celery countdown uses the FLOAT — actual delay is correct.
        assert task.last_countdown == 0.2
        # But the DB stores int(0.2) = 0.
        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["backoff_seconds"] == 0  # <-- the truncation
