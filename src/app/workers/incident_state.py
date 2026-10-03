"""ServiceNow incident state at terminal worker failure and DLQ replay."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import exists, select

from app.clients.servicenow_client import ServiceNowClient
from app.core.config import Settings
from app.db.models import Approval, Execution, ExecutionNodeState
from app.exceptions.app_errors import ConflictError
from app.models.incident import AIProcessingState, IncidentUpdatePayload
from observability.redaction import redact_text

logger = structlog.getLogger(__name__)

_SYS_ID = re.compile(r"[0-9a-f]{32}\Z", re.IGNORECASE)
_PREFIX = "x_2215032_ai_inc_0_ai_"
# Same node name as ``agent.audit_store.HITL_NODE``; importing it would pull the
# agent package into every worker hook.
_HITL_NODE = "hitl.interrupt"

PausedRunLookup = Callable[[str], bool]


def incident_sys_id(payload: dict[str, Any]) -> str | None:
    """Ignore synthetic events used by the stub worker integration suite."""
    value = str(payload.get("sys_id") or "")
    return value if _SYS_ID.fullmatch(value) else None


async def mark_incident_failed(
    settings: Settings,
    payload: dict[str, Any],
    execution_id: str,
    exc: Exception,
    attempt: int,
    has_decidable_pause: PausedRunLookup | None = None,
) -> bool:
    """Write a final failure once, without overwriting a human or completed run.

    ``awaiting_approval`` is normally left alone: an approver can still decide
    it. But a run can fail after ServiceNow was told it is waiting and before a
    pause was stored (INC0010252: the interrupt insert failed), leaving an
    incident nobody can ever decide. ``has_decidable_pause(sys_id)`` answers
    whether any run for the incident can still be decided; only a definite
    ``False`` releases the incident. Without a lookup, or if it fails, the
    incident is left as it is.
    """
    sys_id = incident_sys_id(payload)
    if sys_id is None:
        return False
    async with ServiceNowClient(settings) as client:
        incident = await client.get_incident(sys_id)
        if incident.number != payload.get("number") or not incident.ai_enabled:
            logger.warning("failure_write_skipped_ineligible_incident", execution_id=execution_id)
            return False
        if incident.ai_human_lock is not False:
            logger.warning("failure_write_skipped_human_lock", execution_id=execution_id)
            return False
        stuck_waiting = False
        if incident.ai_processing_state is AIProcessingState.AWAITING_APPROVAL:
            stuck_waiting = _no_decidable_pause(has_decidable_pause, sys_id, execution_id)
            if not stuck_waiting:
                logger.warning(
                    "failure_write_skipped_finished_incident",
                    execution_id=execution_id,
                    state=incident.ai_processing_state.value,
                )
                return False
        if incident.ai_processing_state is AIProcessingState.COMPLETE:
            logger.warning(
                "failure_write_skipped_finished_incident",
                execution_id=execution_id,
                state=incident.ai_processing_state.value,
            )
            return False
        if incident.ai_processing_state is AIProcessingState.FAILED:
            # Celery can call on_failure again after a redelivery. Never overwrite
            # a reason already visible to the operator with a second account.
            return False
        ended_at = datetime.now(UTC)
        if incident.ai_processing_start and ended_at < incident.ai_processing_start:
            ended_at = incident.ai_processing_start
        detail = redact_text(str(exc)).replace("\n", " ").strip()
        released = (
            " ServiceNow showed it awaiting approval, but no paused run exists to "
            "decide, so it is released for an engineer."
            if stuck_waiting
            else ""
        )
        reason = (
            f"Execution {execution_id} failed after {attempt} attempt(s): "
            f"{type(exc).__name__}: {detail or 'no detail available'}.{released}"
        )[:4000]
        await client.update_incident(
            sys_id,
            IncidentUpdatePayload(
                ai_processing_state=AIProcessingState.FAILED,
                ai_failure_reason=reason,
                ai_processing_end=ended_at,
            ),
        )
        logger.warning(
            "incident_marked_failed",
            execution_id=execution_id,
            incident_sys_id=sys_id,
            released_stuck_approval=stuck_waiting,
        )
        return True


def _no_decidable_pause(
    lookup: PausedRunLookup | None, sys_id: str, execution_id: str
) -> bool:
    """True only when the lookup positively says nothing can be decided."""
    if lookup is None:
        return False
    try:
        return lookup(sys_id) is False
    except Exception as exc:  # noqa: BLE001 — unknown means leave the incident alone
        logger.warning(
            "failure_write_pause_lookup_failed",
            execution_id=execution_id,
            incident_sys_id=sys_id,
            error=str(exc),
        )
        return False


def postgres_pause_lookup(settings: Settings) -> PausedRunLookup:
    """Ask PostgreSQL whether any run for an incident can still be decided.

    Decidable means what ``POST /approvals/{id}/decide`` accepts: the execution
    is ``awaiting_approval``, its pause was stored, and nobody has decided it.
    """

    def lookup(incident_sys_id: str) -> bool:
        from app.workers.sync_engine import (
            build_sync_database_url,
            create_sync_engine,
            create_sync_session_factory,
            sync_session_scope,
        )

        engine = create_sync_engine(build_sync_database_url(settings))
        try:
            factory = create_sync_session_factory(engine)
            with sync_session_scope(factory) as session:
                paused = select(Execution.execution_id).where(
                    Execution.incident_sys_id == incident_sys_id,
                    Execution.status == "awaiting_approval",
                    exists().where(
                        ExecutionNodeState.execution_id == Execution.execution_id,
                        ExecutionNodeState.node_name == _HITL_NODE,
                    ),
                    ~exists().where(Approval.execution_id == Execution.execution_id),
                )
                return session.scalar(paused.limit(1)) is not None
        finally:
            engine.dispose()

    return lookup


async def prepare_failed_incident_for_replay(settings: Settings, payload: dict[str, Any]) -> None:
    """Return a failed incident to pending before its original event is requeued.

    Keep the state change strict: an unreachable or locked incident must stop
    replay while the PostgreSQL row and DLQ record are still parked.
    """
    sys_id = incident_sys_id(payload)
    if sys_id is None:
        return
    async with ServiceNowClient(settings) as client:
        incident = await client.get_incident(sys_id)
        if incident.number != payload.get("number") or not incident.ai_enabled:
            raise ConflictError("Incident no longer matches this enabled AI event; replay refused.")
        if incident.ai_human_lock is not False:
            raise ConflictError("Incident has a Human Lock; replay requires human intervention.")
        if incident.ai_processing_state is AIProcessingState.PENDING:
            if (
                incident.ai_failure_reason
                or incident.ai_processing_start is not None
                or incident.ai_processing_end is not None
            ):
                raise ConflictError(
                    "Incident is pending but still carries processing or failure fields; "
                    "replay requires a clean pending incident."
                )
            return
        if incident.ai_processing_state is not AIProcessingState.FAILED:
            raise ConflictError(
                f"Incident AI state is '{incident.ai_processing_state.value}', not 'failed' "
                "or 'pending'; replay would skip or overwrite an active decision."
            )
        body = {
            f"{_PREFIX}processing_state": AIProcessingState.PENDING.value,
            f"{_PREFIX}failure_reason": "",
            f"{_PREFIX}processing_start": "",
            f"{_PREFIX}processing_end": "",
        }
        result = await client._request("PATCH", f"/api/now/table/incident/{sys_id}", json=body)
        # ServiceNow may represent cleared fields as either empty strings or null.
        if result.get(f"{_PREFIX}processing_state") != AIProcessingState.PENDING.value:
            raise ConflictError("ServiceNow did not persist the pending state for replay.")
        fresh = await client.get_incident(sys_id)
        if (
            fresh.ai_processing_state is not AIProcessingState.PENDING
            or fresh.ai_failure_reason
            or fresh.ai_processing_start is not None
            or fresh.ai_processing_end is not None
        ):
            raise ConflictError("ServiceNow did not clear the prior failure before replay.")
        logger.info("failed_incident_reset_for_replay", incident_sys_id=sys_id)


def write_final_failure_best_effort(
    settings: Settings,
    payload: dict[str, Any],
    execution_id: str,
    exc: Exception,
    attempt: int,
) -> None:
    """Keep the DLQ hook alive even when ServiceNow itself is unavailable."""
    try:
        asyncio.run(
            mark_incident_failed(
                settings,
                payload,
                execution_id,
                exc,
                attempt,
                has_decidable_pause=postgres_pause_lookup(settings),
            )
        )
    except Exception as write_exc:  # noqa: BLE001 — failure hook must preserve DLQ
        logger.exception(
            "incident_failure_write_failed",
            execution_id=execution_id,
            incident_sys_id=incident_sys_id(payload),
            error=str(write_exc),
        )


def reset_failed_incident_for_replay(settings: Settings, payload: dict[str, Any]) -> None:
    """Synchronous wrapper for the API's worker-thread replay route and CLI."""
    asyncio.run(prepare_failed_incident_for_replay(settings, payload))


