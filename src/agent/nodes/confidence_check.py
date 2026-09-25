"""``confidence_check`` — the §11.7 confidence floor.

Signature is stable for Sprint 4, which will add calibration. The Sprint 2 score is
deterministic from what earlier nodes recorded:

    score = model_confidence × (1.0 if the symptom matched else 0.5)
                             × kept_steps / drafted_steps

and 0 when the draft has no usable step.
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.state import AgentState, ConfidenceResult, Diagnosis, Draft


def score(diagnosis: Diagnosis, draft: Draft) -> float:
    kept = len(draft.steps)
    drafted = kept + draft.dropped_steps
    if kept == 0 or drafted == 0:
        return 0.0
    match_factor = 1.0 if diagnosis.symptom_match else 0.5
    return round(diagnosis.model_confidence * match_factor * kept / drafted, 4)


def confidence_check(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    diagnosis = Diagnosis.model_validate(state["diagnosis"])
    draft = Draft.model_validate(state["draft"])
    value = score(diagnosis, draft)
    floor = deps.settings.agent_confidence_floor
    result = ConfidenceResult(score=value, floor=floor, passed=value >= floor)
    return {"confidence": result.model_dump(mode="json")}
