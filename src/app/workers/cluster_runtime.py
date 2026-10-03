"""Bridge immutable webhook events, governed graph runs and durable cache waiters."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from agent.config import get_agent_settings
from agent.dependencies import get_agent_dependencies
from agent.errors import HumanLockedError
from agent.guardrails.input_screening import screen_text
from agent.policy import assess_risk, check_eligibility, snapshot_incident
from agent.state import ClassificationResult, EventPayload, RiskLevel
from agent.tools import ToolCallContext
from agent.triage import Situation, look_around
from app.models.execution_log import ExecutionAction, ExecutionLogCreatePayload, ExecutionStatus
from app.models.incident import AIProcessingState, IncidentUpdatePayload
from app.models.knowledge import Classification
from app.utils.async_bridge import run_blocking
from app.workers.db import WorkerRepo
from app.workers.producer import send_incident_event
from app.workers.retry_policy import TerminalError
from observability.redaction import redact_text

logger = structlog.getLogger(__name__)


def load_cluster_incident(payload: dict, execution_id: str, correlation_id: str) -> dict:
    # The webhook contains identifiers only. Never trust descriptions supplied
    # in a broker payload in place of the integration user's governed read.
    event = EventPayload.model_validate(payload)
    deps = get_agent_dependencies()
    raw = asyncio.run(
        deps.tools.invoke(
            "read_incident",
            context=ToolCallContext(execution_id=execution_id, correlation_id=correlation_id),
            arguments={"sys_id": event.sys_id},
        )
    )
    payload["prefetched_incident"] = raw
    incident = snapshot_incident(raw)
    if not incident.ai_enabled or incident.ai_human_lock is not False:
        return {}
    return incident.model_copy(
        update={
            "short_description": redact_text(incident.short_description),
            "description": redact_text(incident.description),
        }
    ).model_dump(mode="json")


def dispatch_cluster_waiters(repo: WorkerRepo) -> int:
    """Periodic at-least-once dispatch; a broker failure retains durable ready rows."""
    count = 0
    for waiter in repo.ready_cluster_waiters():
        send_incident_event(waiter["payload"], waiter["execution_id"], waiter["correlation_id"])
        count += 1
    return count


def cacheable_result(result: dict) -> bool:
    return bool(
        result.get("processing_state") == "complete"
        and result.get("write_back") == "written"
        and result.get("cache_draft")
    )


@dataclass(frozen=True, slots=True)
class ReuseVerdict:
    """Whether a follower may take a cluster's resolution without running the graph."""

    allowed: bool
    reason: str


def leader_caller_id(
    anchor_sys_id: str | None, execution_id: str, correlation_id: str
) -> str | None:
    """The caller of the cluster's leader incident (governed read), or None if unknown."""
    if not anchor_sys_id:
        return None
    try:
        deps = get_agent_dependencies()
        raw = asyncio.run(
            deps.tools.invoke(
                "read_incident",
                context=ToolCallContext(execution_id=execution_id, correlation_id=correlation_id),
                arguments={"sys_id": anchor_sys_id},
            )
        )
        return snapshot_incident(raw).caller_id
    except Exception:  # noqa: BLE001 - unknown leader caller means no reuse
        return None


