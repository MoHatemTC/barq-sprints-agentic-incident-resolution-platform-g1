"""``generate`` — a numbered, cited resolution procedure for a Tier-2 engineer.

Each step must cite an article that was retrieved; a step citing anything else is
dropped here and counted, and the count lowers the confidence score (the manual's
run log shows the same removal: §11.8).
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.nodes.classify import incident_text
from agent.prompts import GENERATE_SYSTEM, GenerateOutput, evidence_block, generate_prompt
from agent.state import (
    AgentState,
    Diagnosis,
    Draft,
    DraftStep,
    EvidenceItem,
    IncidentSnapshot,
    RetrievalResult,
)

#: ``ai_suggestion`` is a String(4000) (S1.1 field model).
MAX_SUGGESTION_CHARS = 4000


def render(steps: list[DraftStep], evidence: list[EvidenceItem]) -> tuple[str, list[str]]:
    by_id = {item.article_id: item for item in evidence}
    lines = []
    sources: list[str] = []
    for number, step in enumerate(steps, start=1):
        item = by_id[step.article_id]
        cite = f"{item.article_number} v{item.version} §{step.section}"
        lines.append(f"{number}. {step.text.strip()} [{cite}]")
        source = f"{item.article_number} v{item.version} — {item.title}"
        if source not in sources:
            sources.append(source)
    text = "\n".join(lines)
    if sources:
        text += "\n\nSources: " + "; ".join(sources)
    return text, sources


def generate(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    retrieval = RetrievalResult.model_validate(state["retrieval"])
    diagnosis = Diagnosis.model_validate(state["diagnosis"])
    evidence = [h for h in retrieval.hits if h.article_id in diagnosis.matched_article_ids]
    evidence = evidence or retrieval.hits

    answer = deps.llm.structured(
        purpose="generate",
        system=GENERATE_SYSTEM,
        prompt=generate_prompt(
            incident_text(incident, deps.settings.agent_max_incident_chars),
            evidence_block(evidence),
            diagnosis.probable_cause,
        ),
        schema=GenerateOutput,
    )
    allowed = {item.article_id for item in evidence}
    steps = [
        DraftStep(text=s.text, article_id=s.article_id, section=s.section)
        for s in answer.steps
        if s.article_id in allowed and s.text.strip()
    ]
    rendered, sources = render(steps, evidence)
    while len(rendered) > MAX_SUGGESTION_CHARS and steps:
        steps = steps[:-1]
        rendered, sources = render(steps, evidence)
    draft = Draft(
        steps=steps,
        rendered=rendered,
        dropped_steps=len(answer.steps) - len(steps),
        sources=sources,
    )
    return {"draft": draft.model_dump(mode="json")}
