"""Pydantic V2 schemas for Dead-Letter Queue (DLQ) inspection and operator replay."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DLQReplayStatus = Literal["accepted", "replayed"]


class DLQEventResponse(BaseModel):
    """Schema representing a poisoned or retry-exhausted incident event in the DLQ."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    event_id: str = Field(
        ...,
        description="Unique event identifier",
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Raw outbound event payload that failed processing",
    )
    failure_reason: str = Field(
        ...,
        description="Reason or error message that routed the event to DLQ",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of retry attempts attempted before landing in DLQ",
    )
    failed_at: datetime = Field(
        ...,
        description="Timestamp when event was placed into the DLQ",
    )


class DLQReplayResponse(BaseModel):
    """Response schema returned upon replaying a DLQ event."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    event_id: str = Field(
        ...,
        description="Identifier of the event being replayed",
    )
    status: DLQReplayStatus = Field(
        default="accepted",
        description="Replay outcome status ('accepted' or 'replayed')",
    )
    correlation_id: str = Field(
        ...,
        description="Correlation ID tracking the replay operation",
    )
    message: str = Field(
        ...,
        description="Human-readable result summary of the replay dispatch",
    )
