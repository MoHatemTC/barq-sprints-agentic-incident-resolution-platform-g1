"""``diagnose`` — probable cause, grounded in the retrieved evidence only."""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.nodes.classify import incident_text
from agent.prompts import DIAGNOSE_SYSTEM, DiagnoseOutput, diagnose_prompt, evidence_block
from agent.state import (
    AgentState,
    ClassificationResult,
    Diagnosis,
    IncidentSnapshot,
    RetrievalResult,
)


def diagnose(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    incident = IncidentSnapshot.model_validate(state["incident"])
    classification = ClassificationResult.model_validate(state["classification"])
    retrieval = RetrievalResult.model_validate(state["retrieval"])

    with deps.tracer.span(
        "agent.diagnostic",
        as_type="agent",
        input={
            "incident_number": incident.number,
            "category": classification.label.value,
            "evidence_count": len(retrieval.hits),
            "priority": incident.priority,
        },
        metadata={
            "agent": "diagnostic",
            "execution_id": state.get("execution_id"),
        },
    ) as span:
        answer = deps.llm.structured(
            purpose="diagnose",
            system=DIAGNOSE_SYSTEM,
            prompt=diagnose_prompt(
                incident_text(incident, deps.settings.agent_max_incident_chars),
                evidence_block(retrieval.hits),
                classification.label.value,
            ),
            schema=DiagnoseOutput,
        )
        retrieved = {hit.article_id for hit in retrieval.hits}
        # A cited article that was not retrieved is not evidence.
        matched = [a for a in dict.fromkeys(answer.matched_article_ids) if a in retrieved]
        diagnosis = Diagnosis(
            probable_cause=answer.probable_cause,
            matched_article_ids=matched,
            symptom_match=answer.symptom_match and bool(matched),
            model_confidence=min(1.0, max(0.0, answer.confidence)),
            rationale=answer.rationale,
        )
        result = {"diagnosis": diagnosis.model_dump(mode="json")}
        span.update(output=result)
        return result
