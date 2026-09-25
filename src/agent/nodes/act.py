"""``act`` — the single terminal node: write the suggestion, or escalate.

Every path through the graph ends here, so there is exactly one place that writes
to ServiceNow and exactly one record of the outcome. The writes are the manual's
§11.6 permitted actions only — AI fields, an internal work note and the human
review flag — sent as one PATCH through the allow-listed gateway. Nothing resolves,
closes, reassigns or contacts the requester: those actions do not exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.dependencies import AgentDependencies
from agent.servicenow import HumanLockedError
from agent.state import (
    AgentState,
    ClassificationResult,
    ConfidenceResult,
    Diagnosis,
    Draft,
    Eligibility,
    FinalOutput,
    GateResult,
    IncidentSnapshot,
    Outcome,
    RetrievalResult,
    RiskAssessment,
    RiskLevel,
)
from app.models.execution_log import (
    ExecutionAction,
    ExecutionLogCreatePayload,
    ExecutionStatus,
)
from app.models.incident import AIProcessingState, IncidentUpdatePayload

PREFIX = "AI Suggested Response"

#: S1.1 field model: ``complete`` means an accepted resolution was applied and needs
#: ``ai_resolution`` in the same write; ``awaiting_approval`` "pauses automation".
#: A draft or an escalation both leave the incident waiting for a person, and
#: processing end stays blank because the state is not terminal.
PAUSED = AIProcessingState.AWAITING_APPROVAL.value


def decide_outcome(state: AgentState) -> Outcome:
    """The outcome implied by the recorded state. Mirrors the edge conditions."""
    eligibility = state.get("eligibility")
    if eligibility is not None and not Eligibility.model_validate(eligibility).eligible:
        return Outcome.SKIPPED_INELIGIBLE
    risk = state.get("risk")
    if risk is None or RiskAssessment.model_validate(risk).level is RiskLevel.HIGH:
        return Outcome.ESCALATED_HIGH_RISK
    retrieval = state.get("retrieval")
    if retrieval is None or not RetrievalResult.model_validate(retrieval).sufficient:
        return Outcome.ESCALATED_NO_EVIDENCE
    diagnosis = state.get("diagnosis")
    if diagnosis is None or not Diagnosis.model_validate(diagnosis).matched_article_ids:
        return Outcome.ESCALATED_NO_EVIDENCE
    for key in ("verification", "safety"):
        gate = state.get(key)
        if gate is None or not GateResult.model_validate(gate).passed:
            return Outcome.ESCALATED_BLOCKED
    confidence = state.get("confidence")
    if confidence is None or not ConfidenceResult.model_validate(confidence).passed:
        return Outcome.ESCALATED_LOW_CONFIDENCE
    if state.get("draft") is None:
        # Every other section is present and passing but generate recorded no
        # draft, so there is nothing to suggest. Escalate instead of claiming one.
        return Outcome.ESCALATED_NO_EVIDENCE
    return Outcome.SUGGESTED


def _blocked_gate(state: AgentState) -> GateResult | None:
    for key in ("verification", "safety"):
        gate = state.get(key)
        if gate is not None and not GateResult.model_validate(gate).passed:
            return GateResult.model_validate(gate)
    return None


def _search_finding(retrieval: RetrievalResult, classification: str | None) -> str:
    """What was searched and what was found, in the manual's run-log wording."""
    if retrieval.category_filter is None:
        return (
            f"The classification '{classification or 'unknown'}' has no knowledge-base "
            "category, so no search was run."
        )
    scope = retrieval.category_filter.replace(",", ", ")
    best = max(retrieval.hits, key=lambda h: h.relevance, default=None)
    if best is None:
        return f"Searched published {scope} articles: nothing matched."
    found = (
        f"Searched published {scope} articles. Best match {best.article_number} "
        f"v{best.version} ({best.title}) scored {best.relevance:.2f}"
    )
    if retrieval.sufficient:
        return f"{found}, but none of the retrieved articles describes this fault."
    return f"{found} against a threshold of {retrieval.threshold:.2f}."


