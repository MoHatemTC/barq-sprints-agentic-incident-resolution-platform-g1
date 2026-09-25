"""Edge conditions (S2.5): pure, deterministic functions of recorded state.

Each router reads exactly one state section written by the node it follows, and
returns the name of the next node. A missing or malformed section always routes to
``act`` (fail closed: escalate rather than continue on unknown state).
"""

from __future__ import annotations

from typing import Final, Literal

from agent.config import AgentSettings, get_agent_settings
from agent.state import (
    AgentState,
    Diagnosis,
    Eligibility,
    GateResult,
    RetrievalResult,
    RiskAssessment,
    RiskLevel,
)

ACT: Final = "act"


def after_validate(state: AgentState) -> Literal["classify", "act"]:
    """eligible → classify; ineligible → act (records the skip, writes nothing)."""
    try:
        eligible = Eligibility.model_validate(state["eligibility"]).eligible
    except (KeyError, ValueError):
        return ACT
    return "classify" if eligible else ACT


def after_determine_risk(state: AgentState) -> Literal["retrieve", "act"]:
    """risk HIGH → act (escalate: no retrieval, no generation); LOW/ELEVATED → retrieve."""
    try:
        level = RiskAssessment.model_validate(state["risk"]).level
    except (KeyError, ValueError):
        return ACT
    return ACT if level is RiskLevel.HIGH else "retrieve"


def after_retrieve(state: AgentState) -> Literal["diagnose", "act"]:
    """best relevance ≥ threshold → diagnose; otherwise → act (no evidence)."""
    try:
        sufficient = RetrievalResult.model_validate(state["retrieval"]).sufficient
    except (KeyError, ValueError):
        return ACT
    return "diagnose" if sufficient else ACT


def after_diagnose(state: AgentState) -> Literal["generate", "act"]:
    """at least one retrieved article describes the fault → generate; none → act."""
    try:
        matched = Diagnosis.model_validate(state["diagnosis"]).matched_article_ids
    except (KeyError, ValueError):
        return ACT
    return "generate" if matched else ACT


def _gate_passed(state: AgentState, key: str) -> bool:
    try:
        return GateResult.model_validate(state[key]).passed  # type: ignore[literal-required]
    except (KeyError, ValueError):
        return False


def _revision_count(state: AgentState) -> int:
    """return the revision count of the state."""
    try:
        return state["revision_count"]
    except (KeyError, ValueError):
        return 0


def after_verify_evidence(
    state: AgentState,
    settings: AgentSettings | None = None,
) -> Literal["safety_check", "generate", "act"]:
    """gate passed → safety_check; failed & revision < max → generate; otherwise → act."""
    if "verification" not in state:
        return ACT
    if _gate_passed(state, "verification"):
        return "safety_check"
    cfg = settings or get_agent_settings()
    if _revision_count(state) < cfg.agent_max_revisions:
        return "generate"
    return ACT


def after_safety_check(state: AgentState) -> Literal["confidence_check", "act"]:
    """gate passed → confidence_check; failed → act (blocked)."""
    if _gate_passed(state, "safety"):
        return "confidence_check"
    else:
        return ACT


#: Every transition, for the design record and the exhaustive edge tests.
#: (source, condition, target). Unconditional edges use the condition "always".
EDGE_TABLE: tuple[tuple[str, str, str], ...] = (
    ("__start__", "always", "load"),
    ("load", "always", "validate"),
    ("validate", "eligibility.eligible", "classify"),
    ("validate", "not eligibility.eligible", "act"),
    ("classify", "always", "determine_risk"),
    ("determine_risk", "risk.level in {low, elevated}", "retrieve"),
    ("determine_risk", "risk.level == high", "act"),
    ("retrieve", "retrieval.best_relevance >= threshold", "diagnose"),
    ("retrieve", "retrieval.best_relevance < threshold or no hits", "act"),
    ("diagnose", "diagnosis.matched_article_ids non-empty", "generate"),
    ("diagnose", "diagnosis.matched_article_ids empty", "act"),
    ("generate", "always", "verify_evidence"),
    ("verify_evidence", "verification.passed", "safety_check"),
    ("verify_evidence", "not verification.passed and revisions < max", "generate"),
    ("verify_evidence", "not verification.passed and revisions >= max", "act"),
    ("safety_check", "safety.passed", "confidence_check"),
    ("safety_check", "not safety.passed", "act"),
    ("confidence_check", "always (act applies the floor)", "act"),
    ("act", "always", "__end__"),
)

__all__ = [
    "EDGE_TABLE",
    "after_determine_risk",
    "after_diagnose",
    "after_retrieve",
    "after_safety_check",
    "after_validate",
    "after_verify_evidence",
]
