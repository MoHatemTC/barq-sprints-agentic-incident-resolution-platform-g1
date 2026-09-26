"""``act`` — the single terminal node: write the suggestion, or escalate.

Every path through the graph ends here, so there is exactly one place that writes
to ServiceNow and exactly one record of the outcome. The writes are the manual's
§11.6 permitted actions only — AI fields, an internal work note and the human
review flag — sent as one PATCH through ToolRegistry to IncidentGateway. ToolRegistry
owns allowlisting and permission enforcement. Nothing resolves, closes, reassigns or
contacts the requester: those actions do not exist.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from langgraph.types import interrupt

from agent.approval_brief import render_brief
from agent.dependencies import AgentDependencies
from agent.errors import HumanLockedError
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
from agent.tools import ToolCallContext
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

#: FR-17 interrupt points. ``ESCALATED_NO_EVIDENCE`` is intentionally absent —
#: see ``docs/sprint3_hitl_design.md``.
INTERRUPT_OUTCOMES = frozenset(
    {
        Outcome.ESCALATED_HIGH_RISK,
        Outcome.ESCALATED_BLOCKED,
        Outcome.ESCALATED_LOW_CONFIDENCE,
    }
)


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
        detail = f"the {gate.gate} check: {gate.reason or 'failed'}" if gate else "a missing check"
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


def interrupt_payload(state: AgentState, output: FinalOutput, outcome: Outcome) -> dict[str, Any]:
    """NFR-07 audit payload persisted next to the checkpoint at interrupt time."""
    incident = state.get("incident") or {}
    return {
        "outcome": outcome.value,
        "gate": outcome.value,
        "summary": output.summary,
        "work_note": output.work_note,
        "planned_action": output.work_note,
        "suggestion": output.suggestion,
        "confidence": output.confidence,
        "classification": output.classification,
        "approval_required": output.approval_required,
        "incident": {
            "sys_id": incident.get("sys_id"),
            "number": incident.get("number"),
            "priority": incident.get("priority"),
            "impact": incident.get("impact"),
            "urgency": incident.get("urgency"),
            "service": incident.get("service"),
            "short_description": incident.get("short_description"),
            "category": incident.get("category"),
        },
        "risk": state.get("risk"),
        "retrieval": _slim_retrieval(state.get("retrieval")),
        "draft": state.get("draft"),
        "verification": state.get("verification"),
        "safety": state.get("safety"),
        "confidence_gate": state.get("confidence"),
        "execution_id": state.get("execution_id"),
        "correlation_id": state.get("correlation_id"),
        "lifecycle": "interrupt",
    }


def _slim_retrieval(section: Any) -> dict[str, Any] | None:
    if not isinstance(section, dict):
        return None
    hits = section.get("hits") or []
    return {k: v for k, v in section.items() if k != "hits"} | {
        "hit_count": len(hits),
        "top_articles": [
            {
                "article_number": h.get("article_number"),
                "version": h.get("version"),
                "section": h.get("section"),
                "relevance": h.get("relevance"),
            }
            for h in hits[:5]
            if isinstance(h, dict)
        ],
    }


def _request_human_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Pause inside a compiled graph; unit tests that call ``act`` directly write."""
    try:
        value = interrupt(payload)
    except RuntimeError:
        return {"decision": "approved", "decided_by": "direct-node-call", "source": "no_graph"}
    if isinstance(value, dict):
        return value
    return {"decision": str(value), "decided_by": "operator"}


def _apply_human_decision(output: FinalOutput, decision: dict[str, Any]) -> FinalOutput:
    verdict = str(decision.get("decision") or "approved").lower()
    if verdict == "approved":
        return output
    who = str(decision.get("decided_by") or "operator")
    why = str(decision.get("reason") or verdict)
    note = (
        f"{PREFIX}: human {verdict} by {who}. {why}. "
        f"Original outcome {output.outcome.value}. No automated action applied."
    )
    return output.model_copy(
        update={
            "summary": note,
            "work_note": note,
            "suggestion": None,
            "approval_required": True,
        }
    )


def act(state: AgentState, deps: AgentDependencies) -> dict[str, Any]:
    execution_id = str(state.get("execution_id") or "")
    receipt = deps.audit.get_receipt(execution_id) if execution_id else None
    if receipt and receipt.get("output"):
        phase = receipt.get("phase")
        if phase in (None, "logged", "written"):
            # "logged" is terminal: both ServiceNow calls landed and only the final
            # receipt save was lost, so replaying it would duplicate the execution
            # log. A receipt saved without a phase predates the boundary markers and
            # can only have been written after the write completed.
            return {"output": receipt["output"]}
        # The previous attempt died inside the write boundary. Resume at the boundary
        # with the output it was writing rather than recomputing the decision, which
        # would interrupt the same human again.
        return _perform_write(
            state,
            deps,
            FinalOutput.model_validate(receipt["output"]),
            resume_phase=phase,
        )

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

    if outcome in INTERRUPT_OUTCOMES:
        payload = interrupt_payload(state, output, outcome)
        payload["brief"] = render_brief(payload, deps)
        deps.audit.save_interrupt(execution_id, payload)
        decision = _request_human_decision(payload)
        output = _apply_human_decision(output, decision)

    return _perform_write(state, deps, output)


