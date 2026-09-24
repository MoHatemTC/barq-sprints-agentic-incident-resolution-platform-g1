"""``generate`` — a numbered, cited resolution procedure for a Tier-2 engineer.

Each step must cite an article that was retrieved; a step citing anything else is
dropped here and counted, and the count lowers the confidence score (the manual's
run log shows the same removal: §11.8).
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.nodes.classify import incident_text
from agent.prompts import (
    RESOLUTION_SYSTEM,
    GenerateOutput,
    evidence_block,
    generate_prompt,
    revision_prompt,
)
from agent.state import (
    AgentState,
    CriticFeedback,
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


def _format_critic_feedback(feedback: CriticFeedback) -> str:
    lines = []
    if feedback.invalid_citations:
        lines.append("Invalid Citations:")
        for ic in feedback.invalid_citations:
            step_info = f"Step {ic.step_index}: " if ic.step_index is not None else ""
            lines.append(f"- {step_info}Citation '{ic.citation}' is invalid: {ic.reason}")
    if feedback.unsupported_claims:
        lines.append("Unsupported Claims:")
        for uc in feedback.unsupported_claims:
            lines.append(f"- Step {uc.step_index}: Claim '{uc.claim}' is unsupported: {uc.reason}")
    if feedback.safety_issues:
        lines.append("Safety Issues:")
        for issue in feedback.safety_issues:
            lines.append(f"- {issue}")
    if feedback.feedback_instructions:
        lines.append(f"Actionable Instructions: {feedback.feedback_instructions}")
    return "\n".join(lines) if lines else "Revise unsupported steps."


def generate(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    retrieval = RetrievalResult.model_validate(state["retrieval"])
    diagnosis = Diagnosis.model_validate(state["diagnosis"])
    evidence = [h for h in retrieval.hits if h.article_id in diagnosis.matched_article_ids]
    evidence = evidence or retrieval.hits

    critic_feedback_raw = state.get("critic_feedback")
    prev_draft_raw = state.get("draft")
    revision_count = state.get("revision_count", 0)

    if critic_feedback_raw and prev_draft_raw:
        feedback = CriticFeedback.model_validate(critic_feedback_raw)
        prev_draft = Draft.model_validate(prev_draft_raw)
        prompt = revision_prompt(
            incident_text=incident_text(incident, deps.settings.agent_max_incident_chars),
            evidence_text=evidence_block(evidence),
            cause=diagnosis.probable_cause,
            previous_steps=prev_draft.rendered,
            feedback_text=_format_critic_feedback(feedback),
        )
        new_revision_count = revision_count + 1
    else:
        prompt = generate_prompt(
            incident_text(incident, deps.settings.agent_max_incident_chars),
            evidence_block(evidence),
            diagnosis.probable_cause,
        )
        new_revision_count = revision_count

    answer = deps.llm.structured(
        purpose="generate",
        system=RESOLUTION_SYSTEM,
        prompt=prompt,
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
        revision_count=new_revision_count,
    )
    return {
        "draft": draft.model_dump(mode="json"),
        "revision_count": new_revision_count,
    }
