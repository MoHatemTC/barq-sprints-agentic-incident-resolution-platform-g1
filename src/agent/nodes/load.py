"""``load`` — read the incident with the integration user's OAuth identity.

The event carries identifiers only (manual §11.9); everything the graph reasons
over is read here, under the integration user's own permissions.
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.policy import snapshot_incident
from agent.state import AgentState, EventPayload


def load(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    event = EventPayload.model_validate(state["event"])
    raw = deps.servicenow.read_incident(event.sys_id)
    incident = snapshot_incident(raw)
    return {"incident": incident.model_dump(mode="json")}
