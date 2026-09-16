from typing import Literal
from pydantic import BaseModel, Field, ConfigDict,field_validator
from app.exceptions.app_errors import UnknownContractVersionError

class IncidentWebhookPayload(BaseModel):
    """Pydantic model for a webhook payload."""
    model_config = ConfigDict(extra='forbid',validate_assignment=True)

    event_id: str = Field(..., description="Unique identifier for the event.")
    sys_id: str = Field(..., pattern=r"^[0-9a-fA-F]{32}$",description="The system identifier for the incident.")
    number: str = Field(..., pattern=r"^INC\d{7,}$",description="The number of the incident.")
    event_type: Literal['incident.created','incident.updated']= Field(..., description="The type of the event.")
    contract_version: str = Field(default="v1", description="Contract version")

    @field_validator("contract_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if value != "v1":
            raise UnknownContractVersionError(f"Unsupported contract version: '{value}'. Supported versions: ['v1']")
        return value



class WebhookAcceptedResponse(BaseModel):
    status: str = "accepted"
    event_id: str
    correlation_id: str
    idempotent_replay: bool = False
