"""Deterministic policy: eligibility, priority derivation and risk (S2.5).

Code, not prompt: the model never decides whether an incident is eligible or how
risky it is. Every rule cites the BARQ IT Service Operations Manual, Edition 4.0.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from agent.state import (
    ClassificationResult,
    Eligibility,
    IncidentSnapshot,
    RiskAssessment,
    RiskLevel,
)
from app.models.knowledge import Classification

#: Incident fields that may carry the affected service, most specific first.
_SERVICE_KEYS: tuple[str, ...] = ("business_service", "service", "u_service", "cmdb_ci")

#: Manual §5.2 service catalogue → criticality tier.
SERVICE_TIERS: Mapping[str, int] = {
    "order-processing": 1,
    "identity": 1,
    "sap-erp": 1,
    "corporate-email": 2,
    "corporate-vpn": 2,
    "file-services": 2,
    "corporate-wifi": 3,
    "endpoint": 3,
    "print-services": 4,
}

#: Manual §3.3 priority matrix, keyed by (impact, urgency).
PRIORITY_MATRIX: Mapping[tuple[int, int], int] = {
    (1, 1): 1,
    (1, 2): 1,
    (1, 3): 2,
    (2, 1): 1,
    (2, 2): 2,
    (2, 3): 3,
    (3, 1): 2,
    (3, 2): 3,
    (3, 3): 4,
}

#: ServiceNow incident ``state`` choice values.
STATE_ON_HOLD = "3"
INACTIVE_STATES = frozenset({"6", "7", "8"})  # resolved, closed, canceled

#: Manual §6 (KB0006): "An MFA reset is a high-risk identity action. It always
#: requires the approval step."
_FACTOR = r"\b(mfa|multi[- ]factor|two[- ]factor|2fa|authenticator)\b"
_FACTOR_CHANGE = r"\b(reset|lost|new|replace|replaced|stolen)\b"
_HIGH_RISK_IDENTITY_ACTION = re.compile(
    rf"(?is){_FACTOR}.*{_FACTOR_CHANGE}|{_FACTOR_CHANGE}.*{_FACTOR}"
)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    match = re.match(r"\s*(\d+)", str(value))
    return int(match.group(1)) if match else None


def service_name(raw: Mapping[str, Any]) -> str | None:
    """Best-effort service name from a Table API incident record.

    ``business_service`` is a reference field: without ``sysparm_display_value`` it
    arrives as ``{"value": sys_id, "link": …}``, which carries no name. Only a
    display value (or a plain string) is used; an unresolvable reference yields
    ``None``, and :func:`service_reference_unresolved` reports that separately so
    the risk rules can fail closed instead of reading it as "no service".
    """
    for key in _SERVICE_KEYS:
        value = raw.get(key)
        if isinstance(value, Mapping):
            value = value.get("display_value")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def service_reference_unresolved(raw: Mapping[str, Any]) -> bool:
    """True when a service is set on the record but its name could not be read.

    ``get_incident`` does not pass ``sysparm_display_value``, so a populated
    reference field arrives as ``{"value": sys_id, "link": …}``. Treating that as
    "no service" would silently skip the Tier 1 approval rule (§11.1) for exactly
    the incidents it exists to catch, so it is surfaced and failed closed instead.
    """
    if service_name(raw) is not None:
        return False
    for key in _SERVICE_KEYS:
        value = raw.get(key)
        if isinstance(value, Mapping) and str(value.get("value") or "").strip():
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def snapshot_incident(raw: Mapping[str, Any]) -> IncidentSnapshot:
    """Project a validated ``app.models.incident.Incident`` dump onto the graph."""
    lock = raw.get("ai_human_lock")
    return IncidentSnapshot(
        sys_id=str(raw["sys_id"]),
        number=str(raw["number"]),
        short_description=str(raw.get("short_description") or ""),
        description=str(raw.get("description") or ""),
        state=str(raw.get("state") or ""),
        priority=_as_int(raw.get("priority")),
        impact=_as_int(raw.get("impact")),
        urgency=_as_int(raw.get("urgency")),
        category=str(raw.get("category") or "").strip().lower(),
        subcategory=str(raw.get("subcategory") or "").strip().lower(),
        service=service_name(raw),
        service_unresolved=service_reference_unresolved(raw),
        active=bool(raw.get("active", True)),
        ai_enabled=bool(raw.get("ai_enabled", False)),
        ai_human_lock=lock if isinstance(lock, bool) else None,
        ai_processing_state=str(raw.get("ai_processing_state") or "pending"),
    )


def check_eligibility(
    incident: IncidentSnapshot,
    *,
    event_number: str,
    supported_categories: Iterable[str],
) -> Eligibility:
    """Manual §11.3, pilot-service and desk-override layers. Fail closed."""
    reasons: list[str] = []
    if incident.number != event_number:
        reasons.append(f"event number {event_number} does not match incident {incident.number}")
    if not incident.active or incident.state in INACTIVE_STATES:
        reasons.append("incident is not active")
    if incident.category not in {c.lower() for c in supported_categories}:
        reasons.append(f"category '{incident.category or 'none'}' is not in the supported set")
    if not incident.ai_enabled:
        reasons.append("AI assistance is not enabled on the incident")
    if incident.ai_human_lock is not False:
        state = "set" if incident.ai_human_lock else "unknown"
        reasons.append(f"human lock is {state}: an analyst has taken over")
    if incident.state == STATE_ON_HOLD:
        reasons.append("incident is on hold")
    if incident.ai_processing_state != "pending":
        reasons.append(f"already processed (AI state '{incident.ai_processing_state}')")
    return Eligibility(eligible=not reasons, reasons=reasons)


def effective_priority(incident: IncidentSnapshot) -> int | None:
    """The more severe of the recorded priority and the §3.3 matrix derivation.

    "Priority is derived, not chosen" (§3.3): a hand-lowered priority does not
    lower the risk verdict.
    """
    derived = None
    if incident.impact in (1, 2, 3) and incident.urgency in (1, 2, 3):
        derived = PRIORITY_MATRIX[(incident.impact, incident.urgency)]
    candidates = [p for p in (incident.priority, derived) if p is not None]
    return min(candidates) if candidates else None


def assess_risk(
    incident: IncidentSnapshot,
    classification: ClassificationResult,
    *,
    risk_priorities: Iterable[int],
) -> RiskAssessment:
    """Decide risk from the incident record alone — no evidence, no model output
    other than the category label. Runs strictly before retrieval.

    HIGH leaves the automated path; ELEVATED may draft but needs approval before
    anything is acted on (Sprint 4 interrupt); LOW proceeds.
    """
    priority = effective_priority(incident)
    tier = SERVICE_TIERS.get(incident.service) if incident.service else None
    high: list[str] = []
    elevated: list[str] = []

    if priority is None:
        high.append("priority unknown: failing closed")
    elif priority in set(risk_priorities):
        high.append(f"Priority {priority} leaves the automated path before any search (§11.7)")
    if classification.label is Classification.SECURITY:
        high.append("suspected security incident: goes to Security, never drafted (§6)")
    if tier == 1:
        elevated.append(f"Tier 1 service '{incident.service}': no action without approval (§11.1)")
    elif incident.service_unresolved:
        # A service is set but its name did not resolve, so we cannot rule out
        # Tier 1. Fail closed rather than default the tier to "not critical".
        elevated.append("affected service could not be resolved, so its tier is unknown (§11.1)")
    text = f"{incident.short_description}\n{incident.description}"
    if _HIGH_RISK_IDENTITY_ACTION.search(text):
        elevated.append("MFA reset is a high-risk identity action that needs approval (§6)")

    if high:
        return RiskAssessment(
            level=RiskLevel.HIGH,
            reasons=high + elevated,
            approval_required=True,
            service_tier=tier,
        )
    if elevated:
        return RiskAssessment(
            level=RiskLevel.ELEVATED,
            reasons=elevated,
            approval_required=True,
            service_tier=tier,
        )
    return RiskAssessment(
        level=RiskLevel.LOW,
        reasons=[f"Priority {priority}, service tier {tier or 'unknown'}"],
        approval_required=False,
        service_tier=tier,
    )


__all__ = [
    "INACTIVE_STATES",
    "PRIORITY_MATRIX",
    "SERVICE_TIERS",
    "STATE_ON_HOLD",
    "assess_risk",
    "check_eligibility",
    "effective_priority",
    "service_name",
    "snapshot_incident",
]
