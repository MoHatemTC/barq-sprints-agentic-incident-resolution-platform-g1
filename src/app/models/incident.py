from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SCOPE = "x_2215032_ai_inc_0"


class AIProcessingState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETE = "complete"
    FAILED = "failed"


class Incident(BaseModel):
    model_config = ConfigDict(extra="allow")

    sys_id: str
    number: str
    short_description: str = ""
    description: str = ""
    state: str
    priority: str = ""
    category: str = ""
    subcategory: str = ""
    active: bool

    # AI Fields
    ai_enabled: bool = Field(default=False, alias=f"{_SCOPE}_ai_enabled")
    ai_processing_state: AIProcessingState = Field(
        default=AIProcessingState.PENDING, alias=f"{_SCOPE}_ai_processing_state"
    )
    ai_classification: str = Field(default=None, alias=f"{_SCOPE}_ai_classification")
    ai_confidence: float = Field(default=None, alias=f"{_SCOPE}_ai_confidence")
    ai_suggestion: str = Field(default=None, alias=f"{_SCOPE}_ai_suggestion")
    ai_resolution: str = Field(default=None, alias=f"{_SCOPE}_ai_resolution")
    ai_model_name: str = Field(default=None, alias=f"{_SCOPE}_ai_model_name")
    ai_agent_version: str = Field(default=None, alias=f"{_SCOPE}_ai_agent_version")
    ai_processing_start: datetime = Field(default=None, alias=f"{_SCOPE}_ai_processing_start")
    ai_processing_end: datetime = Field(default=None, alias=f"{_SCOPE}_ai_processing_end")
    ai_human_review_required: bool = Field(
        default=False, alias=f"{_SCOPE}_ai_human_review_required"
    )
    ai_human_locked: bool = Field(default=False, alias=f"{_SCOPE}_ai_human_locked")
    ai_failure_reason: str = Field(default=None, alias=f"{_SCOPE}_ai_failure_reason")


class IncidentUpdatePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    state: str | None = None
    work_notes: str | None = None

    ai_processing_state: AIProcessingState | None = Field(
        default=None, alias=f"{_SCOPE}_ai_processing_state"
    )
    ai_classification: str | None = Field(
        default=None, alias=f"{_SCOPE}_ai_classification", max_length=100
    )
    ai_confidence: float | None = Field(
        default=None, alias=f"{_SCOPE}_ai_confidence", ge=0.0, le=1.0
    )
    ai_suggestion: str | None = Field(
        default=None, alias=f"{_SCOPE}_ai_suggestion", max_length=4000
    )
    ai_resolution: str | None = Field(
        default=None, alias=f"{_SCOPE}_ai_resolution", max_length=4000
    )
    ai_model_name: str | None = Field(default=None, alias=f"{_SCOPE}_ai_model_name", max_length=100)
    ai_agent_version: str | None = Field(
        default=None, alias=f"{_SCOPE}_ai_agent_version", max_length=64
    )
    ai_processing_start: datetime | None = Field(
        default=None, alias=f"{_SCOPE}_ai_processing_start"
    )
    ai_processing_end: datetime | None = Field(default=None, alias=f"{_SCOPE}_ai_processing_end")
    ai_human_review_required: bool | None = Field(
        default=None, alias=f"{_SCOPE}_ai_human_review_required"
    )
    ai_failure_reason: str | None = Field(
        default=None, alias=f"{_SCOPE}_ai_failure_reason", max_length=4000
    )

    @model_validator(mode="after")
    def _enforce_validation_rules(self) -> IncidentUpdatePayload:
        if (
            self.ai_processing_state == AIProcessingState.FAILED
            and not (self.ai_failure_reason or "").strip()
        ):
            raise ValueError(
                "processing_state=failed requires a non-empty ai_failure_reason in the same write "
            )
        if self.ai_processing_state == AIProcessingState.COMPLETE:
            if self.ai_processing_end is None:
                raise ValueError(
                    "processing_state=complete requires ai_processing_end in the same write "
                )
            if not (self.ai_resolution or "").strip():
                raise ValueError(
                    "processing_state=complete requires a non-empty ai_resolution in the same write"
                )
        if (
            self.ai_processing_start is not None
            and self.ai_processing_end is not None
            and self.ai_processing_end < self.ai_processing_start
        ):
            raise ValueError("ai_processing_end must not precede ai_processing_start")
        return self
