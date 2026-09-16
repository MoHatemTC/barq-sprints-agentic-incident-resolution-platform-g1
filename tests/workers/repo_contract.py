"""Contract tests for the worker repository.

The same contract suite runs against every backend (in-memory for unit tests,
real Postgres in the integration suite): whatever backend the worker uses must
satisfy the exact state-machine behaviour Ahmed's schema enforces with CHECK
constraints. The in-memory backend mirrors those constraints so contract
violations surface in fast unit tests, not only against the live database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.workers.db import WorkerRepo

EXECUTION_ID = uuid4()


def assert_repo_contract(
    make_repo: Callable[[], WorkerRepo],
    *,
    seed_execution_id: UUID | None = None,
) -> WorkerRepo:
    """The full repository contract against one backend instance.

    ``make_repo`` returns a repo whose ``EXECUTION_ID`` (or
    ``seed_execution_id``) exists in 'queued' state.
    """
    exec_id = seed_execution_id or EXECUTION_ID

    # ── claim ──────────────────────────────────────────────────────────────
    repo = make_repo()
    assert repo.claim_for_running(exec_id) is True
    assert repo.get_status(exec_id) == "running"

    for terminal in ("succeeded", "failed", "abandoned", "blocked"):
        terminal_repo = make_repo()
        terminal_repo.seed_execution(exec_id, status=terminal)
        assert terminal_repo.claim_for_running(exec_id) is False, terminal
        assert terminal_repo.get_status(exec_id) == terminal

    assert repo.claim_for_running(uuid4()) is False  # unknown execution

    # ── retry_state get-or-create ──────────────────────────────────────────
    repo.ensure_retry_state(exec_id, max_attempts=5)
    repo.ensure_retry_state(exec_id, max_attempts=5)  # idempotent
    assert repo.get_attempt_count(exec_id) == 0

    # ── config drift alignment (replay CLI vs worker budget) ───────────────
    # A row still untouched — ('ready', no attempts) — aligns to the caller's
    # budget: e.g. a replay CLI reset it under a different worker_max_retries.
    # A row that already burned attempts is NEVER rewritten: history stays
    # exactly what the CHECK constraints recorded it as.
    drift_repo = make_repo()
    drift_repo.seed_execution(exec_id, status="queued")
    drift_repo.ensure_retry_state(exec_id, max_attempts=5)
    drift_repo.ensure_retry_state(exec_id, max_attempts=3)
    assert drift_repo.get_retry_state(exec_id)["max_attempts"] == 3

    drifted_history_repo = make_repo()
    drifted_history_repo.seed_execution(exec_id, status="queued")
    drifted_history_repo.ensure_retry_state(exec_id, max_attempts=5)
    drifted_history_repo.claim_for_running(exec_id)
    history_failure = drifted_history_repo.log_failure(
        execution_id=exec_id,
        attempt=1,
        failure_type="llm_timeout",
        message="first attempt under the old budget",
        retryable=True,
    )
    drifted_history_repo.schedule_retry(
        execution_id=exec_id,
        attempt=1,
        backoff_seconds=1.0,
        next_retry_at=datetime.now(UTC) + timedelta(seconds=1),
        last_failure_id=history_failure,
    )
    drifted_history_repo.ensure_retry_state(exec_id, max_attempts=3)
    snapshot = drifted_history_repo.get_retry_state(exec_id)
    assert snapshot["max_attempts"] == 5, "burned budget history is never rewritten"
    assert snapshot["state"] == "scheduled"
    assert snapshot["attempt_count"] == 1

    # ── failure logging ────────────────────────────────────────────────────
    failure_id = repo.log_failure(
        execution_id=exec_id,
        attempt=1,
        failure_type="llm_timeout",
        message="LLM did not answer within 30s",
        retryable=True,
    )
    assert isinstance(failure_id, UUID)
    assert repo.count_failures(exec_id) == 1

    # ── scheduled retry ────────────────────────────────────────────────────
    next_retry = datetime.now(UTC) + timedelta(seconds=1)
    repo.schedule_retry(
        execution_id=exec_id,
        attempt=1,
        backoff_seconds=1.0,
        next_retry_at=next_retry,
        last_failure_id=failure_id,
    )
    assert repo.get_status(exec_id) == "queued", (
        "during backoff nobody works on the event: 'running' must mean "
        "'a worker holds this right now'"
    )
    snapshot = repo.get_retry_state(exec_id)
    assert snapshot["state"] == "scheduled"
    assert snapshot["attempt_count"] == 1
    assert snapshot["next_retry_at"] is not None
    assert snapshot["last_failure_id"] == failure_id

    # ── exhaustion (constraint-safe) ───────────────────────────────────────
    assert repo.claim_for_running(exec_id) is True  # retry pickup
    final_failure = repo.log_failure(
        execution_id=exec_id,
        attempt=5,
        failure_type="llm_timeout",
        message="final timeout",
        retryable=True,
    )
    repo.mark_exhausted(exec_id, max_attempts=5, last_failure_id=final_failure)
    snapshot = repo.get_retry_state(exec_id)
    assert snapshot["state"] == "exhausted"
    assert snapshot["attempt_count"] == 5  # exhausted ⟺ count == max
    assert snapshot["next_retry_at"] is None
    assert repo.get_status(exec_id) == "failed"

    # ── cancellation (terminal errors, NOT exhausted) ──────────────────────
    cancelled_repo = make_repo()
    cancelled_repo.seed_execution(exec_id, status="queued")
    cancelled_repo.claim_for_running(exec_id)
    cancelled_repo.ensure_retry_state(exec_id, max_attempts=5)
    poison_failure = cancelled_repo.log_failure(
        execution_id=exec_id,
        attempt=1,
        failure_type="malformed_payload",
        message="event_type outside contract",
        retryable=False,
    )
    cancelled_repo.mark_cancelled(
        exec_id,
        attempt=1,
        last_failure_id=poison_failure,
        termination_cause="terminal failure: malformed payload",
    )
    snapshot = cancelled_repo.get_retry_state(exec_id)
    assert snapshot["state"] == "cancelled"
    assert snapshot["attempt_count"] == 1  # honest history, not forged to max
    assert snapshot["next_retry_at"] is None
    assert cancelled_repo.get_status(exec_id) == "failed"

    # ── success ────────────────────────────────────────────────────────────
    success_repo = make_repo()
    success_repo.seed_execution(exec_id, status="queued")
    success_repo.claim_for_running(exec_id)
    success_repo.ensure_retry_state(exec_id, max_attempts=5)

    success_repo.mark_succeeded(exec_id)

    assert success_repo.get_status(exec_id) == "succeeded"
    assert success_repo.get_termination_cause(exec_id) == "completed"
    snapshot = success_repo.get_retry_state(exec_id)
    assert snapshot["state"] == "succeeded"
    assert snapshot["next_retry_at"] is None

    # ── replay ─────────────────────────────────────────────────────────────
    for parked_state in ("exhausted", "cancelled"):
        replay_repo = make_repo()
        replay_repo.seed_execution(exec_id, status="queued")
        replay_repo.claim_for_running(exec_id)
        replay_repo.ensure_retry_state(exec_id, max_attempts=5)
        replay_repo.force_state_for_test(exec_id, parked_state)

        assert replay_repo.reset_for_replay(exec_id, max_attempts=5) is True
        assert replay_repo.get_status(exec_id) == "queued"
        assert replay_repo.get_termination_cause(exec_id) is None
        snapshot = replay_repo.get_retry_state(exec_id)
        assert snapshot["state"] == "ready"
        assert snapshot["attempt_count"] == 0
        assert snapshot["next_retry_at"] is None

    in_flight_repo = make_repo()
    in_flight_repo.seed_execution(exec_id, status="queued")
    in_flight_repo.claim_for_running(exec_id)
    in_flight_repo.ensure_retry_state(exec_id, max_attempts=5)
    assert in_flight_repo.reset_for_replay(exec_id, max_attempts=5) is False, (
        "only parked events replay: never reset a queued/running execution from under a live worker"
    )

    # ── event lookup (the replay CLI's event_id → execution path) ──────────
    EVENT_PAYLOAD = {
        "event_id": "evt-lookup-0001",
        "sys_id": "SYS0000001",
        "number": "INC0000001",
        "event_type": "incident.created",
        "contract_version": "v1",
    }
    lookup_repo = make_repo()
    lookup_repo.seed_execution(
        exec_id, status="queued", event_id=EVENT_PAYLOAD["event_id"], payload=EVENT_PAYLOAD
    )
    assert lookup_repo.find_execution_id(EVENT_PAYLOAD["event_id"]) == exec_id
    assert lookup_repo.find_execution_id("evt-unknown") is None
    assert lookup_repo.get_event_payload(EVENT_PAYLOAD["event_id"]) == EVENT_PAYLOAD
    assert lookup_repo.get_event_payload("evt-unknown") is None

    return repo