def follower_reuse_verdict(
    raw_incident: Mapping[str, Any],
    solution: Mapping[str, Any],
    *,
    leader_caller: str | None = "",
    situation: Situation | None = None,
) -> ReuseVerdict:
    """Decide, with no LLM call, whether this follower may reuse the leader's resolution.

    Clustering only says two incidents *read* alike. It says nothing about the follower's
    own priority, service tier, eligibility or text, and the PRD's safety rules are per
    incident: no high-risk action without a recorded approval (NFR-05), no work on an
    ineligible or locked incident (FR-03), injection screening before anything is applied
    (FR-18). So the follower is held to the same deterministic gates the graph applies
    (``check_eligibility``, pattern screening, ``assess_risk``) on its own record, and only
    a clean, low-risk follower skips the graph. Everything else returns ``allowed=False``
    and runs the full governed path, which still reuses the cached draft.
    """
    settings = get_agent_settings()
    try:
        snapshot = snapshot_incident(raw_incident)
    except Exception:  # noqa: BLE001 - an unreadable record is never auto-resolved
        return ReuseVerdict(False, "follower incident could not be read")

    eligibility = check_eligibility(
        snapshot,
        event_number=snapshot.number,
        supported_categories=settings.agent_supported_categories,
    )
    if not eligibility.eligible:
        return ReuseVerdict(False, "ineligible: " + "; ".join(eligibility.reasons))

    if screen_text(f"{snapshot.short_description}\n{snapshot.description}").flagged:
        return ReuseVerdict(False, "input screening flagged the incident text")

    # ``leader_caller`` "" means the caller was not asked for (tests, stub backend);
    # None means the lookup failed, which never allows reuse.
    if leader_caller is None:
        return ReuseVerdict(False, "the leader incident's caller could not be read")
    if leader_caller and snapshot.caller_id == leader_caller:
        return ReuseVerdict(
            False,
            "the same caller reported this again: the earlier fix may not have worked",
        )

    # The graph's look-around (agent.triage) without its model call: a burst of similar
    # open reports is a likely outage for a person, and a caller reporting the same thing
    # again means the earlier fix did not work. Either way the full graph decides.
    if situation is not None:
        if not situation.looked:
            return ReuseVerdict(False, "could not look at related incidents")
        if situation.attack_signal:
            return ReuseVerdict(False, "the text reports a possible security incident")
        burst = len(situation.similar_open) + 1 >= settings.agent_outage_threshold
        if situation.outage_words or burst:
            return ReuseVerdict(False, "likely outage: a person coordinates one fix")
        if situation.repeat_from_caller:
            return ReuseVerdict(False, "the same caller reported this again recently")

    try:
        label = Classification(str(solution.get("classification") or ""))
    except ValueError:
        return ReuseVerdict(False, "the leader recorded no usable classification")
    risk = assess_risk(
        snapshot,
        ClassificationResult(
            label=label, rationale="reused from the resolved cluster", model_confidence=1.0
        ),
        risk_priorities=settings.agent_risk_priorities,
    )
    if risk.level is not RiskLevel.LOW or risk.approval_required:
        return ReuseVerdict(False, f"risk {risk.level.value}: " + "; ".join(risk.reasons))
    return ReuseVerdict(True, "follower passed eligibility, screening and risk gates")


def follower_situation(
    raw_incident: Mapping[str, Any], execution_id: str, correlation_id: str
) -> Situation:
    """Related incidents around a follower (governed reads, no model call)."""
    try:
        return look_around(
            get_agent_dependencies(),
            ToolCallContext(execution_id=execution_id, correlation_id=correlation_id),
            snapshot_incident(raw_incident),
        )
    except Exception:  # noqa: BLE001 - not knowing means the full graph decides
        return Situation()


def apply_follower_cluster_resolution(
    payload: dict[str, Any],
    solution: dict[str, Any],
    execution_id: str,
    correlation_id: str,
    raw_incident: Mapping[str, Any] | None = None,
) -> list[str]:
    """Apply the leader's resolution to a follower incident without executing LLMs (0x LLM)."""
    deps = get_agent_dependencies()
    if not deps.settings.agent_write_back_enabled:
        return []

    sys_id = payload.get("sys_id")
    if not sys_id:
        return []

    work_note = (
        solution.get("work_note")
        or solution.get("summary")
        or "AI Suggested Response applied from resolved incident cluster."
    )
    resolution = (
        solution.get("resolution")
        or solution.get("suggestion")
        or (solution.get("cache_draft") or {}).get("rendered")
    )
    update_fields: dict[str, Any] = {
        "work_notes": work_note,
        "ai_processing_state": AIProcessingState.COMPLETE,
        "ai_agent_version": deps.settings.agent_version,
        "ai_human_review_required": False,
        "ai_processing_end": datetime.now(UTC),
    }
    if solution.get("classification"):
        update_fields["ai_classification"] = solution["classification"]
    if solution.get("confidence") is not None:
        update_fields["ai_confidence"] = solution["confidence"]
    if solution.get("suggestion") or resolution:
        update_fields["ai_suggestion"] = solution.get("suggestion") or resolution
    if resolution:
        update_fields["ai_resolution"] = resolution
    if solution.get("model_name"):
        update_fields["ai_model_name"] = solution["model_name"]

    update_payload = IncidentUpdatePayload(**update_fields)
    tool_context = ToolCallContext(execution_id=execution_id, correlation_id=correlation_id)
    with deps.tracer.span(
        "cluster_cache.resolve_follower",
        as_type="span",
        metadata={
            "execution_id": execution_id,
            "sys_id": sys_id,
            "cluster_role": "follower",
            "llm_calls": 0,
        },
    ):
        try:
            run_blocking(
                deps.tools.invoke(
                    "write_ai_fields",
                    context=tool_context,
                    arguments={"sys_id": sys_id, "payload": update_payload},
                )
            )
        except HumanLockedError as exc:
            # Same translation the graph applies: an analyst took over, so this run ends
            # without a write and without a retry.
            raise TerminalError(str(exc)) from exc
        # Any other failure propagates. Swallowing it here marked the follower
        # ``succeeded`` and its cluster membership applied with nothing written to
        # ServiceNow.

        steps: list[str] = []
        if raw_incident is not None and resolution:
            # The follower passed its own gates (follower_reuse_verdict), so it is
            # worked exactly like a low-risk incident the graph resolved itself.
            from agent.nodes.act import fulfil_applied_fix

            confidence = solution.get("confidence")
            steps = fulfil_applied_fix(
                deps,
                snapshot_incident(raw_incident),
                resolution=str(resolution),
                confidence=float(confidence) if isinstance(confidence, int | float) else None,
                context=tool_context,
                classification=str(solution.get("classification") or "") or None,
            )

        log_payload = ExecutionLogCreatePayload(
            incident_sys_id=sys_id,
            execution_id=execution_id,
            agent=deps.settings.agent_version,
            action=ExecutionAction.PROPOSE,
            status=ExecutionStatus.SUCCEEDED,
            result=str(solution.get("summary") or "Resolved via cluster cache"),
        )
        try:
            run_blocking(
                deps.tools.invoke(
                    "write_execution_log",
                    context=tool_context,
                    arguments={"sys_id": sys_id, "payload": log_payload},
                )
            )
        except Exception as exc:  # noqa: BLE001 - the incident write already landed
            logger.warning(
                "follower_cluster_execution_log_failed",
                execution_id=execution_id,
                sys_id=sys_id,
                error=redact_text(str(exc))[:500],
            )
    return steps


