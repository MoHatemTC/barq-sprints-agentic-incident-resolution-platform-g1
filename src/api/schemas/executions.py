"""Pydantic V2 schemas matching PostgreSQL operational-state models.

Covers Execution and ExecutionNodeState.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Exact check constraint on executions.status in models.py
ExecutionStatus = Literal[
    "accepted",
    "queued",
    "running",
    "awaiting_approval",
    "succeeded",
    "failed",
    "blocked",
    "abandoned",
]

# Exact check constraint on workflow_state.status in models.py
NodeStateStatus = Literal[
    "started",
    "succeeded",
    "failed",
    "blocked",
    "awaiting_approval",
    "skipped",
]


class ExecutionResponse(BaseModel):
    """Schema representing an execution record from the 'executions' table."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, from_attributes=True)

    execution_id: UUID = Field(
        ...,
        description="Primary key from executions.execution_id",
    )
    event_record_id: UUID = Field(
        ...,
        description="Foreign key to events.id",
    )
    incident_sys_id: str = Field(
        ...,
        max_length=32,
        description="ServiceNow incident 32-character sys_id",
    )
    status: ExecutionStatus = Field(
        ...,
        description="Execution lifecycle status matching executions.status constraint",
    )
    node_reached: str | None = Field(
        default=None,
        max_length=100,
        description="Latest workflow node entered",
    )
    model_name: str | None = Field(
        default=None,
        max_length=100,
        description="LLM model identifier used for this execution",
    )
    agent_version: str | None = Field(
        default=None,
        max_length=64,
        description="Version string of the executing agent",
    )
    started_at: datetime = Field(
        ...,
        description="Timestamp when execution started",
    )
    ended_at: datetime | None = Field(
        default=None,
        description="Timestamp when execution reached a terminal state",
    )
    termination_cause: str | None = Field(
        default=None,
        description="Explanation when execution reached terminal status",
    )
    updated_at: datetime = Field(
        ...,
        description="Timestamp when execution record was last modified",
    )


class ExecutionNodeStateResponse(BaseModel):
    """Schema representing one node attempt from the 'workflow_state' table."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, from_attributes=True)

    id: UUID = Field(
        ...,
        description="Primary key of workflow_state record",
    )
    execution_id: UUID = Field(
        ...,
        description="Foreign key to executions.execution_id",
    )
    sequence_number: int = Field(
        ...,
        ge=1,
        description="Sequence order of the node attempt (>= 1)",
    )
    node_name: str = Field(
        ...,
        max_length=100,
        description="Name of the workflow node",
    )
    attempt: int = Field(
        default=1,
        ge=1,
        description="Attempt count for this specific node (>= 1)",
    )
    status: NodeStateStatus = Field(
        ...,
        description="Node state status matching workflow_state.status constraint",
    )
    started_at: datetime = Field(
        ...,
        description="Timestamp when node attempt started",
    )
    ended_at: datetime | None = Field(
        default=None,
        description="Timestamp when node attempt concluded",
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Evidence array gathered during this node execution",
    )
    decision: dict[str, Any] | None = Field(
        default=None,
        description="Node decision payload if applicable",
    )
    state_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="State snapshot object if captured",
    )


class TraceResponse(BaseModel):
    """Execution trace progression combining execution status and workflow node states."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, from_attributes=True)

    execution_id: UUID = Field(
        ...,
        description="Unique execution identifier",
    )
    incident_sys_id: str = Field(
        ...,
        max_length=32,
        description="ServiceNow incident sys_id",
    )
    status: ExecutionStatus = Field(
        ...,
        description="Current execution status",
    )
    node_states: list[ExecutionNodeStateResponse] = Field(
        default_factory=list,
        description="Ordered list of historical workflow node attempts from workflow_state",
    )


class IncidentExecutionsResponse(BaseModel):
    """Collection of execution records for a specific ServiceNow incident."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    incident_sys_id: str = Field(
        ...,
        max_length=32,
        description="ServiceNow incident 32-character sys_id",
    )
    executions: list[ExecutionResponse] = Field(
        default_factory=list,
        description="List of executions associated with this incident",
    )
