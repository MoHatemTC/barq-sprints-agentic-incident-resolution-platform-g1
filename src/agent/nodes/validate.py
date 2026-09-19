"""``validate`` — is the incident still eligible on re-read? (manual §11.3)"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.policy import check_eligibility
from agent.state import AgentState, EventPayload, IncidentSnapshot


def validate(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    event = EventPayload.model_validate(state["event"])
    incident = IncidentSnapshot.model_validate(state["incident"])
    eligibility = check_eligibility(
        incident,
        event_number=event.number,
        supported_categories=deps.settings.agent_supported_categories,
    )
    return {"eligibility": eligibility.model_dump(mode="json")}