def _perform_write(
    state: AgentState,
    deps: AgentDependencies,
    output: FinalOutput,
    *,
    resume_phase: str | None = None,
) -> dict[str, Any]:
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
    execution_id = str(state["execution_id"])
    lifecycle = "interrupt_resume" if deps.audit.get_interrupt(execution_id) else "direct"
    receipt_base: dict[str, Any] = {
        "lifecycle": lifecycle,
        "incident_sys_id": incident.sys_id,
    }
    in_flight = output.model_dump(mode="json")

    # Resuming after a crash inside this boundary: the PATCH is skipped only when
    # ServiceNow itself says it landed, so a kill between the write and the receipt
    # cannot append the work note twice.
    #
    # ``logged`` is a further terminal phase, saved once the execution-log row has
    # landed. Without it a kill between that write and the final receipt save
    # restarted the log write, because the receipt still said ``fields_written``.
    #
    # Known residual, deliberately not closed here: a kill in the window *after*
    # the log row lands but *before* the receipt reaches ``logged`` still replays
    # the log. Closing it needs a read-back probe for the execution-log table the
    # way _write_already_landed probes the incident, and execution_id is not unique
    # on that table, so existence cannot distinguish this attempt from an earlier
    # one. The duplicate is an extra audit row; the authoritative record of the run
    # is executions + workflow_state. See docs/sprint3_recovery_design.md.
    write_fields = True
    if resume_phase in ("fields_written", "logged"):
        write_fields = False
    elif resume_phase is not None:
        write_fields = not _write_already_landed(state, deps, incident, payload)

    if write_fields:
        # The intent receipt is saved before the call that can die, so a restarted
        # worker can always tell "in flight" from "never started".
        deps.audit.save_receipt(
            execution_id, {**receipt_base, "phase": "writing", "output": in_flight}
        )
        # Act-node writes currently run on synchronous Celery/graph worker threads
        # without a running event loop, so asyncio.run() bridges to ToolRegistry. If
        # execution moves to an async worker/task context, replace this bridge rather
        # than nest asyncio.run().
        try:
            asyncio.run(
                deps.tools.invoke(
                    "write_ai_fields",
                    context=_tool_context(state),
                    arguments={"sys_id": incident.sys_id, "payload": payload},
                )
            )
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
        deps.audit.save_receipt(
            execution_id,
            {**receipt_base, "phase": "fields_written", "output": in_flight},
        )
    _write_execution_log(
        state,
        deps,
        incident,
        output,
        status=ExecutionStatus.AWAITING_APPROVAL,
    )
    final = output.model_copy(update={"actions": actions, "write_back": "written"})
    dumped = final.model_dump(mode="json")
    # Both terminal phases carry the *final* payload, not the in-flight one, so a
    # restart from either returns the run as it actually completed.
    deps.audit.save_receipt(execution_id, {**receipt_base, "phase": "logged", "output": dumped})
    deps.audit.save_receipt(execution_id, {**receipt_base, "phase": "written", "output": dumped})
    return {"output": dumped}


#: Fields whose ServiceNow-side values describe this attempt's PATCH, used to prove a
#: write landed when its receipt never did.
_LANDED_PROBE_FIELDS = (
    "work_notes",
    "ai_classification",
    "ai_suggestion",
    "ai_agent_version",
    "ai_model_name",
)


def _write_already_landed(
    state: AgentState,
    deps: AgentDependencies,
    incident: IncidentSnapshot,
    payload: IncidentUpdatePayload,
) -> bool:
    """Ask ServiceNow whether the PATCH this attempt was about to send is already there.

    Recovery only: called when a receipt says a write was in flight. ``False`` (write
    again) is the safe answer on any read failure -- at worst the work note is appended
    twice, which is the pre-existing failure, rather than a write silently skipped.
    """
    sent = payload.model_dump(exclude_none=True)
    try:
        current = asyncio.run(
            deps.tools.invoke(
                "read_incident",
                context=_tool_context(state),
                arguments={"sys_id": incident.sys_id},
            )
        )
    except Exception:  # noqa: BLE001 — a failed probe must never block the retry
        return False
    if not isinstance(current, dict):
        return False

    expected_start = sent.get("ai_processing_start")
    if expected_start is not None:
        # This run's own processing-start timestamp: it is written by the same PATCH,
        # so ServiceNow handing it back proves *this* attempt landed, not an earlier
        # run that left similar fields behind.
        started = _as_utc(expected_start)
        return started is not None and started == _as_utc(current.get("ai_processing_start"))

    probes = {k: str(v) for k, v in sent.items() if k in _LANDED_PROBE_FIELDS and v is not None}
    if not probes:
        return False
    return all(str(current.get(k)) == v for k, v in probes.items())


def _as_utc(value: Any) -> datetime | None:
    """Normalise a written or read-back timestamp to UTC.

    ``to_table_api_body`` formats datetimes as UTC text without an offset, so a value
    read back from ServiceNow arrives naive; it is UTC by construction.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


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
    asyncio.run(
        deps.tools.invoke(
            "write_execution_log",
            context=_tool_context(state),
            arguments={"sys_id": incident.sys_id, "payload": payload},
        )
    )


def _tool_context(state: AgentState) -> ToolCallContext:
    return ToolCallContext(
        execution_id=state["execution_id"],
        correlation_id=state.get("correlation_id"),
    )


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
