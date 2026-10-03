"""``determine_risk`` — decide risk strictly before retrieval.

The base verdict uses the incident's priority, impact, urgency, service tier and the
classification label; ``agent.triage`` then looks at related incidents and the text for
attacks, outages and repeats. Neither ever sees evidence or a draft, so the verdict
cannot be argued down by what retrieval finds (``docs/sprint3_graph_design.md`` §3;
reassessment rules in ``docs/barq_agentic_platform_design.md`` §6.3).
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.policy import assess_risk
from agent.state import AgentState, ClassificationResult, IncidentSnapshot
from agent.tools import ToolCallContext
from agent.triage import adjust_risk, look_around


def determine_risk(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    classification = ClassificationResult.model_validate(state["classification"])
    base = assess_risk(
        incident,
        classification,
        risk_priorities=deps.settings.agent_risk_priorities,
    )
    # Look around (similar open incidents, the caller's history) and adjust: raising is
    # code; lowering a priority-only HIGH also needs a model check (agent.triage).
    context = ToolCallContext(
        execution_id=str(state.get("execution_id") or ""),
        correlation_id=state.get("correlation_id"),
    )
    situation = look_around(deps, context, incident)
    risk = adjust_risk(base, incident, situation, deps)
    return {"risk": risk.model_dump(mode="json")}
