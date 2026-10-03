# mypy: disable-error-code="literal-required"
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.datetime import assume_utc, require_timezone

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
    #: Reference sys_ids (read with ``sysparm_exclude_reference_link``). The caller is
    #: who the agent may address; the group is never overwritten once a person set it.
    caller_id: str | None = ""
    assignment_group: str | None = ""
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
    ai_human_lock: bool | None = Field(default=None, alias=f"{_SCOPE}_ai_human_lock")
    ai_failure_reason: str | None = Field(default=None, alias=f"{_SCOPE}_ai_failure_reason")
    ai_retry_count: int | None = Field(default=0, ge=0, alias=f"{_SCOPE}_ai_retry_count")

    @model_validator(mode="before")
    @classmethod
    def _sanitize_servicenow_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        cleaned = dict(data)
        LOCK_FIELD = f"{_SCOPE}_ai_human_lock"
        bool_fields = {
            "active",
            f"{_SCOPE}_ai_enabled",
            f"{_SCOPE}_ai_human_review_required",
        }
        state_field = f"{_SCOPE}_ai_processing_state"

        for key, value in cleaned.items():
            if key == LOCK_FIELD:
                if value == "":
                    cleaned[key] = None
                elif isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized in ("true", "1"):
                        cleaned[key] = True
                    elif normalized in ("false", "0"):
                        cleaned[key] = False
                    else:
                        cleaned[key] = None

                continue
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

    @field_validator("ai_processing_start", "ai_processing_end")
    @classmethod
    def _attach_utc_timezone(cls, value: datetime | None) -> datetime | None:
        return None if value is None else assume_utc(value)


class IncidentUpdatePayload(BaseModel):
    """The agent's write body for the incident table.

    ``extra="forbid"`` is load-bearing, not tidiness. Every AI field is written
    through a hand-typed ``x_2215032_ai_inc_0_ai_*`` alias, and under the Pydantic
    default a single-character typo in one of them is silently dropped from
    ``to_table_api_body()``: the write goes out missing that field and nothing
    reports it, because the field never entered the requested body for
    ``_verify_write_persisted`` to compare. Forbidding extras turns that silent
    no-write into a loud error at the call site.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

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

    @field_validator("ai_processing_start", "ai_processing_end")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        return require_timezone(value)

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
                if value.tzinfo is None:
                    raise ValueError("datetime must include timezone information")
                body[key] = value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
            else:
                body[key] = str(value)
        return body


#: ServiceNow incident states the agent moves through (internal choice values).
INCIDENT_STATE_NEW: Literal["1"] = "1"
INCIDENT_STATE_IN_PROGRESS: Literal["2"] = "2"
INCIDENT_STATE_ON_HOLD: Literal["3"] = "3"
#: Stock On hold reason "Awaiting Caller".
HOLD_REASON_AWAITING_CALLER: Literal["1"] = "1"
INCIDENT_STATE_RESOLVED: Literal["6"] = "6"
INCIDENT_STATE_CLOSED: Literal["7"] = "7"

_SYS_ID_PATTERN = r"^[0-9a-f]{32}$"


class IncidentFulfilmentPayload(BaseModel):
    """The standard incident fields the agent may set when it works an incident itself.

    Separate from ``IncidentUpdatePayload`` (the AI fields) on purpose: each field here
    has its own ServiceNow ACL, and the values are pinned so a model can never choose a
    state, a close code or a free-text target. ``extra="forbid"`` keeps any other field
    out of the PATCH.
    """

    model_config = ConfigDict(extra="forbid")

    assignment_group: str | None = Field(default=None, pattern=_SYS_ID_PATTERN)
    state: Literal["2", "3", "6"] | None = None
    hold_reason: Literal["1"] | None = None
    comments: str | None = Field(default=None, max_length=4000)
    work_notes: str | None = Field(default=None, max_length=4000)
    close_code: Literal["Solution provided"] | None = None
    close_notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _resolution_is_complete(self) -> IncidentFulfilmentPayload:
        # ServiceNow requires close information when an incident is resolved.
        if self.state == INCIDENT_STATE_RESOLVED and not (self.close_code and self.close_notes):
            raise ValueError("resolving an incident requires close_code and close_notes")
        # The agent only puts an incident On Hold to wait for the caller's answer, so
        # the question itself must travel with it.
        if self.state == INCIDENT_STATE_ON_HOLD and not (
            self.hold_reason == HOLD_REASON_AWAITING_CALLER and self.comments
        ):
            raise ValueError("On Hold requires hold_reason Awaiting Caller and the question")
        if self.hold_reason is not None and self.state != INCIDENT_STATE_ON_HOLD:
            raise ValueError("hold_reason is only set together with On Hold")
        return self

    def to_table_api_body(self) -> dict[str, str]:
        return {key: str(value) for key, value in self.model_dump(exclude_none=True).items()}


#: Result of a fulfilment write, so a retry or a person's change is never an error.
FulfilmentResult = Literal["applied", "already_applied", "skipped_state_changed"]
