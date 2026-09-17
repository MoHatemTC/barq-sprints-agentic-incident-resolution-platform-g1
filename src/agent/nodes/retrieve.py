"""``retrieve`` — hybrid, published-only, category-filtered knowledge search."""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.llm import bounded
from agent.state import AgentState, ClassificationResult, IncidentSnapshot

QUERY_CHARS = 1000


def build_query(incident: IncidentSnapshot) -> str:
    text = f"{incident.short_description}\n{incident.description}".strip()
    return bounded(text, QUERY_CHARS) or incident.category or incident.number


def retrieve(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    classification = ClassificationResult.model_validate(state["classification"])
    result = deps.retriever.search(
        build_query(incident),
        classification=classification.label,
        top_k=deps.settings.agent_retrieval_top_k,
        threshold=deps.settings.agent_retrieval_threshold,
    )
    return {"retrieval": result.model_dump(mode="json")}
