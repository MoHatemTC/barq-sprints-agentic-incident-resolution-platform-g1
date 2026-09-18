"""Failure-injection and timeout tests for Celery workers and Dead-Letter Queue (DLQ).

Covers the exact requirements requested by mentor review:
1. Hanging-task verification: Celery terminates and handles executions running past timeout.
2. Failure-injection verification: Terminal or malformed payloads route straight to DLQ
   on the initial attempt without burning retries.
"""

from __future__ import annotations

from unittest import mock
from uuid import uuid4

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded

from app.workers import tasks as tasks_module
from app.workers.db import InMemoryRepo
from app.workers.retry_policy import RetryConfig, TerminalError
from app.workers.tasks import _run_incident

EXECUTION_ID = uuid4()
CFG = RetryConfig(max_retries=5, backoff_base=1.0, backoff_max=60.0, jitter=False)

PAYLOAD_VALID = {
    "event_id": "evt-valid-001",
    "sys_id": "0123456789abcdef0123456789abcdef",
    "number": "INC0010099",
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

    def retry(self, exc: Exception | None = None, countdown: float | None = None):
        self.retry_called = True
        raise Retry(exc=exc, when=None)


def test_hanging_task_terminated_by_timeout() -> None:
    """Verify that a task hanging/exceeding timeout raises SoftTimeLimitExceeded
    and is caught, recorded, and handled rather than hanging indefinitely."""
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status="queued")
    task = MockCeleryTask(retries=0)

    def simulate_hanging_graph(payload):
        # Simulates graph exceeding task-level soft_time_limit
        raise SoftTimeLimitExceeded()

    with mock.patch.object(tasks_module, "invoke_graph", side_effect=simulate_hanging_graph):
        with pytest.raises(Retry):
            _run_incident(task, PAYLOAD_VALID, str(EXECUTION_ID), CFG, repo)

    # Handled cleanly as retryable attempt 1 without freezing the worker
    assert repo.count_failures(EXECUTION_ID) == 1
    assert repo.get_status(EXECUTION_ID) == "queued"
    snapshot = repo.get_retry_state(EXECUTION_ID)
    assert snapshot["state"] == "scheduled"
    assert snapshot["attempt_count"] == 1


def test_terminal_event_routes_straight_to_dlq_without_burning_retries() -> None:
    """Verify that a poison/terminal event dies on attempt 1 and is cancelled/dead-lettered
    immediately without burning through remaining retries."""
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status="queued")
    task = MockCeleryTask(retries=0)

    # Attempt 1 raises TerminalError (poison pill)
    with pytest.raises(TerminalError):
        _run_incident(task, PAYLOAD_TERMINAL, str(EXECUTION_ID), CFG, repo)

    # Must NOT schedule retry; must be marked cancelled on attempt 1
    snapshot = repo.get_retry_state(EXECUTION_ID)
    assert snapshot["state"] == "cancelled"
    assert snapshot["attempt_count"] == 1
    assert snapshot["next_retry_at"] is None
    assert repo.get_status(EXECUTION_ID) == "failed"
    assert repo.count_failures(EXECUTION_ID) == 1


def test_malformed_payload_routes_straight_to_dlq_without_burning_retries() -> None:
    """Verify that a malformed payload (missing schema fields or corrupt data)
    fails closed immediately to DLQ without burning retries."""
    repo = InMemoryRepo()
    repo.seed_execution(EXECUTION_ID, status="queued")
    task = MockCeleryTask(retries=0)

    def validate_and_invoke(payload):
        if "number" not in payload or "sys_id" not in payload:
            raise KeyError("Malformed payload: missing required incident fields")
        return {"status": "ok"}

    with mock.patch.object(tasks_module, "invoke_graph", side_effect=validate_and_invoke):
        with pytest.raises(KeyError):
            _run_incident(task, PAYLOAD_MALFORMED, str(EXECUTION_ID), CFG, repo)

    # Fails closed on initial attempt (attempt 1), status failed, never scheduled for retry
    snapshot = repo.get_retry_state(EXECUTION_ID)
    assert snapshot["state"] == "cancelled"
    assert snapshot["attempt_count"] == 1
    assert repo.get_status(EXECUTION_ID) == "failed"
    assert repo.count_failures(EXECUTION_ID) == 1
