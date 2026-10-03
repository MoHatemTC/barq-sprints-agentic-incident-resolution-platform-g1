"""Webhook request and response schemas for ServiceNow inbound events."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.events import CONTRACT_EVENT_TYPES
from app.exceptions.app_errors import UnknownContractVersionError

SUPPORTED_VERSIONS = sorted(CONTRACT_EVENT_TYPES)


class IncidentWebhookPayload(BaseModel):
    """Pydantic model for an inbound ServiceNow incident event payload."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    event_id: str = Field(..., description="Unique identifier for the event.")
    sys_id: str = Field(
        ...,
        pattern=r"^[0-9a-fA-F]{32}$",
        description="The system identifier for the incident.",
    )
    number: str = Field(
        ...,
        pattern=r"^INC\d{7,}$",
        max_length=32,
        description="The number of the incident.",
    )
    event_type: str = Field(
        ...,
        max_length=64,
        description=(
            "The type of the event. v1: incident.created, incident.updated. v2 adds "
            "incident.caller_replied, incident.caller_updated, incident.handed_back, "
            "incident.engineer_replied, incident.reopened, incident.closed."
        ),
    )
    contract_version: str = Field(
        "v1",
        description="Contract version. Optional: a producer that omits it is treated as v1.",
    )
    actor_sys_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{32}$",
        description="v2 only: the ServiceNow user whose action caused the event.",
    )

    @field_validator("contract_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value not in CONTRACT_EVENT_TYPES:
            raise UnknownContractVersionError(
                f"Unsupported contract version: '{value}'. Supported versions: {SUPPORTED_VERSIONS}"
            )
        return value

    @model_validator(mode="after")
    def validate_type_for_version(self) -> IncidentWebhookPayload:
        allowed = CONTRACT_EVENT_TYPES[self.contract_version]
        if self.event_type not in allowed:
            raise ValueError(
                f"event_type '{self.event_type}' is not part of contract "
                f"{self.contract_version}: expected one of {sorted(allowed)}"
            )
        if self.actor_sys_id is not None and self.contract_version == "v1":
            raise ValueError("actor_sys_id is only part of contract v2")
        return self


class WebhookAcceptedResponse(BaseModel):
    """Response returned upon successful acceptance (HTTP 202) of an inbound incident event."""

    status: str = "accepted"
    event_id: str
    correlation_id: str
    idempotent_replay: bool = False
