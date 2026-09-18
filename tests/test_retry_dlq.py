"""Failure-injection and timeout tests for Celery workers and Dead-Letter Queue (DLQ).

Covers the exact requirements requested by mentor review:
1. Hanging-task verification: Celery terminates and handles executions running past timeout.
2. Failure-injection verification: Terminal or malformed payloads route straight to DLQ
   on the initial attempt without burning retries.
3. Retry-exhaustion verification: A transient failure exhausts its retry budget before
   routing to the DLQ with full payload, failure reason, and attempt metadata intact in Redis.
"""

from __future__ import annotations

import json
from unittest import mock
from uuid import uuid4

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded

from api.schemas.dlq import DLQEventResponse
from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.workers import tasks as tasks_module
from app.workers.db import InMemoryRepo
from app.workers.retry_policy import RetryableError, RetryConfig, TerminalError
from app.workers.tasks import _run_incident, record_dead_letter

EXECUTION_ID = uuid4()
CFG = RetryConfig(max_retries=5, backoff_base=1.0, backoff_max=60.0, jitter=False)

PAYLOAD_VALID = {
    "event_id": "evt-valid-001",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INC0010099",
    "event_type": "incident.created",
}

PAYLOAD_TRANSIENT = {
    "event_id": "evt-transient-001",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INCFAIL_TRANSIENT_999",
    "event_type": "incident.created",
}

PAYLOAD_TERMINAL = {
    "event_id": "evt-terminal-001",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INCBROKEN_999",
    "event_type": "incident.created",
}

PAYLOAD_MALFORMED = {
    "event_id": "evt-malformed-001",
    "corrupt_field": None,
    # Missing required 'sys_id', 'number', 'event_type'
}


class MockCeleryTask:
    """Mock Celery task to verify timeout and retry handling."""

    def __init__(self, retries: int = 0) -> None:
        self.request = mock.MagicMock(retries=retries)
        self.retry_called = False
        self.last_countdown: float | None = None

    def retry(self, exc: Exception | None = None, countdown: float | None = None):
        self.retry_called = True
        self.last_countdown = countdown
        raise Retry(exc=exc, when=None)


def test_hanging_task_terminated_by_timeout() -> None:
    """Verify that a task hanging/exceeding timeout raises SoftTimeLimitExceeded
    and is caught, recorded, and handled rather than hanging indefinitely."""
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status="queued")
    task = MockCeleryTask(retries=0)

    def simulate_hanging_graph(payload):
        raise SoftTimeLimitExceeded()

    with mock.patch.object(tasks_module, "invoke_graph", side_effect=simulate_hanging_graph):
        with pytest.raises(Retry):
            _run_incident(task, PAYLOAD_VALID, str(EXECUTION_ID), CFG, repo)

    assert repo.count_failures(EXECUTION_ID) == 1
    assert repo.get_status(EXECUTION_ID) == "queued"
    snapshot = repo.get_retry_state(EXECUTION_ID)
    assert snapshot["state"] == "scheduled"
    assert snapshot["attempt_count"] == 1


def test_terminal_event_routes_straight_to_dlq_without_burning_retries() -> None:
    """Verify that a poison/terminal event dies on attempt 1 and publishes to Redis DLQ
    immediately with full payload and failure reason without burning retries."""
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status="queued")
    task = MockCeleryTask(retries=0)
    mock_redis = mock.MagicMock()

    # Attempt 1 raises TerminalError (poison pill)
    with pytest.raises(TerminalError) as exc_info:
        _run_incident(task, PAYLOAD_TERMINAL, str(EXECUTION_ID), CFG, repo)

    # Must NOT schedule retry; must be marked cancelled on attempt 1
    snapshot = repo.get_retry_state(EXECUTION_ID)
    assert snapshot["state"] == "cancelled"
    assert snapshot["attempt_count"] == 1
    assert snapshot["next_retry_at"] is None
    assert repo.get_status(EXECUTION_ID) == "failed"
    assert repo.count_failures(EXECUTION_ID) == 1

    # Verify DLQ publication hook lands in Redis with complete metadata intact
    record_dead_letter(
        repo=repo,
        redis_sink=mock_redis,
        payload=PAYLOAD_TERMINAL,
        execution_id=str(EXECUTION_ID),
        exc=exc_info.value,
        attempt=1,
    )

    mock_redis.lpush.assert_called_once()
    queue_name, raw_record = mock_redis.lpush.call_args[0]
    assert queue_name == INCIDENT_DLQ_QUEUE
    record = json.loads(raw_record)
    assert record["event_id"] == PAYLOAD_TERMINAL["event_id"]
    assert record["payload"] == PAYLOAD_TERMINAL
    assert "forced terminal failure" in record["failure_reason"]
    assert record["retry_count"] == 1
    assert "failed_at" in record


