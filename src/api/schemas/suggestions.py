"""Schemas for reviewing and accepting a completed AI draft."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.incident import AIProcessingState


class SuggestionReviewResponse(BaseModel):
    """A drafted suggestion as it stands on the incident, for a human to judge."""

    execution_id: UUID
    incident_sys_id: str
    incident_number: str | None = None
    suggestion: str = Field(
        default="",
        description="The drafted resolution currently on the incident's AI Suggestion field.",
    )
    confidence: float | None = Field(
        default=None, description="Confidence the confidence gate recorded, 0.00-1.00."
    )
    classification: str | None = None
    processing_state: AIProcessingState | None = Field(
        default=None,
        description=(
            "The incident's state. 'awaiting_approval' with a decided=None is a "
            "completed draft waiting for a human; there is no paused thread behind it."
        ),
    )
    processing_start: datetime | None = None
    human_review_required: bool | None = None
    decided: str | None = Field(
        default=None,
        description="The recorded decision, or null while the draft is undecided.",
    )


class SuggestionDecisionRequest(BaseModel):
    """Accept or reject a completed draft."""

    decision: str = Field(
        ...,
        pattern="^(approved|rejected)$",
        description="'approved' applies the resolution and completes the incident; "
        "'rejected' records the decision and leaves the incident to be closed by hand.",
    )
    reason: str | None = Field(default=None, description="Why the draft was accepted or rejected.")
    solution: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "The resolution actually applied. Optional on approval: when omitted the "
            "drafted suggestion is accepted as written. When given it becomes "
            "ai_resolution verbatim, which is what lets an operator record what they "
            "did rather than what the model proposed."
        ),
    )
    evidence: dict[str, Any] | None = Field(
        default=None, description="Optional structured evidence for the decision."
    )


class SuggestionDecisionResponse(BaseModel):
    """The recorded decision and, on acceptance, what was written to the incident."""

    approval_id: UUID
    execution_id: UUID
    incident_sys_id: str
    decision: str
    decided_by: str = Field(description="The operator token's subject, never taken from the body.")
    decided_at: datetime
    reason: str | None = None
    ai_resolution_written: bool = Field(
        default=False,
        description="True when ai_resolution and ai_processing_end were written.",
    )
    ai_processing_end: str | None = None
    ai_processing_state: str | None = None
    knowledge_capture: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Knowledge capture result, present when the operator supplied their own "
            "resolution. None when they merely accepted the drafted suggestion: the "
            "model's own words are not captured as human knowledge."
        ),
    )
