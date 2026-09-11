from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SCOPE = "x_2215032_ai_inc_0"
EXECUTION_LOG_TABLE = f"{_SCOPE}_ai_execution_log"


class ExecutionStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    ABANDONED = "abandoned"


class ExecutionLogEntry(BaseModel):
    """Read model: represents a record returned by ServiceNow after POST/GET."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    sys_id: str
    execution_id: str
    incident_reference: str  # sys_id of the parent incident
    agent: str
    action: str
    status: ExecutionStatus
    timestamp: datetime | None = None
    result: str | None = None
    error: str | None = None


class ExecutionLogCreatePayload(BaseModel):
    """Write model: the body we POST to create a new execution log record."""

    incident_sys_id: str = Field(..., description="sys_id of the parent incident")
    execution_id: str = Field(..., max_length=40)
    agent: str = Field(..., max_length=100)
    action: str = Field(..., max_length=100)
    status: ExecutionStatus
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    result: str | None = Field(default=None, max_length=4000)
    error: str | None = Field(default=None, max_length=4000)

    @field_validator("execution_id", "agent", "action")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field must not be blank")
        return v

    def to_table_api_body(self) -> dict[str, str]:
        """Serialise to the flat dict the ServiceNow Table API expects."""
        body: dict[str, str] = {
            "incident_reference": self.incident_sys_id,
            "execution_id": self.execution_id,
            "agent": self.agent,
            "action": self.action,
            "status": self.status.value,
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if self.result is not None:
            body["result"] = self.result
        if self.error is not None:
            body["error"] = self.error
        return body
