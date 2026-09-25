"""``classify`` — label the incident with the S1.1 classification vocabulary."""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.llm import bounded
from agent.prompts import CLASSIFY_SYSTEM, ClassifyOutput, classify_prompt, incident_block
from agent.state import AgentState, ClassificationResult, IncidentSnapshot
from app.models.knowledge import Classification


def incident_text(incident: IncidentSnapshot, limit: int) -> str:
    return incident_block(
        incident,
        short=bounded(incident.short_description, 300),
        description=bounded(incident.description, limit),
    )


def classify(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    answer = deps.llm.structured(
        purpose="classify",
        system=CLASSIFY_SYSTEM,
        prompt=classify_prompt(incident_text(incident, deps.settings.agent_max_incident_chars)),
        schema=ClassifyOutput,
    )
    result = ClassificationResult(
        label=Classification(answer.label),
        rationale=answer.rationale,
        model_confidence=min(1.0, max(0.0, answer.confidence)),
    )
    return {"classification": result.model_dump(mode="json")}