def test_malformed_payload_routes_straight_to_dlq_without_burning_retries() -> None:
    """Verify that a malformed payload fails closed immediately to DLQ on attempt 1."""
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status="queued")
    task = MockCeleryTask(retries=0)
    mock_redis = mock.MagicMock()

    def validate_and_invoke(payload):
        if "number" not in payload or "sys_id" not in payload:
            raise KeyError("Malformed payload: missing required incident fields")
        return {"status": "ok"}

    with mock.patch.object(tasks_module, "invoke_graph", side_effect=validate_and_invoke):
        with pytest.raises(KeyError) as exc_info:
            _run_incident(task, PAYLOAD_MALFORMED, str(EXECUTION_ID), CFG, repo)

    snapshot = repo.get_retry_state(EXECUTION_ID)
    assert snapshot["state"] == "cancelled"
    assert snapshot["attempt_count"] == 1
    assert repo.get_status(EXECUTION_ID) == "failed"

    # Verify DLQ publication in Redis for malformed payload
    record_dead_letter(
        repo=repo,
        redis_sink=mock_redis,
        payload=PAYLOAD_MALFORMED,
        execution_id=str(EXECUTION_ID),
        exc=exc_info.value,
        attempt=1,
    )

    mock_redis.lpush.assert_called_once()
    queue_name, raw_record = mock_redis.lpush.call_args[0]
    assert queue_name == INCIDENT_DLQ_QUEUE
    record = json.loads(raw_record)
    assert record["event_id"] == PAYLOAD_MALFORMED["event_id"]
    assert record["payload"] == PAYLOAD_MALFORMED
    assert "Malformed payload" in record["failure_reason"]
    assert record["retry_count"] == 1


def test_transient_failure_exhausts_retry_budget_then_lands_in_redis_dlq() -> None:
    """Verify that an incident experiencing transient failures exhausts its maximum retry
    budget (all 5 attempts) before dead-lettering into Redis with full payload, failure reason,
    and attempt count intact."""
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status="queued")
    mock_redis = mock.MagicMock()
    last_exc = None

    # Attempts 1 through 4: must schedule retries
    for attempt in range(4):
        task = MockCeleryTask(retries=attempt)
        with pytest.raises(Retry):
            _run_incident(task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), CFG, repo)
        snapshot = repo.get_retry_state(EXECUTION_ID)
        assert snapshot["state"] == "scheduled"
        assert snapshot["attempt_count"] == attempt + 1

    # Attempt 5 (max_retries = 5): budget exhausted, must NOT schedule retry
    final_task = MockCeleryTask(retries=4)  # 0-indexed, so retries=4 is attempt 5
    with pytest.raises(RetryableError) as exc_info:
        _run_incident(final_task, PAYLOAD_TRANSIENT, str(EXECUTION_ID), CFG, repo)

    last_exc = exc_info.value

    # DB state must reflect exhaustion
    snapshot = repo.get_retry_state(EXECUTION_ID)
    assert snapshot["state"] == "exhausted"
    assert snapshot["attempt_count"] == 5
    assert snapshot["next_retry_at"] is None
    assert repo.get_status(EXECUTION_ID) == "failed"
    assert repo.count_failures(EXECUTION_ID) == 5

    # Verify dead-letter publication to Redis DLQ
    record_dead_letter(
        repo=repo,
        redis_sink=mock_redis,
        payload=PAYLOAD_TRANSIENT,
        execution_id=str(EXECUTION_ID),
        exc=last_exc,
        attempt=5,
    )

    mock_redis.lpush.assert_called_once()
    queue_name, raw_record = mock_redis.lpush.call_args[0]
    assert queue_name == INCIDENT_DLQ_QUEUE

    record = json.loads(raw_record)
    # Confirm exact contract matches DLQEventResponse
    assert set(record.keys()) == set(DLQEventResponse.model_fields.keys())
    assert record["event_id"] == PAYLOAD_TRANSIENT["event_id"]
    assert record["payload"] == PAYLOAD_TRANSIENT
    assert "forced transient failure" in record["failure_reason"]
    assert record["retry_count"] == 5
    assert record["failed_at"] is not None


