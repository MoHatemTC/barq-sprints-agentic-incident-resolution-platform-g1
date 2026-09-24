"""Graph state schema (S2.5).

``AgentState`` is what LangGraph checkpoints: JSON-only values, so a checkpoint row
in ``workflow_state.state_snapshot`` is readable with plain SQL. Each section has a
Pydantic model that validates what a node writes; nodes store
``model.model_dump(mode="json")``.

The sections follow the brief — incident payload, retrieved evidence,
classification, risk, confidence, final outputs — plus the eligibility verdict and
the two Sprint 4 gate results.
"""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.models.knowledge import Classification


class RiskLevel(StrEnum):
    LOW = "low"
    ELEVATED = "elevated"
    HIGH = "high"


class Outcome(StrEnum):
    """Manual §11.4 outcomes, plus the two ways a run ends without one."""

    SUGGESTED = "suggested"
    ESCALATED_HIGH_RISK = "escalated_high_risk"
    ESCALATED_NO_EVIDENCE = "escalated_no_evidence"
    ESCALATED_LOW_CONFIDENCE = "escalated_low_confidence"
    ESCALATED_BLOCKED = "escalated_blocked"
    SKIPPED_INELIGIBLE = "skipped_ineligible"
    SKIPPED_HUMAN_LOCK = "skipped_human_lock"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventPayload(_Section):
    """The minimal outbound event (Outbound Event Contract v1): identifiers only."""

    model_config = ConfigDict(extra="ignore")

    event_id: str
    sys_id: str
    number: str
    event_type: str = "incident.created"


class IncidentSnapshot(_Section):
    """The fields of the incident the graph reasons over, read with OAuth in ``load``."""

    sys_id: str
    number: str
    short_description: str = ""
    description: str = ""
    state: str = ""
    priority: int | None = None
    impact: int | None = None
    urgency: int | None = None
    category: str = ""
    subcategory: str = ""
    service: str | None = None
    #: True when the record names a service but the reference could not be
    #: resolved to a name, so its criticality tier is unknowable. Distinct from
    #: ``service is None`` with this False, which means no service was set at all.
    service_unresolved: bool = False
    active: bool = True
    ai_enabled: bool = False
    ai_human_lock: bool | None = None
    ai_processing_state: str = "pending"


class Eligibility(_Section):
    eligible: bool
    reasons: list[str] = Field(default_factory=list)


class ClassificationResult(_Section):
    label: Classification
    rationale: str
    model_confidence: float = Field(ge=0.0, le=1.0)


class RiskAssessment(_Section):
    level: RiskLevel
    reasons: list[str]
    approval_required: bool
    service_tier: int | None = None


class EvidenceItem(_Section):
    article_id: str
    article_number: str
    version: str
    title: str
    section: str
    chunk_index: int
    text: str
    fused_score: float
    relevance: float = Field(description="Dense cosine similarity to the query (0-1).")

    @property
    def citation(self) -> str:
        return f"{self.article_number} v{self.version} §{self.section}"


class RetrievalResult(_Section):
    query: str
    category_filter: str | None = Field(
        description="Comma-separated corpus categories searched; None = no search."
    )
    hits: list[EvidenceItem]
    best_relevance: float
    threshold: float
    sufficient: bool
    latency_ms: float


class Diagnosis(_Section):
    probable_cause: str
    matched_article_ids: list[str]
    symptom_match: bool
    model_confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class DraftStep(_Section):
    text: str
    article_id: str
    section: str


class Draft(_Section):
    steps: list[DraftStep]
    rendered: str
    dropped_steps: int = 0
    sources: list[str]
    revision_count: int = 0


class UnsupportedClaim(_Section):
    step_index: int
    claim: str
    reason: str
    citation: str | None = None


class InvalidCitation(_Section):
    step_index: int | None = None
    citation: str
    reason: str


class CriticFeedback(_Section):
    passed: bool
    attempt: int = 0
    invalid_citations: list[InvalidCitation] = Field(default_factory=list)
    unsupported_claims: list[UnsupportedClaim] = Field(default_factory=list)
    safety_issues: list[str] = Field(default_factory=list)
    feedback_instructions: str = ""


class GateResult(_Section):
    """Result of a Sprint 4 gate (``verify_evidence`` / ``safety_check``).

    Sprint 2 ships the gates as pass-through nodes: ``implemented`` is False and
    ``passed`` is True. Sprint 4 fills ``checks`` without changing the shape, the
    node signatures or the edge conditions.
    """

    gate: Literal["verify_evidence", "safety_check"]
    passed: bool
    implemented: bool
    checks: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None


class ConfidenceResult(_Section):
    score: float = Field(ge=0.0, le=1.0)
    floor: float
    passed: bool


class FinalOutput(_Section):
    outcome: Outcome
    summary: str
    suggestion: str | None = None
    confidence: float | None = None
    classification: str | None = None
    work_note: str | None = None
    human_review_required: bool
    approval_required: bool = False
    processing_state: str
    actions: list[str] = Field(default_factory=list)
    write_back: Literal["written", "dry_run", "skipped"] = "skipped"


class AgentState(TypedDict, total=False):
    # identity
    execution_id: str
    correlation_id: str
    # incident payload
    event: dict[str, Any]
    incident: dict[str, Any]
    eligibility: dict[str, Any]
    # reasoning
    classification: dict[str, Any]
    risk: dict[str, Any]
    retrieval: dict[str, Any]
    diagnosis: dict[str, Any]
    draft: dict[str, Any]
    # multi-agent revision loop
    critic_feedback: dict[str, Any] | None
    revision_count: int
    # gates
    verification: dict[str, Any]
    safety: dict[str, Any]
    confidence: dict[str, Any]
    # outputs
    output: dict[str, Any]
    started_at: str
    # bookkeeping: the node that wrote this checkpoint, and the path so far
    current_node: str
    path: Annotated[list[str], operator.add]


def initial_state(
    event: EventPayload, *, execution_id: str, correlation_id: str, started_at: str
) -> AgentState:
    return AgentState(
        execution_id=execution_id,
        correlation_id=correlation_id,
        event=event.model_dump(mode="json"),
        started_at=started_at,
        current_node="__input__",
        path=[],
        revision_count=0,
    )


__all__ = [
    "AgentState",
    "ClassificationResult",
    "ConfidenceResult",
    "CriticFeedback",
    "Diagnosis",
    "Draft",
    "DraftStep",
    "Eligibility",
    "EventPayload",
    "EvidenceItem",
    "FinalOutput",
    "GateResult",
    "IncidentSnapshot",
    "InvalidCitation",
    "Outcome",
    "RetrievalResult",
    "RiskAssessment",
    "RiskLevel",
    "UnsupportedClaim",
    "initial_state",
]
