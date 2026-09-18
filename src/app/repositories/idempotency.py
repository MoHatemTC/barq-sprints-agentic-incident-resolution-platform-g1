"""Atomic PostgreSQL acceptance primitive for Sprint 1 inbound events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.db.models import Event, Execution, IdempotencyKey
from app.db.session import SessionFactory

IDEMPOTENCY_CONSTRAINT_NAME = "uq_idempotency_keys_event_id"


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """Exact four-field Sprint 1 event accepted by the persistence boundary."""

    event_id: str
    sys_id: str
    number: str
    event_type: str


class EventAcceptanceStatus(StrEnum):
    """Database acceptance outcome, independent of future HTTP behavior."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class EventAcceptanceResult:
    """Result of atomically claiming and persisting one inbound event."""

    status: EventAcceptanceStatus
    event_id: str
    event_record_id: UUID | None = None
    execution_id: UUID | None = None


def _postgres_constraint_name(error: BaseException) -> str | None:
    """Find structured PostgreSQL constraint metadata through driver wrappers."""
    pending: list[object] = [error]
    visited: set[int] = set()

    while pending:
        candidate = pending.pop()
        if id(candidate) in visited:
            continue
        visited.add(id(candidate))

        constraint_name = getattr(candidate, "constraint_name", None)
        if isinstance(constraint_name, str):
            return constraint_name

        diagnostics = getattr(candidate, "diag", None)
        diagnostic_name = getattr(diagnostics, "constraint_name", None)
        if isinstance(diagnostic_name, str):
            return diagnostic_name

        for attribute in ("orig", "__cause__", "__context__"):
            nested = getattr(candidate, attribute, None)
            if nested is not None:
                pending.append(nested)

    return None


async def accept_inbound_event(
    session_factory: SessionFactory,
    inbound_event: InboundEvent,
) -> EventAcceptanceResult:
    """Atomically claim an event ID, persist its event, and create its execution.

    This function owns one transaction and one session. PostgreSQL arbitrates
    duplicates when the idempotency-key INSERT is flushed; no existence query is
    performed. Any later failure rolls the key, event, and execution back together.
    """
    async with session_factory() as session:
        try:
            async with session.begin():
                event_record_id = uuid4()
                execution_id = uuid4()
                session.add_all([
                    IdempotencyKey(event_id=inbound_event.event_id),
                    Event(
                        id=event_record_id,
                        event_id=inbound_event.event_id,
                        incident_sys_id=inbound_event.sys_id,
                        incident_number=inbound_event.number,
                        event_type=inbound_event.event_type,
                        contract_version="v1",
                    ),
                    Execution(
                        execution_id=execution_id,
                        event_record_id=event_record_id,
                        incident_sys_id=inbound_event.sys_id,
                        status="accepted",
                    ),
                ])

                accepted_result = EventAcceptanceResult(
                    status=EventAcceptanceStatus.ACCEPTED,
                    event_id=inbound_event.event_id,
                    event_record_id=event_record_id,
                    execution_id=execution_id,
                )
        except IntegrityError as error:
            if _postgres_constraint_name(error) in (IDEMPOTENCY_CONSTRAINT_NAME, "uq_events_event_id"):
                return EventAcceptanceResult(
                    status=EventAcceptanceStatus.DUPLICATE,
                    event_id=inbound_event.event_id,
                )
            raise

    return accepted_result


__all__ = [
    "EventAcceptanceResult",
    "EventAcceptanceStatus",
    "InboundEvent",
    "accept_inbound_event",
]