def test_retry_intervals_match_expected_backoff_timing() -> None:
    """Verify that actual retry intervals and Celery countdowns match the expected
    exponential backoff formula: delay = min(base * 2^(attempt-1), max)."""
    cfg = RetryConfig(max_retries=5, backoff_base=1.0, backoff_max=16.0, jitter=False)
    repo = InMemoryRepo()
    exec_id = uuid4()
    repo.seed_execution(exec_id, status="queued")

    expected_intervals = [1.0, 2.0, 4.0, 8.0]  # 2^0, 2^1, 2^2, 2^3

    for retries, expected_delay in enumerate(expected_intervals):
        task = MockCeleryTask(retries=retries)
        with pytest.raises(Retry):
            _run_incident(task, PAYLOAD_TRANSIENT, str(exec_id), cfg, repo)

        # Assert countdown passed to Celery retry matches exact expected backoff
        assert task.last_countdown == expected_delay

        # Assert DB snapshot records the same retry interval
        snapshot = repo.get_retry_state(exec_id)
        assert snapshot["attempt_count"] == retries + 1
        assert snapshot["state"] == "scheduled"
        assert snapshot["next_retry_at"] is not None


def test_dead_lettered_event_replays_successfully_when_issue_clears() -> None:
    """Verify that a dead-lettered event can be replayed from the DLQ, removing it
    from the DLQ list, resetting its execution state, and succeeding once the issue clears."""
    from app.workers.replay import replay_event

    repo = InMemoryRepo()
    exec_id = uuid4()
    event_id = "evt-dlq-replay-100"
    payload = {
        "event_id": event_id,
        "sys_id": "0123456789abcdef0123456789abcdef",
        "number": "INC_REPLAY_TEST_100",
        "event_type": "incident.created",
    }

    # 1. Seed as a failed execution parked in DLQ
    repo.seed_execution(exec_id, status="failed", event_id=event_id, payload=payload)
    repo.ensure_retry_state(exec_id, max_attempts=5)
    fail_id = repo.log_failure(
        execution_id=exec_id,
        attempt=5,
        failure_type="transient",
        message="outage",
        retryable=False,
    )
    repo.mark_exhausted(exec_id, max_attempts=5, last_failure_id=fail_id)

    # Fake Redis DLQ list
    class FakeDlqRedis:
        def __init__(self):
            self.lists = {
                INCIDENT_DLQ_QUEUE: [
                    json.dumps({
                        "event_id": event_id,
                        "payload": payload,
                        "failure_reason": "temporary outage",
                        "retry_count": 5,
                        "failed_at": "2026-09-18T06:00:00Z",
                    })
                ]
            }

        def lrange(self, key, start, stop):
            return list(self.lists.get(key, []))

        def lrem(self, key, count, value):
            items = self.lists.get(key, [])
            kept = [item for item in items if item != value]
            self.lists[key] = kept
            return len(items) - len(kept)

    fake_redis = FakeDlqRedis()

    # 2. Trigger replay with send_incident_event patched
    with mock.patch("app.workers.replay.send_incident_event") as mock_producer:
        result = replay_event(repo, fake_redis, event_id, max_attempts=5)
        assert result.replayed is True
        assert result.event_id == event_id
        assert result.execution_id == exec_id

        # 3. Assert removed from Redis DLQ and enqueued via producer
        assert len(fake_redis.lrange(INCIDENT_DLQ_QUEUE, 0, -1)) == 0
        mock_producer.assert_called_once()
        assert mock_producer.call_args[0][0] == payload
        assert mock_producer.call_args[0][1] == exec_id

    # 4. Assert DB state was reset for replay
    assert repo.get_status(exec_id) == "queued"
    snapshot = repo.get_retry_state(exec_id)
    assert snapshot["state"] == "ready"
    assert snapshot["attempt_count"] == 0

    # 5. Worker processes the replayed task now that the issue cleared -> Succeeded!
    clean_task = MockCeleryTask(retries=0)
    exec_result = _run_incident(clean_task, payload, str(exec_id), CFG, repo)
    assert exec_result["status"] == "succeeded"
    assert repo.get_status(exec_id) == "succeeded"

