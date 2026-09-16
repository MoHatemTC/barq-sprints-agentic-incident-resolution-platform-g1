"""Redis key constants and namespace definitions across the platform."""

from __future__ import annotations

# Primary FIFO / LPUSH queue for inbound incident events
INCIDENT_EVENTS_QUEUE = "barq:incident:events"

# Dead Letter Queue (DLQ) for poisoned or exhausted retry events
INCIDENT_DLQ_QUEUE = "barq:incident:dlq"

__all__ = [
    "INCIDENT_DLQ_QUEUE",
    "INCIDENT_EVENTS_QUEUE",
]
