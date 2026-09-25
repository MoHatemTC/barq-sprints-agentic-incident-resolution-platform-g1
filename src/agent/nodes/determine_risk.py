"""``determine_risk`` — decide risk from the record alone, strictly before retrieval.

The verdict uses the incident's priority, impact, urgency, service tier and the
classification label. It never sees evidence or a draft, so it cannot be argued
out of a HIGH verdict by what retrieval finds (design record:
``docs/sprint3_graph_design.md`` §3).
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.policy import assess_risk
from agent.state import AgentState, ClassificationResult, IncidentSnapshot


def determine_risk(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    classification = ClassificationResult.model_validate(state["classification"])
    risk = assess_risk(
        incident,
        classification,
        risk_priorities=deps.settings.agent_risk_priorities,
    )
    return {"risk": risk.model_dump(mode="json")}
