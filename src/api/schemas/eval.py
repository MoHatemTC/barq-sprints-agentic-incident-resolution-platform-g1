"""Pydantic V2 schemas for benchmark evaluation runs and metric reporting."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvalRunRequest(BaseModel):
    """Request schema to initiate an automated benchmark evaluation run."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    dataset_name: str = Field(
        ...,
        description="Name of the evaluation dataset or benchmark corpus",
    )
    sample_size: int | None = Field(
        default=None,
        ge=1,
        description="Optional limit on the number of evaluation samples to execute",
    )
    model_override: str | None = Field(
        default=None,
        description="Optional model identifier override for the evaluation run",
    )


class EvalRunResponse(BaseModel):
    """Response schema acknowledging the launch of an evaluation run."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    run_id: str = Field(
        ...,
        description="Unique identifier for the initiated evaluation run",
    )
    status: str = Field(
        ...,
        description="Current state of the evaluation run (e.g. 'started', 'running')",
    )
    created_at: str = Field(
        ...,
        description="ISO 8601 timestamp when evaluation run was initiated",
    )


class EvalResultResponse(BaseModel):
    """Response schema containing benchmark metrics and scores for an evaluation run."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    run_id: str = Field(
        ...,
        description="Unique identifier of the completed evaluation run",
    )
    dataset_name: str = Field(
        ...,
        description="Name of the evaluated benchmark dataset",
    )
    benchmark_score: float = Field(
        ...,
        ge=0.0,
        description="Overall benchmark composite score",
    )
    accuracy: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Evaluation accuracy score between 0.0 and 1.0",
    )
    p95_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="95th percentile inference/processing latency in milliseconds",
    )
    completed_at: str = Field(
        ...,
        description="ISO 8601 timestamp when evaluation finished",
    )
