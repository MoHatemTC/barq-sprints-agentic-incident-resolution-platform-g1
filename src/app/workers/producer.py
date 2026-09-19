"""Event producer — the single owner of the Celery envelope format.

The webhook and the replay CLI must never hand-write queue messages: a raw
JSON string on the queue crashes Celery workers (``KeyError: 'properties'`` —
verified experimentally, see docs/sprint2_worker_topology.md). Everything that
enqueues an incident event goes through this one function, which also coerces
``execution_id`` to ``str`` at the boundary (Celery's JSON serializer cannot
encode UUID objects).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.db.redis.keys import INCIDENT_EVENTS_QUEUE
from app.workers.celery_app import celery_app

# Must match the registered name of the task in app.workers.tasks — the
# producer/worker name agreement is pinned by tests/workers/test_producer.py.
PROCESS_INCIDENT_TASK = "app.workers.tasks.process_incident"

#: Message header carrying the request's correlation id to the worker (S2.5). A
#: header, not a task argument, so the task signature and the replay CLI are
#: unchanged; a message without it is traced under its execution id.
CORRELATION_HEADER = "x_correlation_id"


def send_incident_event(
    payload: dict[str, Any],
    execution_id: str | UUID,
    correlation_id: str | None = None,
) -> None:
    """Enqueue one accepted incident event for worker processing."""
    options: dict[str, Any] = {}
    if correlation_id:
        options["headers"] = {CORRELATION_HEADER: correlation_id}
    celery_app.send_task(
        PROCESS_INCIDENT_TASK,
        args=[payload, str(execution_id)],
        queue=INCIDENT_EVENTS_QUEUE,
        **options,
    )


__all__ = ["CORRELATION_HEADER", "PROCESS_INCIDENT_TASK", "send_incident_event"]