def compose(state: AgentState, outcome: Outcome) -> FinalOutput:
    classification = (
        ClassificationResult.model_validate(state["classification"]).label.value
        if state.get("classification")
        else None
    )
    risk = RiskAssessment.model_validate(state["risk"]) if state.get("risk") else None
    if outcome is Outcome.SKIPPED_INELIGIBLE:
        ineligible = Eligibility.model_validate(state["eligibility"]).reasons
        return FinalOutput(
            outcome=outcome,
            summary="Not eligible: " + "; ".join(ineligible),
            human_review_required=False,
            processing_state=AIProcessingState.PENDING.value,
        )
    if outcome is Outcome.ESCALATED_HIGH_RISK:
        reasons = "; ".join(risk.reasons) if risk else "risk not assessed"
        note = (
            f"{PREFIX}: risk assessed as high before retrieval — {reasons}. "
            "No action taken. Escalated for human decision."
        )
        return FinalOutput(
            outcome=outcome,
            summary=note,
            classification=classification,
            work_note=note,
            human_review_required=True,
            processing_state=PAUSED,
        )
    if outcome is Outcome.ESCALATED_NO_EVIDENCE:
        # decide_outcome selects this outcome precisely when the retrieval section
        # is missing, so it must not be indexed here: that would turn the
        # fail-closed route into a KeyError.
        retrieval = state.get("retrieval")
        if retrieval is not None:
            finding = _search_finding(RetrievalResult.model_validate(retrieval), classification)
            note = f"{PREFIX}: no matching knowledge article found. {finding}"
        else:
            note = f"{PREFIX}: no search result was recorded, so no evidence was considered."
        note += " No draft written. Escalated for human handling."
        return FinalOutput(
            outcome=outcome,
            summary=note,
            classification=classification,
            work_note=note,
            human_review_required=True,
            processing_state=PAUSED,
        )
    if outcome is Outcome.ESCALATED_BLOCKED:
        gate = _blocked_gate(state)
        attempts = int(state.get("revision_count", 0))
        attempts_str = f" after {attempts} revision attempts" if attempts > 0 else ""
        detail = (
            f"the {gate.gate} check{attempts_str}: {gate.reason or 'failed'}"
            if gate
            else "a missing check"
        )
        note = f"{PREFIX}: draft blocked by {detail}. No draft written. Escalated."
        return FinalOutput(
            outcome=outcome,
            summary=note,
            classification=classification,
            work_note=note,
            human_review_required=True,
            processing_state=PAUSED,
        )
    # Same reasoning as the retrieval section above: a missing confidence check is
    # itself what selects ESCALATED_LOW_CONFIDENCE, so read it defensively.
    recorded_confidence = state.get("confidence")
    confidence = (
        ConfidenceResult.model_validate(recorded_confidence) if recorded_confidence else None
    )
    if outcome is Outcome.ESCALATED_LOW_CONFIDENCE:
        if confidence is not None:
            detail = f"confidence {confidence.score:.2f} is below the {confidence.floor:.2f} floor"
        else:
            detail = "no confidence check was recorded"
        note = f"{PREFIX}: draft withheld — {detail}. Escalated for human handling."
        return FinalOutput(
            outcome=outcome,
            summary=note,
            confidence=confidence.score if confidence else None,
            classification=classification,
            work_note=note,
            human_review_required=True,
            processing_state=PAUSED,
        )

    # Only SUGGESTED reaches here, and decide_outcome returns it only once the
    # confidence and draft sections are both present and passing.
    if confidence is None:
        raise ValueError("SUGGESTED outcome without a recorded confidence check")
    draft = Draft.model_validate(state["draft"])
    approval = bool(risk and risk.approval_required)
    note = (
        f"{PREFIX} drafted. Confidence {confidence.score:.2f}. "
        f"Source {'; '.join(draft.sources)}. Human review required before it is applied."
    )
    if approval and risk:
        note += " Approval required before any action: " + "; ".join(risk.reasons) + "."
    return FinalOutput(
        outcome=outcome,
        summary=note,
        suggestion=draft.rendered,
        confidence=confidence.score,
        classification=classification,
        work_note=note,
        human_review_required=True,
        processing_state=PAUSED,
        approval_required=approval,
    )


def act(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    outcome = decide_outcome(state)
    output = compose(state, outcome)
    if outcome is Outcome.SKIPPED_INELIGIBLE:
        if deps.settings.agent_write_back_enabled:
            incident = IncidentSnapshot.model_validate(state["incident"])
            _write_execution_log(
                state,
                deps,
                incident,
                output,
                status=ExecutionStatus.BLOCKED,
            )
        return {"output": output.model_dump(mode="json")}

    incident = IncidentSnapshot.model_validate(state["incident"])
    fields: dict[str, Any] = {
        "work_notes": output.work_note,
        "ai_processing_state": AIProcessingState(output.processing_state),
        "ai_classification": output.classification,
        "ai_confidence": output.confidence,
        "ai_suggestion": output.suggestion,
        "ai_model_name": deps.llm.model_name if state.get("classification") else None,
        "ai_agent_version": deps.settings.agent_version,
        "ai_human_review_required": output.human_review_required,
    }
    started = _parse_ts(state.get("started_at"))
    if started is not None:
        # The S1.1 model rejects an explicit None timestamp, so it is omitted instead.
        fields["ai_processing_start"] = started
    payload = IncidentUpdatePayload(**fields)
    actions = ["write_ai_fields", "write_work_note", "flag_human_review", "write_execution_log"]
    if not deps.settings.agent_write_back_enabled:
        output = output.model_copy(update={"actions": actions, "write_back": "dry_run"})
        return {"output": output.model_dump(mode="json")}
    try:
        deps.servicenow.write_ai_fields(incident.sys_id, payload)
    except HumanLockedError:
        output = FinalOutput(
            outcome=Outcome.SKIPPED_HUMAN_LOCK,
            summary="An analyst locked the incident before the write; nothing was written.",
            human_review_required=False,
            processing_state=AIProcessingState.PENDING.value,
        )
        _write_execution_log(
            state,
            deps,
            incident,
            output,
            status=ExecutionStatus.BLOCKED,
            error="Human lock prevented incident write-back.",
        )
        return {"output": output.model_dump(mode="json")}
    _write_execution_log(
        state,
        deps,
        incident,
        output,
        status=ExecutionStatus.AWAITING_APPROVAL,
    )
    output = output.model_copy(update={"actions": actions, "write_back": "written"})
    return {"output": output.model_dump(mode="json")}


def _write_execution_log(
    state: AgentState,
    deps: AgentDependencies,
    incident: IncidentSnapshot,
    output: FinalOutput,
    *,
    status: ExecutionStatus,
    error: str | None = None,
) -> None:
    action = (
        ExecutionAction.PROPOSE if output.outcome is Outcome.SUGGESTED else ExecutionAction.ESCALATE
    )
    payload = ExecutionLogCreatePayload(
        incident_sys_id=incident.sys_id,
        execution_id=state["execution_id"],
        agent=deps.settings.agent_version,
        action=action,
        status=status,
        result=output.summary,
        error=error,
    )
    deps.servicenow.write_execution_log(incident.sys_id, payload)


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
