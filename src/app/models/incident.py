# mypy: disable-error-code="literal-required"
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SCOPE = "x_2215032_ai_inc_0"


class AIProcessingState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETE = "complete"
    FAILED = "failed"


class Incident(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    sys_id: str
    number: str
    short_description: str | None = ""
    description: str | None = ""
    state: str | None = ""
    priority: str | None = ""
    category: str | None = ""
    subcategory: str | None = ""
    active: bool = True

    # AI Fields
    ai_enabled: bool = Field(default=False, alias=f"{_SCOPE}_ai_enabled")
    ai_processing_state: AIProcessingState = Field(
        default=AIProcessingState.PENDING, alias=f"{_SCOPE}_ai_processing_state"
    )
    ai_classification: str | None = Field(default=None, alias=f"{_SCOPE}_ai_classification")
    ai_confidence: float | None = Field(default=None, alias=f"{_SCOPE}_ai_confidence")
    ai_suggestion: str | None = Field(default=None, alias=f"{_SCOPE}_ai_suggestion")
    ai_resolution: str | None = Field(default=None, alias=f"{_SCOPE}_ai_resolution")
    ai_model_name: str | None = Field(default=None, alias=f"{_SCOPE}_ai_model_name")
    ai_agent_version: str | None = Field(default=None, alias=f"{_SCOPE}_ai_agent_version")
    ai_processing_start: datetime | None = Field(
        default=None, alias=f"{_SCOPE}_ai_processing_start"
    )
    ai_processing_end: datetime | None = Field(default=None, alias=f"{_SCOPE}_ai_processing_end")
    ai_human_review_required: bool = Field(
        default=False, alias=f"{_SCOPE}_ai_human_review_required"
    )
    ai_human_lock: bool = Field(default=False, alias=f"{_SCOPE}_ai_human_lock")
    ai_failure_reason: str | None = Field(default=None, alias=f"{_SCOPE}_ai_failure_reason")

    @model_validator(mode="before")
    @classmethod
    def _sanitize_servicenow_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        cleaned = dict(data)
        bool_fields = {
            "active",
            f"{_SCOPE}_ai_enabled",
            f"{_SCOPE}_ai_human_review_required",
            f"{_SCOPE}_ai_human_lock",
        }
        state_field = f"{_SCOPE}_ai_processing_state"

        for key, value in cleaned.items():
            if value == "":
                if key in bool_fields:
                    cleaned[key] = False
                elif key == state_field:
                    cleaned[key] = AIProcessingState.PENDING.value
                else:
                    cleaned[key] = None
            elif key in bool_fields and isinstance(value, str):
                cleaned[key] = value.lower() in ("true", "1", "t", "yes")

        return cleaned


class IncidentUpdatePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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

    def to_table_api_body(self) -> dict[str, str]:
        body: dict[str, str] = {}
        if self.work_notes is not None:
            body["work_notes"] = self.work_notes

        data = self.model_dump(by_alias=True, exclude={"work_notes"}, exclude_none=True)
        for key, value in data.items():
            if isinstance(value, AIProcessingState):
                body[key] = value.value
            elif isinstance(value, bool):
                body[key] = "true" if value else "false"
            elif isinstance(value, float):
                body[key] = f"{value:.2f}"
            elif isinstance(value, datetime):
                body[key] = value.strftime("%Y-%m-%d %H:%M:%S")
            else:
                body[key] = str(value)
        return body
