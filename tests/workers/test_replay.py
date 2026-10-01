"""Unit tests for the dead-letter replay CLI (fast; live-service twin in the
integration suite covers the Postgres + real-Redis path end to end).

The replay contract pinned here:

- only ``exhausted``/``cancelled`` events replay — the atomic
  ``reset_for_replay`` guard refuses anything in flight;
- unknown event ids are refused, never guessed;
- the event's records are removed from the Redis DLQ list (LREM) and the
  re-enqueue goes through the ONE producer, never a hand-written envelope;
- the DLQ inspector never crashes on a malformed record.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.exceptions.app_errors import ConflictError
from app.workers.db import InMemoryRepo
from app.workers.replay import load_dead_letters, replay_event

EVENT_PAYLOAD = {
    "event_id": "evt-replay-0001",
    "sys_id": "SYS0000001",
    "number": "INC0000001",
    "event_type": "incident.created",
    "contract_version": "v1",
}


class FakePipeline:
    """Mock pipeline executing queued operations on execute()."""

    def __init__(self, redis: FakeListRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, int, str]] = []

    def lrem(self, key: str, count: int, value: str) -> FakePipeline:
        self._ops.append((key, count, value))
        return self

    def execute(self) -> list[int]:
        return [self._redis.lrem(key, count, value) for key, count, value in self._ops]


class FakeListRedis:
    """Just enough of redis-py for the DLQ list: LPUSH / LRANGE(0,-1) / LREM(0) / PIPELINE."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        assert (start, stop) == (0, -1), "the DLQ inspector reads the whole list"
        return list(self.lists.get(key, []))

    def lrem(self, key: str, count: int, value: str) -> int:
        assert count == 0, "replay removes every record of the event"
        items = self.lists.get(key, [])
        kept = [item for item in items if item != value]
        self.lists[key] = kept
        return len(items) - len(kept)

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


def _parked_repo(*, state: str) -> InMemoryRepo:
    """A repo holding one event whose execution reached the given retry state."""
    repo = InMemoryRepo()
    execution_id = uuid4()
    repo.seed_execution(
        execution_id, status="failed", event_id=EVENT_PAYLOAD["event_id"], payload=EVENT_PAYLOAD
    )
    repo.ensure_retry_state(execution_id, max_attempts=5)
    repo.force_state_for_test(execution_id, state)
    return repo


def _dlq_with_event(redis: FakeListRedis, *, records_for_event: int) -> None:
    for index in range(records_for_event):
        redis.lpush(
            INCIDENT_DLQ_QUEUE,
            json.dumps({**EVENT_PAYLOAD, "failure_reason": "boom", "failed_at": f"t{index}"}),
        )
    redis.lpush(
        INCIDENT_DLQ_QUEUE,
        json.dumps({"event_id": "evt-other", "payload": {}, "failure_reason": "x"}),
    )


def test_replay_resets_parked_event_and_reenqueues_via_producer() -> None:
    repo = _parked_repo(state="exhausted")
    redis = FakeListRedis()
    _dlq_with_event(redis, records_for_event=2)
    producer = MagicMock()

    with patch("app.workers.replay.send_incident_event", producer):
        outcome = replay_event(repo, redis, EVENT_PAYLOAD["event_id"], max_attempts=5)

    assert outcome.replayed is True
    assert outcome.removed_records == 2  # every record of THIS event, others untouched
    remaining = [json.loads(raw)["event_id"] for raw in redis.lrange(INCIDENT_DLQ_QUEUE, 0, -1)]
    assert remaining == ["evt-other"]
    producer.assert_called_once_with(
        EVENT_PAYLOAD, outcome.execution_id, correlation_id=str(outcome.execution_id)
    )
    execution = repo.executions[outcome.execution_id]
    assert execution["status"] == "queued"
    assert execution["ended_at"] is None
    state = repo.retry_states[outcome.execution_id]
    assert state["state"] == "ready"
    assert state["attempt_count"] == 0


def test_replay_also_accepts_cancelled_events() -> None:
    repo = _parked_repo(state="cancelled")
    redis = FakeListRedis()
    _dlq_with_event(redis, records_for_event=1)

    with patch("app.workers.replay.send_incident_event") as producer:
        outcome = replay_event(repo, redis, EVENT_PAYLOAD["event_id"], max_attempts=5)

    assert outcome.replayed is True
    producer.assert_called_once()


def test_replay_refuses_events_still_in_flight() -> None:
    repo = _parked_repo(state="scheduled")
    redis = FakeListRedis()
    _dlq_with_event(redis, records_for_event=1)
    producer = MagicMock()

    with patch("app.workers.replay.send_incident_event", producer):
        outcome = replay_event(repo, redis, EVENT_PAYLOAD["event_id"], max_attempts=5)

    assert outcome.replayed is False
    assert "not parked" in (outcome.reason or "")
    producer.assert_not_called()  # nothing was enqueued behind the caller's back
    assert len(redis.lrange(INCIDENT_DLQ_QUEUE, 0, -1)) == 2  # DLQ untouched


