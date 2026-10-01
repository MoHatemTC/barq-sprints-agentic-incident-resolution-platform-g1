"""Unit tests for incident eligibility gating and deterministic signature building."""

from __future__ import annotations

from types import SimpleNamespace
import pytest

from app.services.clustering.signature import (
    build_incident_signature,
    is_incident_cluster_eligible,
    sanitize_incident_text,
)


def test_is_incident_cluster_eligible_active_and_open() -> None:
    inc = SimpleNamespace(
        active=True,
        state="in_progress",
        ai_human_lock=False,
        short_description="VPN connection timeout",
        description="User cannot connect to corporate VPN",
    )
    assert is_incident_cluster_eligible(inc) is True


def test_is_incident_cluster_eligible_inactive() -> None:
    inc = SimpleNamespace(
        active=False,
        state="in_progress",
        ai_human_lock=False,
        short_description="VPN connection timeout",
        description="User cannot connect to corporate VPN",
    )
    assert is_incident_cluster_eligible(inc) is False


@pytest.mark.parametrize("state", ["closed", "resolved", "canceled", "cancelled", "6", "7", "8"])
def test_is_incident_cluster_eligible_terminal_states(state: str) -> None:
    inc = SimpleNamespace(
        active=True,
        state=state,
        ai_human_lock=False,
        short_description="VPN connection timeout",
        description="User cannot connect to corporate VPN",
    )
    assert is_incident_cluster_eligible(inc) is False


def test_is_incident_cluster_eligible_human_locked() -> None:
    inc = SimpleNamespace(
        active=True,
        state="in_progress",
        ai_human_lock=True,
        short_description="VPN connection timeout",
        description="User cannot connect to corporate VPN",
    )
    assert is_incident_cluster_eligible(inc) is False


def test_is_incident_cluster_eligible_empty_text() -> None:
    inc = SimpleNamespace(
        active=True,
        state="in_progress",
        ai_human_lock=False,
        short_description="",
        description="   ",
    )
    assert is_incident_cluster_eligible(inc) is False


def test_sanitize_incident_text_redacts_credentials_and_pii() -> None:
    raw_text = (
        "Server failed with error. Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-ID "
        "and password=SuperSecretPassword123! Contact admin@example.com at +1-555-555-0199."
    )
    sanitized = sanitize_incident_text(raw_text)
    assert "SuperSecretPassword123!" not in sanitized
    assert "eyJhbGciOi" not in sanitized
    assert "admin@example.com" not in sanitized
    assert "+1-555-555-0199" not in sanitized
    assert "***REDACTED***" in sanitized


def test_build_incident_signature_deterministic_format() -> None:
    inc = SimpleNamespace(
        service="Payment-Gateway",
        category="Network",
        subcategory="Firewall",
        short_description="Connection timed out after 30s",
        description="Outbound TCP connection to auth.internal:8443 dropped with ETIMEDOUT.",
        # Volatile fields that must NOT appear in signature:
        sys_id="0123456789abcdef0123456789abcdef",
        number="INC0012345",
        caller_id="john.doe@company.com",
    )
    sig = build_incident_signature(inc)

    lines = sig.splitlines()
    assert lines[0] == "Service: payment-gateway"
    assert lines[1] == "Category: network"
    assert lines[2] == "Subcategory: firewall"
    assert lines[3] == "Summary: Connection timed out after 30s"
    assert lines[4] == "Description: Outbound TCP connection to auth.internal:8443 dropped with ETIMEDOUT."

    assert "INC0012345" not in sig
    assert "0123456789abcdef" not in sig
    assert "john.doe" not in sig


def test_build_incident_signature_caps_description_at_500() -> None:
    long_desc = "x" * 1200
    inc = SimpleNamespace(
        service="db-service",
        category="software",
        short_description="Memory spike",
        description=long_desc,
    )
    sig = build_incident_signature(inc)
    desc_line = [line for line in sig.splitlines() if line.startswith("Description: ")][0]
    # "Description: " is 13 chars + 500 chars = 513 chars max
    assert len(desc_line) == 513