def notify_caller_waiting(
    payload: Mapping[str, Any], result: Mapping[str, Any], execution_id: str, correlation_id: str
) -> str:
    """Route a paused ticket to its team and tell the caller who has it.

    A paused run waits for an engineer, so the ticket must reach a team's queue (a ticket
    with no group was seen live: nobody would have picked it up) and the caller must not
    be left in silence. Runs once in the worker after the graph paused, outside the paused
    node, so a resume never repeats it. Best effort: any failure is logged and skipped.
    """
    from agent.conversation import AGENT_NAME
    from agent.nodes.act import _TEAM_NAMES, route

    sys_id = str(payload.get("sys_id") or "")
    if not sys_id:
        return "skipped:no_incident"
    done: list[str] = []
    try:
        deps = get_agent_dependencies()
        context = ToolCallContext(execution_id=execution_id, correlation_id=correlation_id)
        raw = asyncio.run(
            deps.tools.invoke("read_incident", context=context, arguments={"sys_id": sys_id})
        )
        incident = snapshot_incident(raw)
        brief = result.get("interrupt_payload") or {}
        category, group = route(deps, incident, brief.get("classification"))
        if group and incident.state == "1":
            routed = asyncio.run(
                deps.tools.invoke(
                    "assign_incident",
                    context=context,
                    arguments={
                        "sys_id": sys_id,
                        "assignment_group": group,
                        "work_note": f"{AGENT_NAME} routed it to the {category} group: it "
                        "waits for an engineer's decision (see the BARQ AI card).",
                    },
                )
            )
            done.append(f"assign_incident:{routed}")
        caller = incident.caller_id
        if not caller or caller in deps.settings.agent_service_account_ids:
            return ";".join([*done, "update_caller:skipped_no_caller"])
        team = _TEAM_NAMES.get(category, f"the {category} team") if group else "an engineer"
        who = "an engineer" if team == "an engineer" else f"an engineer of {team}"
        why = (
            f"You reported a similar problem recently, so {who} will check the fix before it "
            "is applied."
            if "repeat from the same caller" in str(brief.get("summary") or "")
            else f"{who[0].upper()}{who[1:]} needs to check it before anything is applied."
        )
        message = (
            f"Hello, this is {AGENT_NAME}. I looked into your incident. {why} You will hear "
            "from them here, and you can add details at any time."
        )
        outcome = asyncio.run(
            deps.tools.invoke(
                "update_caller", context=context, arguments={"sys_id": sys_id, "message": message}
            )
        )
        return ";".join([*done, f"update_caller:{outcome}"])
    except Exception as exc:  # noqa: BLE001 - telling the caller must not break the run
        logger.warning("notify_caller_waiting_failed", error_type=type(exc).__name__)
        return ";".join([*done, "failed"])