def test_replay_preserves_parked_event_when_incident_reset_fails() -> None:
    repo = _parked_repo(state="exhausted")
    redis = FakeListRedis()
    _dlq_with_event(redis, records_for_event=1)
    original_records = redis.lrange(INCIDENT_DLQ_QUEUE, 0, -1)

    with (
        patch(
            "app.workers.replay.reset_failed_incident_for_replay",
            side_effect=ConflictError("Human Lock"),
        ),
        patch("app.workers.replay.send_incident_event") as producer,
        pytest.raises(ConflictError, match="Human Lock"),
    ):
        replay_event(repo, redis, EVENT_PAYLOAD["event_id"], max_attempts=5)

    execution_id = repo.find_execution_id(EVENT_PAYLOAD["event_id"])
    assert execution_id is not None
    assert repo.get_status(execution_id) == "failed"
    assert repo.get_retry_state(execution_id)["state"] == "exhausted"
    assert redis.lrange(INCIDENT_DLQ_QUEUE, 0, -1) == original_records
    producer.assert_not_called()


def test_replay_refuses_unknown_event_ids() -> None:
    repo = InMemoryRepo()
    redis = FakeListRedis()
    producer = MagicMock()

    with patch("app.workers.replay.send_incident_event", producer):
        outcome = replay_event(repo, redis, "evt-ghost", max_attempts=5)

    assert outcome.replayed is False
    assert "unknown" in (outcome.reason or "")
    producer.assert_not_called()


def test_load_dead_letters_reads_newest_first_and_skips_garbage() -> None:
    redis = FakeListRedis()
    redis.lpush(INCIDENT_DLQ_QUEUE, "not-json at all")
    redis.lpush(INCIDENT_DLQ_QUEUE, json.dumps({"event_id": "evt-old", "retry_count": 3}))
    redis.lpush(INCIDENT_DLQ_QUEUE, json.dumps({"event_id": "evt-new", "retry_count": 1}))

    records = load_dead_letters(redis)

    assert [record["event_id"] for record in records] == ["evt-new", "evt-old"]


def test_replay_succeeds_when_redis_has_no_records_for_event() -> None:
    """an operator might flush Redis or the record was already cleaned.
    Replay should still succeed (Postgres is the truth) — just 0 records removed."""
    repo = _parked_repo(state="exhausted")
    redis = FakeListRedis()
    # No DLQ records seeded — Redis is empty for this event.
    producer = MagicMock()

    with patch("app.workers.replay.send_incident_event", producer):
        outcome = replay_event(repo, redis, EVENT_PAYLOAD["event_id"], max_attempts=5)

    assert outcome.replayed is True
    assert outcome.removed_records == 0  # nothing to clean, that's fine
    producer.assert_called_once()  # event was still re-enqueued


def test_enqueue_outage_keeps_dlq_and_restores_retryable_parked_state() -> None:
    repo = _parked_repo(state="exhausted")
    redis = FakeListRedis()
    _dlq_with_event(redis, records_for_event=1)
    before = redis.lrange(INCIDENT_DLQ_QUEUE, 0, -1)
    with patch(
        "app.workers.replay.send_incident_event", side_effect=ConnectionError("broker down")
    ):
        with pytest.raises(ConnectionError):
            replay_event(repo, redis, EVENT_PAYLOAD["event_id"], max_attempts=5)
    execution_id = repo.find_execution_id(EVENT_PAYLOAD["event_id"])
    assert repo.get_status(execution_id) == "failed"
    assert repo.get_retry_state(execution_id)["state"] == "exhausted"
    assert redis.lrange(INCIDENT_DLQ_QUEUE, 0, -1) == before


def test_replay_keeps_original_trace_and_does_not_delete_a_fresh_failure() -> None:
    repo = _parked_repo(state="cancelled")
    redis = FakeListRedis()
    redis.lpush(
        INCIDENT_DLQ_QUEUE,
        json.dumps(
            {
                "event_id": EVENT_PAYLOAD["event_id"],
                "correlation_id": "original-webhook-trace",
                "failed_at": "old",
            }
        ),
    )

    def enqueue(*args, **kwargs):
        assert kwargs["correlation_id"] == "original-webhook-trace"
        redis.lpush(
            INCIDENT_DLQ_QUEUE,
            json.dumps(
                {
                    "event_id": EVENT_PAYLOAD["event_id"],
                    "correlation_id": "original-webhook-trace",
                    "failed_at": "new",
                }
            ),
        )

    with patch("app.workers.replay.send_incident_event", side_effect=enqueue):
        result = replay_event(repo, redis, EVENT_PAYLOAD["event_id"], max_attempts=5)
    assert result.removed_records == 1
    remaining = load_dead_letters(redis)
    assert len(remaining) == 1 and remaining[0]["failed_at"] == "new"
