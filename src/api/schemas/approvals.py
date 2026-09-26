"""Pydantic V2 schemas matching the PostgreSQL operational-state Approval model."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

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
    # decided_by is deliberately absent: it comes from the verified operator
    # token's subject, and accepting it here let any caller write any name
    # into the audit record (#148).
    reason: str | None = Field(
        default=None,
        description="Optional justification, feedback, or remediation note",
    )
    evidence: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured evidence or parameter overrides",
    )
    solution: str | None = Field(
        default=None,
        max_length=8000,
        validation_alias=AliasChoices("solution", "human_solution"),
        description=(
            "Optional human-authored resolution knowledge contributed while deciding an "
            "escalated incident. Distinct from 'reason': the reason annotates the "
            "decision, the solution is the fix itself and is composed into a KB article "
            "by knowledge capture (S3.5). Persisted folded into 'evidence' together "
            "with the knowledge-capture tool name. Accepted as either 'solution' or "
            "'human_solution'."
        ),
    )


#: Tool name registered in the S3.2 registry for the KB write-back. Folding it into
#: every decision's evidence keeps the registry's high-risk approval checker
#: (PostgreSQLApprovalChecker matches evidence["tool_name"]) well-formed for the
#: executions that carry a solution.
KNOWLEDGE_CAPTURE_TOOL = "publish_kb_article"


def fold_solution_into_evidence(
    evidence: dict[str, Any] | None, solution: str | None
) -> dict[str, Any] | None:
    """Merge the human solution into the evidence persisted with the decision.

    A Mapping is always produced when a solution is present (the strict approval
    checker refuses executions whose evidence rows are not mappings). The caller's
    evidence survives the merge; a conflicting ``tool_name`` is overwritten because
    the knowledge-capture flow owns that key. Redaction runs here — the persistence
    point — so the raw credential never reaches the database.
    """
    if not solution:
        return evidence
    from observability.redaction import redact_text

    folded: dict[str, Any] = dict(evidence) if isinstance(evidence, dict) else {}
    folded["tool_name"] = KNOWLEDGE_CAPTURE_TOOL
    folded["solution"] = redact_text(solution)
    return folded