async def prepare_servicenow_retry(settings: Settings, payload: dict[str, Any]) -> bool:
    """Accept one of ServiceNow's two failed-transition retry events.

    S1.3 emits a fresh event after ``failed`` and increments retry_count to 1
    or 2. The graph itself admits only ``pending``, so the worker must clear the
    visible failure before invoking it. Ordinary events and Celery retries are
    left alone.
    """
    sys_id = incident_sys_id(payload)
    if sys_id is None or payload.get("event_type") != "incident.updated":
        return False
    async with ServiceNowClient(settings) as client:
        incident = await client.get_incident(sys_id)
    if incident.ai_processing_state is not AIProcessingState.FAILED:
        return False
    if incident.ai_retry_count not in {1, 2}:
        return False
    await prepare_failed_incident_for_replay(settings, payload)
    return True


def prepare_servicenow_retry_sync(settings: Settings, payload: dict[str, Any]) -> bool:
    """Worker-facing bridge for the async ServiceNow client."""
    return asyncio.run(prepare_servicenow_retry(settings, payload))


__all__ = [
    "incident_sys_id",
    "mark_incident_failed",
    "postgres_pause_lookup",
    "prepare_failed_incident_for_replay",
    "prepare_servicenow_retry",
    "prepare_servicenow_retry_sync",
    "reset_failed_incident_for_replay",
    "write_final_failure_best_effort",
]
