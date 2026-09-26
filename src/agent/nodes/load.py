"""``load`` — read the incident with the integration user's OAuth identity.

The event carries identifiers only (manual §11.9); everything the graph reasons
over is read here, under the integration user's own permissions.
"""

from __future__ import annotations

from typing import Any

from agent.dependencies import AgentDependencies
from agent.guardrails.input_screening import screen_text
from agent.guardrails.semantic_injection_classifier import (
    ClassifierOutcome,
    classify_injection,
)
from agent.policy import snapshot_incident
from agent.state import AgentState, EventPayload, GateResult, IncidentSnapshot
from observability.redaction import redact_text_with_count


def load(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    event = EventPayload.model_validate(state["event"])
    raw = deps.servicenow.read_incident(event.sys_id)
    incident = snapshot_incident(raw)

    sanitized_incident, gate = _run_input_guardrails(incident, deps)

    return {
        "incident": sanitized_incident.model_dump(mode="json"),
        "input_guardrail": gate.model_dump(mode="json"),
    }


def _run_input_guardrails(
    incident: IncidentSnapshot, deps: AgentDependencies
) -> tuple[IncidentSnapshot, GateResult]:
    with deps.tracer.span(
        "guardrail.input_screening",
        as_type="guardrail",
        input={"stage": "input_guardrail"},
    ) as span:
        # 1. Deterministic pattern screening runs on the RAW text.
        raw_text = f"{incident.short_description}\n{incident.description}"
        pattern_result = screen_text(raw_text)

        # 2. Redaction
        sanitized_short, short_count = redact_text_with_count(incident.short_description)
        sanitized_description, description_count = redact_text_with_count(incident.description)
        redaction_count = short_count + description_count

        # 3. Semantic classifier, on the sanitized text, and only when
        # deterministic screening has already passed
        classifier_ran = False
        classifier_outcome: ClassifierOutcome | None = None
        if pattern_result.flagged:
            blocked = True
            detection_layer = "pattern_screening"
        else:
            classifier_ran = True
            sanitized_text = f"{sanitized_short}\n{sanitized_description}"
            classifier_outcome = classify_injection(deps.llm, sanitized_text)
            if classifier_outcome.available:
                blocked = classifier_outcome.is_injection
                detection_layer = "semantic_classifier" if blocked else "none"
            else:
                blocked = False
                detection_layer = "none"

        gate = GateResult(
            gate="input_guardrail",
            passed=not blocked,
            implemented=True,
            checks=[
                {
                    "layer": "pattern_screening",
                    "flagged": pattern_result.flagged,
                    "categories": [c.value for c in pattern_result.categories],
                },
                {
                    "layer": "semantic_classifier",
                    "ran": classifier_ran,
                    "available": (classifier_outcome.available if classifier_outcome else None),
                    "flagged": (
                        classifier_outcome.is_injection
                        if classifier_outcome and classifier_outcome.available
                        else None
                    ),
                    "failure_category": (
                        classifier_outcome.failure_category if classifier_outcome else None
                    ),
                },
            ],
            reason="blocked by input guardrail" if blocked else None,
        )
        span.update(
            output={
                "passed": gate.passed,
                "detection_layer": detection_layer,
                "pattern_categories": [c.value for c in pattern_result.categories],
                "classifier_ran": classifier_ran,
                "classifier_available": (
                    classifier_outcome.available if classifier_outcome else None
                ),
                "redaction_count": redaction_count,
            }
        )

    sanitized_incident = incident.model_copy(
        update={
            "short_description": sanitized_short,
            "description": sanitized_description,
        }
    )
    return sanitized_incident, gate


__all__ = ["load"]
