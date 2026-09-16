"""Producer tests: the single owner of the Celery envelope format.

The webhook (and the replay CLI) must never hand-write queue messages: a raw
JSON string on the queue crashes Celery workers (KeyError 'properties' —
verified experimentally). These tests pin the routing, the task name and the
UUID coercion at the one boundary that owns the envelope.
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock
from uuid import uuid4

from app.db.redis.keys import INCIDENT_EVENTS_QUEUE
from app.workers import producer
from app.workers.producer import send_incident_event


def test_producer_routes_to_events_queue_with_payload() -> None:
    sink = MagicMock()
    payload = {
        "event_id": "evt-1",
        "sys_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "number": "INC0010001",
        "event_type": "incident.created",
    }

    with mock.patch.object(producer.celery_app, "send_task", sink):
        send_incident_event(payload, "abc-123")

    sink.assert_called_once()
    call = sink.call_args
    assert call.args[0] == producer.PROCESS_INCIDENT_TASK
    assert call.kwargs["args"] == [payload, "abc-123"]
    assert call.kwargs["queue"] == INCIDENT_EVENTS_QUEUE


def test_producer_coerces_uuid_to_str() -> None:
    """Celery's JSON serializer cannot encode UUID objects: the producer is
    the boundary where execution_id is coerced."""
    sink = MagicMock()
    execution_id = uuid4()

    with mock.patch.object(producer.celery_app, "send_task", sink):
        send_incident_event({"event_id": "evt-1"}, execution_id)

    sent = sink.call_args.kwargs["args"]
    assert sent[1] == str(execution_id)
    assert isinstance(sent[1], str)


def test_producer_task_name_matches_registered_task() -> None:
    """The producer and the worker must agree on the registered task name —
    a mismatch strands messages silently on the broker."""
    from app.workers.tasks import process_incident

    assert producer.PROCESS_INCIDENT_TASK == process_incident.name
