"""Pydantic V2 schemas for Human-in-the-Loop (HITL) approval governance endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ApprovalStatus = Literal["pending", "approved", "rejected"]
ApprovalDecision = Literal["approved", "rejected"]


class ApprovalResponse(BaseModel):
    """Schema representing a HITL approval request record."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    approval_id: UUID | str = Field(
        ...,
        description="Unique approval request identifier",
    )
    execution_id: UUID | str = Field(
        ...,
        description="Associated execution identifier",
    )
    incident_sys_id: str = Field(
        ...,
        pattern=r"^[0-9a-fA-F]{32}$",
        description="ServiceNow incident 32-character hexadecimal sys_id",
    )
    action_type: str = Field(
        ...,
        description="Type of proposed remediation action requiring approval",
    )
    proposed_payload: dict[str, Any] = Field(
        ...,
        description="Parameters and arguments for the proposed action",
    )
    status: ApprovalStatus = Field(
        default="pending",
        description="Current approval status: pending, approved, or rejected",
    )
    requested_at: datetime = Field(
        ...,
        description="Timestamp when approval was requested by the agent",
    )
    decided_at: datetime | None = Field(
        default=None,
        description="Timestamp when human operator decided the approval",
    )
    decided_by: str | None = Field(
        default=None,
        description="Identifier of operator or user who submitted the decision",
    )


class ApprovalDecisionRequest(BaseModel):
    """Schema for submitting a human operator approval decision."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    decision: ApprovalDecision = Field(
        ...,
        description="Operator decision: 'approved' or 'rejected'",
    )
    comment: str | None = Field(
        default=None,
        description="Optional justification, feedback, or remediation note",
    )
    decided_by: str = Field(
        ...,
        min_length=1,
        description="Operator identifier submitting this decision",
    )
