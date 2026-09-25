"""Pydantic V2 schemas matching the PostgreSQL operational-state Approval model."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ApprovalDecision = Literal["approved", "rejected", "cancelled", "expired"]


class ApprovalResponse(BaseModel):
    """Schema representing an approval record from the 'approvals' table."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, from_attributes=True)

    id: UUID = Field(
        ...,
        description="Primary key from approvals.id",
    )
    execution_id: UUID = Field(
        ...,
        description="Foreign key to executions.execution_id",
    )
    workflow_state_id: UUID | None = Field(
        default=None,
        description="Foreign key to workflow_state.id if associated with a specific node",
    )
    decision: ApprovalDecision | None = Field(
        default=None,
        description="Decision outcome matching approvals.decision constraint; null while paused",
    )
    decided_by: str | None = Field(
        default=None,
        max_length=255,
        description="Identity of the operator or automated service that decided",
    )
    reason: str | None = Field(
        default=None,
        description="Operator justification or explanation text",
    )
    evidence: dict[str, Any] | None = Field(
        default=None,
        description="Structured context or parameters evaluated during decision",
    )
    decided_at: datetime | None = Field(
        default=None,
        description="Timestamp when the decision was finalized",
    )
    status: str = Field(
        default="decided",
        description="decided | awaiting_approval",
    )
    brief: dict[str, Any] | None = Field(
        default=None,
        description="Approval Brief Agent output; descriptive only",
    )
    facts: dict[str, Any] | None = Field(
        default=None,
        description="Raw interrupt payload persisted at pause time",
    )


class ApprovalDecisionRequest(BaseModel):
    """Schema for submitting a human operator approval decision."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    decision: ApprovalDecision = Field(
        ...,
        description="Decision: 'approved', 'rejected', 'cancelled', or 'expired'",
    )
    decided_by: str = Field(
        ...,
        max_length=255,
        min_length=1,
        description="Identity of the deciding operator",
    )
    reason: str | None = Field(
        default=None,
        description="Optional justification, feedback, or remediation note",
    )
    evidence: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured evidence or parameter overrides",
    )
