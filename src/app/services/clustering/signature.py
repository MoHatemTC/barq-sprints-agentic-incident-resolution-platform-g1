"""Incident eligibility gating, redaction, and deterministic canonical signature builder."""

from __future__ import annotations

import re
from typing import Any

from observability.redaction import redact_text

# Terminal / Ineligible ServiceNow states (string labels and numeric state codes)
_INELIGIBLE_STATES = frozenset(
    {
        "closed",
        "resolved",
        "canceled",
        "cancelled",
        "6",  # ServiceNow state: Resolved
        "7",  # ServiceNow state: Closed
        "8",  # ServiceNow state: Canceled
    }
)


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    """Safely get a field whether obj is a dict or an object with attributes."""
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def is_incident_cluster_eligible(incident: Any) -> bool:
    """Check whether an incident is eligible for semantic clustering.

    An incident is ineligible if:
    - It is marked inactive (active == False)
    - It is already closed, resolved, or cancelled
    - A human operator has locked the incident (ai_human_lock == True)
    - It lacks basic textual content (both short_description and description are empty)
    """
    if incident is None:
        return False

    # Check active flag
    active = _get_field(incident, "active", True)
    if active is False or active == "false" or active == 0:
        return False

    # Check state
    state = str(_get_field(incident, "state", "") or "").strip().lower()
    if state in _INELIGIBLE_STATES:
        return False

    # Check human lock
    human_lock = _get_field(incident, "ai_human_lock", None)
    if human_lock is True:
        return False

    # Check text content existence
    short_desc = str(_get_field(incident, "short_description", "") or "").strip()
    desc = str(_get_field(incident, "description", "") or "").strip()
    if not short_desc and not desc:
        return False

    return True


def sanitize_incident_text(text: str) -> str:
    """Sanitize incident text using security redaction patterns.

    Guarantees API keys, bearer tokens, passwords, and sensitive PII are stripped
    BEFORE embedding generation and Qdrant ingestion.
    """
    if not text:
        return ""
    # Redact credentials and PII
    redacted = redact_text(text)
    # Normalize consecutive whitespace
    normalized = re.sub(r"[ \t]+", " ", redacted).strip()
    return normalized


def build_incident_signature(incident: Any) -> str:
    """Construct a deterministic canonical signature string for semantic clustering.

    Excludes volatile fields: incident numbers, sys_ids, timestamps, caller IDs,
    assignment groups, and state fields.
    Caps description to 500 characters to prevent long stack traces from drowning
    out the core incident symptom in vector embeddings.
    """
    parts: list[str] = []

    service = _get_field(incident, "service", None)
    if service and str(service).strip():
        parts.append(f"Service: {str(service).strip().lower()}")

    category = _get_field(incident, "category", None)
    if category and str(category).strip():
        parts.append(f"Category: {str(category).strip().lower()}")

    subcategory = _get_field(incident, "subcategory", None)
    if subcategory and str(subcategory).strip():
        parts.append(f"Subcategory: {str(subcategory).strip().lower()}")

    short_desc = _get_field(incident, "short_description", None)
    if short_desc and str(short_desc).strip():
        clean_summary = sanitize_incident_text(str(short_desc))
        if clean_summary:
            parts.append(f"Summary: {clean_summary}")

    desc = _get_field(incident, "description", None)
    if desc and str(desc).strip():
        clean_desc = sanitize_incident_text(str(desc))
        if clean_desc:
            # Caps at 500 characters to preserve semantic centroid
            parts.append(f"Description: {clean_desc[:500]}")

    return "\n".join(parts)
