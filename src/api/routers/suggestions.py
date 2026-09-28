"""Review and acceptance of a completed AI draft (post-hoc HITL).

A straight-through run — one that found its evidence, drafted, passed every gate and
wrote its suggestion to the incident — has no paused thread. ``act`` deliberately
leaves the incident at ``ai_processing_state = awaiting_approval`` because a human
still has to accept the draft before it counts as applied, but there was no
interrupt to approve: ``/api/v1/approvals/pending/{id}`` returns 404 and
``/api/v1/approvals/{id}/decide`` returns 409 for such a run. The incident therefore
sat on "Awaiting Approval" with no way to ever leave it, and ``ai_resolution`` and
``ai_processing_end`` were never written by anything, so ``complete`` was
unreachable (dev407364, verified 2026-09-28 across 12 incidents).

This module closes that: it reviews a completed draft and, on acceptance, writes the
one payload that the S1.1 field model has always required for ``complete`` — a
non-empty ``ai_resolution`` and ``ai_processing_end`` in the same write
(``app/models/incident.py`` ``_enforce_validation_rules``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_role, verify_bearer_token
from api.schemas.approvals import fold_solution_into_evidence
from api.schemas.errors import ErrorResponse
from api.schemas.suggestions import (
    SuggestionDecisionRequest,
    SuggestionDecisionResponse,
    SuggestionReviewResponse,
)
from app.api.dependencies import get_app_settings, get_db_session
from app.clients.servicenow_client import ServiceNowClient
from app.core.config import Settings, get_settings
from app.db.models import Approval, Execution
from app.exceptions.app_errors import (
    ConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.exceptions.servicenow import ServiceNowError
from app.models.incident import AIProcessingState, IncidentUpdatePayload

logger = structlog.getLogger("api.suggestions")

router = APIRouter(
    prefix="/api/v1/suggestions",
    tags=["Suggestion Review"],
    dependencies=[Depends(verify_bearer_token)],
)

#: Only a run that finished by drafting something is reviewable here. The value is
#: ``executions.termination_cause``, written by the act node's SUGGESTED branch.
_DRAFTED = "suggested"


@router.get(
    "/{execution_id}",
    response_model=SuggestionReviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Review a completed AI draft awaiting acceptance",
    description=(
        "Return the suggestion a completed run left on the incident, together with "
        "its confidence, classification and citation sources, for a human to accept "
        "or reject. Distinct from /api/v1/approvals/pending/{execution_id}, which "
        "serves a *paused* thread: a drafted run has no interrupt and 404s there."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid operator token."},
        404: {"model": ErrorResponse, "description": "No such execution, or it did not draft."},
        503: {"model": ErrorResponse, "description": "ServiceNow or database unavailable."},
    },
)
async def review_suggestion(
    execution_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> SuggestionReviewResponse:
    """The draft as it stands on the incident, plus whether it is still decidable."""
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise ResourceNotFoundError(f"No execution '{execution_id}' found")
    if execution.termination_cause != _DRAFTED:
        raise ResourceNotFoundError(
            f"Execution '{execution_id}' finished as "
            f"'{execution.termination_cause}', not '{_DRAFTED}'; it has no draft to "
            "review. A paused escalation is served by /api/v1/approvals/pending."
        )

    incident = await _get_incident(settings, execution.incident_sys_id)
    decided = (
        await db.execute(select(Approval).where(Approval.execution_id == execution_id).limit(1))
    ).scalar_one_or_none()

    return SuggestionReviewResponse(
        execution_id=execution.execution_id,
        incident_sys_id=execution.incident_sys_id,
        incident_number=incident.number,
        suggestion=incident.ai_suggestion or "",
        confidence=incident.ai_confidence,
        classification=incident.ai_classification,
        processing_state=incident.ai_processing_state,
        processing_start=incident.ai_processing_start,
        human_review_required=incident.ai_human_review_required,
        decided=decided.decision if decided is not None else None,
    )


@router.post(
    "/{execution_id}/decide",
    response_model=SuggestionDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Accept or reject a completed AI draft",
    description=(
        "Accepting writes the acceptance to the incident: ai_resolution, "
        "ai_processing_state=complete and ai_processing_end in the single write the "
        "field model requires, which is the only path to 'complete'. Rejecting "
        "records the decision for audit and leaves the incident for the human to "
        "close. The decision is immutable: a second one is 409. Who decided comes "
        "from the operator token, never the body."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid operator token."},
        403: {"model": ErrorResponse, "description": "Token lacks the approver role."},
        404: {"model": ErrorResponse, "description": "No execution, or it did not draft."},
        409: {"model": ErrorResponse, "description": "Already decided."},
        503: {"model": ErrorResponse, "description": "ServiceNow or database unavailable."},
    },
)
async def decide_suggestion(
    execution_id: UUID,
    payload: SuggestionDecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    claims: Annotated[dict[str, Any], Depends(verify_bearer_token)],
    _approver: Annotated[str, Depends(require_role("approver"))],
) -> SuggestionDecisionResponse:
    """Accept or reject a draft; on acceptance, complete the incident.

    The ServiceNow write happens before the audit row is committed, for the same
    reason ``decide_approval`` resumes before it records: a decision that could not
    be applied has to stay retryable, and the approvals table is immutable by
    trigger, so recording first would make the retry hit the 409 instead.
    """
    decided_by = str(claims.get("sub") or "")
    execution = await db.get(Execution, execution_id)
    if execution is None:
        raise ResourceNotFoundError(f"No execution '{execution_id}' found")
    if execution.termination_cause != _DRAFTED:
        raise ConflictError(
            f"Execution '{execution_id}' finished as '{execution.termination_cause}', "
            f"not '{_DRAFTED}'; there is no draft to decide. A paused escalation is "
            "decided through /api/v1/approvals/{id}/decide."
        )

    try:
        existing = (
            await db.execute(select(Approval).where(Approval.execution_id == execution_id).limit(1))
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.exception("suggestion_decision_read_failed", execution_id=str(execution_id))
        raise ServiceUnavailableError("Database unavailable to record the decision.") from exc
    if existing is not None:
        raise ConflictError(
            f"Execution '{execution_id}' has already been decided "
            f"('{existing.decision}') and decisions are immutable."
        )

    incident = await _get_incident(settings, execution.incident_sys_id)
    now = datetime.now(UTC)
    written: dict[str, Any] = {}
    # What the operator actually did, if they said. This is the S3.5 input: the
    # human's own words are what gets composed into a knowledge article, and they
    # also make the incident genuinely resolved.
    resolution = (payload.solution or "").strip()

    if resolution or (payload.decision == "approved" and (incident.ai_suggestion or "").strip()):
        # Accepted with no operator solution: the drafted suggestion is applied as
        # written. Either way ``ai_resolution`` ends up non-empty, which is what the
        # field model demands before ``complete`` is legal.
        applied = resolution or (incident.ai_suggestion or "").strip()
        update = IncidentUpdatePayload(
            ai_processing_state=AIProcessingState.COMPLETE,
            ai_resolution=applied,
            ai_processing_end=now,
            ai_human_review_required=False,
            work_notes=(
                f"AI Suggested Response accepted by {decided_by}. "
                f"Confidence {incident.ai_confidence}. Resolution applied."
            ),
        )
        try:
            await _update_incident(settings, execution.incident_sys_id, update)
        except ServiceNowError as exc:
            logger.exception(
                "suggestion_acceptance_write_failed",
                execution_id=str(execution_id),
                error=str(exc),
            )
            raise ServiceUnavailableError(
                f"ServiceNow refused the acceptance write: {exc}"
            ) from exc
        written = {
            "ai_resolution_written": True,
            "ai_processing_end": now.isoformat(),
            "ai_processing_state": AIProcessingState.COMPLETE.value,
        }
        logger.info(
            "suggestion_accepted",
            execution_id=str(execution_id),
            incident=execution.incident_sys_id,
            decided_by=decided_by,
            confidence=incident.ai_confidence,
        )
    elif payload.decision == "approved":
        # Accepted with nothing to apply: no operator solution and no drafted
        # suggestion. Refuse rather than write a blank resolution — the field model
        # would reject it anyway, and claiming one would be a fabrication.
        raise ConflictError(
            "Cannot accept: the incident carries no AI suggestion to accept and the "
            "request supplied no solution. Nothing would be written."
        )
    else:
        # Declined with no resolution offered. Record the decision and stop. The
        # incident is deliberately not marked complete — there is no resolution to
        # record, and claiming one would be a fabrication. The human closes it in
        # ServiceNow, which is the correct outcome for a rejected suggestion.
        logger.info(
            "suggestion_rejected",
            execution_id=str(execution_id),
            incident=execution.incident_sys_id,
            decided_by=decided_by,
        )

    try:
        approval = Approval(
            id=uuid4(),
            execution_id=execution.execution_id,
            decision=payload.decision,
            decided_by=decided_by,
            reason=payload.reason,
            evidence=fold_solution_into_evidence(payload.evidence, payload.solution),
            decided_at=now,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)
    except IntegrityError as exc:
        raise ConflictError(
            f"Execution '{execution_id}' has already been decided by a concurrent request."
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception(
            "suggestion_decision_record_failed", execution_id=str(execution_id), error=str(exc)
        )
        raise ServiceUnavailableError("Database unavailable to record the decision.") from exc

    return SuggestionDecisionResponse(
        approval_id=approval.id,
        execution_id=approval.execution_id,
        incident_sys_id=execution.incident_sys_id,
        decision=approval.decision,
        decided_by=approval.decided_by,
        decided_at=approval.decided_at,
        reason=approval.reason,
        **written,
    )


async def _get_incident(settings: Settings, sys_id: str) -> Any:
    client = ServiceNowClient(settings)
    try:
        return await client.get_incident(sys_id)
    except ServiceNowError as exc:
        logger.exception("suggestion_incident_read_failed", incident=sys_id, error=str(exc))
        raise ServiceUnavailableError(
            f"ServiceNow could not return incident {sys_id}: {exc}"
        ) from exc
    finally:
        await client.aclose()


async def _update_incident(settings: Settings, sys_id: str, payload: IncidentUpdatePayload) -> None:
    client = ServiceNowClient(settings)
    try:
        await client.update_incident(sys_id, payload)
    finally:
        await client.aclose()


__all__ = ["router", "get_settings"]
