"""``retrieve`` — hybrid, published-only, category-filtered knowledge search."""

from __future__ import annotations

import re
from typing import Any

from agent.conversation import CONVERSATION_MARKER
from agent.dependencies import AgentDependencies
from agent.llm import bounded
from agent.query_rewrite import rewrite_query
from agent.state import AgentState, ClassificationResult, IncidentSnapshot

QUERY_CHARS = 1000


_ENTRY = re.compile(r"^\[([^\]]+)\] ", re.MULTILINE)


def _people_said(transcript: str) -> list[str]:
    """What the caller and engineers said, newest first; the agent's own messages are
    left out (its long questions would otherwise outweigh the caller's words)."""
    marks = list(_ENTRY.finditer(transcript))
    said = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(transcript)
        if not mark.group(1).startswith("BARQ AI Agent"):
            said.append(transcript[mark.end() : end].strip())
    return [text for text in reversed(said) if text]


def build_query(incident: IncidentSnapshot) -> str:
    description, _, transcript = incident.description.partition(CONVERSATION_MARKER)
    text = "\n".join(
        part
        for part in (*_people_said(transcript), incident.short_description, description)
        if part.strip()
    ).strip()
    return bounded(text or incident.category or incident.number, QUERY_CHARS)


def retrieve(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    classification = ClassificationResult.model_validate(state["classification"])
    result = deps.retriever.search(
        rewrite_query(build_query(incident), deps),
        classification=classification.label,
        top_k=deps.settings.agent_retrieval_top_k,
        threshold=deps.settings.agent_retrieval_threshold,
        incident_category=incident.category or None,
    )
    return {"retrieval": result.model_dump(mode="json")}
