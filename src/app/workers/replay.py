"""Dead-letter inspection and replay — the human exit of the DLQ path.

The Redis DLQ list (``barq:incident:dlq``) is a bulletin board for humans:
no worker ever consumes it. PostgreSQL is the durable truth. Replay therefore

1. re-reads the ORIGINAL event from the immutable ``events`` table,
2. resets the parked state atomically (``reset_for_replay`` is guarded with
   ``WHERE state IN ('exhausted', 'cancelled')`` — 0 rows ⇒ refuse),
3. removes the event's records from the Redis list, and
4. re-enqueues through :func:`app.workers.producer.send_incident_event` so the
   envelope format keeps a single owner.

Order matters: reset → LREM → enqueue. If replay dies between reset and
enqueue the event sits in 'queued' without a live message; recovery is the
documented re-enqueue sweep (``events WHERE status = 'queued'``). Enqueueing
before LREM instead would race the worker: a fresh failure could push a new
DLQ record between our LRANGE and our LREM. Exact-string LREM is also safe on
its own — a new record carries a new ``failed_at``, so it never matches an
older record's string.

CLI::

    python -m app.workers.replay list
    python -m app.workers.replay replay <event_id>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import redis as redis_lib
import structlog

from app.core.config import get_settings
from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.workers.db import WorkerRepo, build_worker_repo
from app.workers.producer import send_incident_event

logger = structlog.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    """What one replay attempt actually did (or why it refused to)."""

    event_id: str
    execution_id: UUID | None = None
    replayed: bool = False
    removed_records: int = 0
    reason: str | None = None


def load_dead_letters(redis_client: Any) -> list[dict]:
    """Parsed DLQ records, newest first. A malformed record is skipped with a
    warning — an inspector that crashes on one bad line is useless during the
    incident it was built for."""
    raw_records = redis_client.lrange(INCIDENT_DLQ_QUEUE, 0, -1)
    records: list[dict] = []
    for raw in raw_records:
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("dead_letter_record_unparseable", raw_prefix=str(raw)[:80])
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _remove_event_records(redis_client: Any, event_id: str) -> int:
    """LREM every record of this event by its exact stored string (count=0).

    Uses a single-pass scan and batch pipeline to eliminate repetitive network round-trips.
    """
    raw_records = redis_client.lrange(INCIDENT_DLQ_QUEUE, 0, -1)
    if not raw_records:
        return 0

    to_remove: list[str] = []
    for raw in raw_records:
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("event_id") == event_id:
            to_remove.append(raw)

    if not to_remove:
        return 0

    if hasattr(redis_client, "pipeline") and callable(redis_client.pipeline):
        pipe = redis_client.pipeline()
        for raw in to_remove:
            pipe.lrem(INCIDENT_DLQ_QUEUE, 0, raw)
        results = pipe.execute()
        return sum(int(r or 0) for r in results)

    removed = 0
    for raw in to_remove:
        removed += int(redis_client.lrem(INCIDENT_DLQ_QUEUE, 0, raw) or 0)
    return removed


def replay_event(
    repo: WorkerRepo,
    redis_client: Any,
    event_id: str,
    *,
    max_attempts: int,
) -> ReplayOutcome:
    """Reset one parked event and put it back on the events queue.

    Refuses (returns ``replayed=False``) for unknown event ids and for events
    whose retry state is not parked — a queued/running execution belongs to a
    live worker and is never reset from underneath it.
    """
    payload = repo.get_event_payload(event_id)
    if payload is None:
        return ReplayOutcome(event_id=event_id, reason=f"unknown event_id: {event_id}")

    execution_id = repo.find_execution_id(event_id)
    if execution_id is None:
        return ReplayOutcome(
            event_id=event_id, reason=f"no execution exists for event_id: {event_id}"
        )

    if not repo.reset_for_replay(execution_id, max_attempts=max_attempts):
        state = (repo.get_retry_state(execution_id) or {}).get("state")
        return ReplayOutcome(
            event_id=event_id,
            execution_id=execution_id,
            reason=f"event is not parked (retry_state={state!r}) — "
            "only 'exhausted'/'cancelled' events replay",
        )

    removed = _remove_event_records(redis_client, event_id)
    send_incident_event(payload, execution_id)
    logger.info(
        "event_replayed",
        event_id=event_id,
        execution_id=str(execution_id),
        removed_records=removed,
    )
    return ReplayOutcome(
        event_id=event_id,
        execution_id=execution_id,
        replayed=True,
        removed_records=removed,
    )


def _print_listing(redis_client: Any) -> None:
    records = load_dead_letters(redis_client)
    if not records:
        print("DLQ is empty — nothing dead-lettered.")
        return
    print(f"{len(records)} dead-lettered record(s), newest first:\n")
    print(f"{'event_id':<36} {'retries':>7}  {'failed_at':<27} reason")
    for record in records:
        reason = str(record.get("failure_reason", ""))[:60]
        print(
            f"{str(record.get('event_id', '?')):<36} "
            f"{str(record.get('retry_count', '?')):>7}  "
            f"{str(record.get('failed_at', '?')):<27} {reason}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.replay",
        description="Inspect and replay the incident dead-letter queue.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="show dead-lettered events (newest first)")
    replay_parser = subparsers.add_parser("replay", help="reset one parked event and re-enqueue it")
    replay_parser.add_argument("event_id", help="the event_id to replay")

    args = parser.parse_args(argv)

    settings = get_settings()
    repo = build_worker_repo(settings)
    redis_client = redis_lib.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=(
            settings.redis_password.get_secret_value()
            if settings.redis_password is not None
            else None
        ),
        decode_responses=True,
    )

    if args.command == "list":
        _print_listing(redis_client)
        return 0

    outcome = replay_event(
        repo, redis_client, args.event_id, max_attempts=settings.worker_max_retries
    )
    if outcome.replayed:
        print(
            f"replayed {outcome.event_id} (execution {outcome.execution_id}): "
            f"state reset, {outcome.removed_records} DLQ record(s) removed, event re-enqueued"
        )
        return 0
    print(f"refused {outcome.event_id}: {outcome.reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["ReplayOutcome", "load_dead_letters", "main", "replay_event"]
