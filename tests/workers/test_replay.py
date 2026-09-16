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

from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.workers.db import InMemoryRepo
from app.workers.replay import load_dead_letters, replay_event

EVENT_PAYLOAD = {
    "event_id": "evt-replay-0001",
    "sys_id": "SYS0000001",
    "number": "INC0000001",
    "event_type": "incident.created",
    "contract_version": "v1",
}


class FakeListRedis:
    """Just enough of redis-py for the DLQ list: LPUSH / LRANGE(0,-1) / LREM(0)."""

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
    producer.assert_called_once_with(EVENT_PAYLOAD, outcome.execution_id)
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
