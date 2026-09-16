"""Pydantic V2 schemas for execution audit and trace queries across Sprints 2–4."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ExecutionStatus = Literal[
    "accepted",
    "in_progress",
    "succeeded",
    "failed",
    "blocked",
    "abandoned",
]


class ExecutionResponse(BaseModel):
    """Execution status and audit record response schema."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    execution_id: UUID | str = Field(
        ...,
        description="Unique execution identifier",
    )
    incident_sys_id: str = Field(
        ...,
        pattern=r"^[0-9a-fA-F]{32}$",
        description="ServiceNow incident 32-character hexadecimal sys_id",
    )
    status: ExecutionStatus = Field(
        ...,
        description="Current execution lifecycle status",
    )
    contract_version: str = Field(
        default="v1",
        description="Contract version of the execution payload",
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when execution record was accepted",
    )
    updated_at: datetime = Field(
        ...,
        description="Timestamp when execution status was last updated",
    )
    retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of retries attempted for this execution",
    )


class TraceStep(BaseModel):
    """Single node execution step within an agent trace."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    step_name: str = Field(
        ...,
        description="Name of the trace step or LangGraph agent node",
    )
    status: str = Field(
        ...,
        description="Outcome of the step (e.g. started, succeeded, failed)",
    )
    duration_ms: float = Field(
        ...,
        ge=0.0,
        description="Elapsed execution time in milliseconds",
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp when this step executed",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured step metadata, inputs, or node outputs",
    )


class TraceResponse(BaseModel):
    """Complete execution trace details and step progression."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    execution_id: UUID | str = Field(
        ...,
        description="Unique execution identifier",
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier (e.g. Langfuse / OpenTelemetry)",
    )
    steps: list[TraceStep] = Field(
        default_factory=list,
        description="Sequential list of executed agent trace steps",
    )


class IncidentExecutionsResponse(BaseModel):
    """Collection of all execution records for a specific ServiceNow incident."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    incident_sys_id: str = Field(
        ...,
        pattern=r"^[0-9a-fA-F]{32}$",
        description="ServiceNow incident 32-character hexadecimal sys_id",
    )
    executions: list[ExecutionResponse] = Field(
        default_factory=list,
        description="List of all executions triggered for this incident",
    )
