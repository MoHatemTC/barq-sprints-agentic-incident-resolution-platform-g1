from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ClusterStatus(StrEnum):
    CREATING = "creating"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    FAILED = "failed"
    EXPIRED = "expired"


class ClusterRole(StrEnum):
    ANCHOR = "anchor"
    FOLLOWER = "follower"


class AdmissionMode(StrEnum):
    LEADER = "leader"
    FOLLOWER = "follower"
    INDEPENDENT = "independent"


class AdmissionResult(BaseModel):
    """Result of the semantic admission engine evaluation."""

    model_config = ConfigDict(extra="ignore")

    mode: AdmissionMode
    cluster_id: UUID | None = None
    similarity_score: float | None = None
    reason: str
    anchor_incident_sys_id: str | None = None
    anchor_incident_number: str | None = None


class ClusterSolution(BaseModel):
    """The durable resolution produced by the leader LangGraph pipeline execution."""

    model_config = ConfigDict(extra="allow")

    outcome: str
    summary: str
    suggestion: str | None = None
    confidence: float | None = None
    classification: str | None = None
    work_note: str
    model_name: str | None = None
    agent_version: str | None = None
    resolved_at: datetime


class ClusterMembershipView(BaseModel):
    """High-level view of an incident's membership in a semantic cluster."""

    model_config = ConfigDict(from_attributes=True)

    cluster_id: UUID
    execution_id: UUID
    incident_sys_id: str
    incident_number: str
    similarity_score: float
    role: ClusterRole
    status: ClusterStatus
    solution: ClusterSolution | None = None
