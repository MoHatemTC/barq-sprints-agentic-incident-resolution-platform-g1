"""``load`` — read the incident with the integration user's OAuth identity.

The event carries identifiers only (manual §11.9); everything the graph reasons
over is read here, under the integration user's own permissions.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from agent.config import PIIDetectionMode
from agent.conversation import (
    CONVERSATION_MARKER,
    agent_replies,
    conversation_entries,
    questions_asked,
    render_for_model,
)
from agent.dependencies import AgentDependencies
from agent.guardrails.input_screening import screen_text
from agent.guardrails.pii_detection import PIIProtectionOutcome, protect_residual_pii
from agent.guardrails.semantic_injection_classifier import (
    ClassifierOutcome,
    classify_injection,
)
from agent.policy import snapshot_incident
from agent.prompts import PIIText
from agent.state import AgentState, EventPayload, GateResult, IncidentSnapshot
from agent.tools import ToolCallContext
from observability.redaction import REDACTED, redact_text_with_count

logger = structlog.getLogger(__name__)


def load(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    event = EventPayload.model_validate(state["event"])
    if event.prefetched_incident is not None:
        raw = event.prefetched_incident
    else:
        raw = asyncio.run(
            deps.tools.invoke(
                "read_incident",
                context=ToolCallContext(
                    execution_id=state["execution_id"],
                    correlation_id=state.get("correlation_id"),
                ),
                arguments={"sys_id": event.sys_id},
            )
        )
    incident = _with_conversation(snapshot_incident(raw), state, deps)
    sanitized_incident, gate = _run_input_guardrails(incident, deps)
    return {
        "incident": sanitized_incident.model_dump(mode="json"),
        "input_guardrail": gate.model_dump(mode="json"),
    }


def _with_conversation(
    incident: IncidentSnapshot, state: AgentState, deps: AgentDependencies
) -> IncidentSnapshot:
    """Add what the caller, the agent and engineers said so far (design 2A).

    The transcript is appended to the description the model reasons over, so every
    later step (classification, retrieval, diagnosis, the caller message) sees the
    caller's answers and an engineer's hand-back instruction, and it passes the same
    input screening as the incident text. A failed read leaves a first-run view.
    """
    try:
        raw = asyncio.run(
            deps.tools.invoke(
                "read_conversation",
                context=ToolCallContext(
                    execution_id=state["execution_id"],
                    correlation_id=state.get("correlation_id"),
                ),
                arguments={"sys_id": incident.sys_id},
            )
        )
    except Exception as exc:  # noqa: BLE001 - the incident itself is still readable
        logger.warning("conversation_read_failed", error_type=type(exc).__name__)
        return incident
    entries = conversation_entries(raw if isinstance(raw, dict) else {})
    if not entries:
        return incident
    transcript = render_for_model(entries)
    return incident.model_copy(
        update={
            "conversation": transcript,
            "questions_asked": questions_asked(entries),
            "agent_replies": agent_replies(entries),
            "description": (f"{incident.description}{CONVERSATION_MARKER}{transcript}"),
        }
    )


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

        # 3. Residual-PII detection when shadowed or enforced, then 4. semantic
        # classification. Neither model is called after deterministic screening
        # has already blocked.
        pii_mode = deps.settings.agent_pii_detection_mode
        pii_ran = False
        pii_outcome: PIIProtectionOutcome | None = None
        classifier_ran = False
        classifier_outcome: ClassifierOutcome | None = None
        if pattern_result.flagged:
            blocked = True
            detection_layer = "pattern_screening"
            protected_short = REDACTED
            protected_description = REDACTED
        else:
            protected_short = sanitized_short
            protected_description = sanitized_description
            blocked = False
            detection_layer = "none"

            if pii_mode is not PIIDetectionMode.DISABLED:
                pii_ran = True
                pii_outcome = protect_residual_pii(
                    deps.llm,
                    PIIText(
                        short_description=sanitized_short,
                        description=sanitized_description,
                    ),
                    max_chars=deps.settings.agent_max_incident_chars,
                )

            if pii_mode is PIIDetectionMode.ENFORCED and (
                pii_outcome is None or not pii_outcome.available or pii_outcome.protected is None
            ):
                blocked = True
                detection_layer = "residual_pii"
                protected_short = REDACTED
                protected_description = REDACTED
            else:
                if pii_mode is PIIDetectionMode.ENFORCED:
                    assert pii_outcome is not None and pii_outcome.protected is not None
                    protected_short = pii_outcome.protected.short_description
                    protected_description = pii_outcome.protected.description

                classifier_ran = True
                protected_text = f"{protected_short}\n{protected_description}"
                classifier_outcome = classify_injection(deps.llm, protected_text)

                if classifier_outcome.available:
                    blocked = classifier_outcome.is_injection
                    detection_layer = "semantic_classifier" if blocked else "none"
                else:
                    blocked = True
                    detection_layer = "classifier_unavailable"

                if blocked:
                    protected_short = REDACTED
                    protected_description = REDACTED

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
                    "layer": "residual_pii",
                    "mode": pii_mode.value,
                    "ran": pii_ran,
                    "available": pii_outcome.available if pii_outcome else None,
                    "finding_count": (
                        pii_outcome.finding_count if pii_outcome and pii_outcome.available else None
                    ),
                    "categories": (
                        [category.value for category in pii_outcome.categories]
                        if pii_outcome and pii_outcome.available
                        else None
                    ),
                    "failure_category": pii_outcome.failure_category if pii_outcome else None,
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
            reason=(
                "semantic input screening unavailable; processing withheld"
                if detection_layer == "classifier_unavailable"
                else "blocked by input guardrail"
                if blocked
                else None
            ),
        )
        span.update(
            output={
                "passed": gate.passed,
                "detection_layer": detection_layer,
                "pattern_categories": [c.value for c in pattern_result.categories],
                "pii_mode": pii_mode.value,
                "pii_ran": pii_ran,
                "pii_available": pii_outcome.available if pii_outcome else None,
                "pii_finding_count": (
                    pii_outcome.finding_count if pii_outcome and pii_outcome.available else None
                ),
                "pii_categories": (
                    [category.value for category in pii_outcome.categories]
                    if pii_outcome and pii_outcome.available
                    else None
                ),
                "pii_failure_category": pii_outcome.failure_category if pii_outcome else None,
                "classifier_ran": classifier_ran,
                "classifier_available": (
                    classifier_outcome.available if classifier_outcome else None
                ),
                "redaction_count": redaction_count,
            }
        )

    sanitized_incident = incident.model_copy(
        update={
            "short_description": protected_short,
            "description": protected_description,
        }
    )
    return sanitized_incident, gate


__all__ = ["load"]
