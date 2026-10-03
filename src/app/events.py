"""Registered incident event types and the contract versions that carry them.

This is the one place to extend when ServiceNow starts sending a new kind of event:
the webhook schema, the database check, the agent state and the worker all read
these sets, so adding a type here (plus a migration widening the check) is the whole
change.

Contract v1 is the original four-field event (created / updated). Contract v2 adds the
conversation events and an optional ``actor_sys_id`` — still identifiers only, never
incident text.
"""

from __future__ import annotations

V1_EVENT_TYPES: frozenset[str] = frozenset({"incident.created", "incident.updated"})

#: Events after which the agent (re)considers the incident: the graph runs.
ACT_EVENT_TYPES: frozenset[str] = V1_EVENT_TYPES | {
    "incident.caller_replied",
    "incident.caller_updated",
    "incident.handed_back",
}

#: Things a person did that the agent records for learning but does not act on.
OBSERVE_EVENT_TYPES: frozenset[str] = frozenset(
    {"incident.engineer_replied", "incident.reopened", "incident.closed"}
)

EVENT_TYPES: frozenset[str] = ACT_EVENT_TYPES | OBSERVE_EVENT_TYPES

CONTRACT_EVENT_TYPES: dict[str, frozenset[str]] = {"v1": V1_EVENT_TYPES, "v2": EVENT_TYPES}


def is_observe_only(event_type: str | None) -> bool:
    return event_type in OBSERVE_EVENT_TYPES


__all__ = [
    "ACT_EVENT_TYPES",
    "CONTRACT_EVENT_TYPES",
    "EVENT_TYPES",
    "OBSERVE_EVENT_TYPES",
    "V1_EVENT_TYPES",
    "is_observe_only",
]
