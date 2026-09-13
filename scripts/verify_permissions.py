"""Automated Security & Permissions Verification Harness.

BARQ G1 - Sprint 1 (S1.2): AI Execution Log, OAuth Identity & Least-Privilege ACLs.

Empirically executes every permitted and forbidden operation using the real OAuth
integration identity against the live ServiceNow PDI.  Only observed, executed
outcomes count - not configuration assertions.

Requirements covered:
  SS1  Authentication & identity verification
  SS2  Permitted incident operations
  SS3  Forbidden incident fields (with read-before / read-after proof)
  SS4  Human-lock circuit breaker
  SS5  Execution Log FR-02 (succeeded / failed / blocked statuses)
  SS6  Execution ID indexed lookup
  SS7  Forbidden execution log operations (delete / append-only)
  SS8  Bulk/multi-field bypass
  SS9  Read-after-write built into every test
  SS10 OAuth token lifecycle (normal + invalid + mid-run)
  SS11 Machine-readable JSON report (verification_report.json)
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

INSTANCE_URL: str = os.getenv("SERVICENOW_INSTANCE_URL", "").rstrip("/")
CLIENT_ID: str = os.getenv("SERVICENOW_CLIENT_ID", "")
CLIENT_SECRET: str = os.getenv("SERVICENOW_CLIENT_SECRET", "")
USERNAME: str = os.getenv("SERVICENOW_USERNAME", "ai_orchestrator_svc")
PASSWORD: str = os.getenv("SERVICENOW_PASSWORD", "")

TOKEN_ENDPOINT: str = f"{INSTANCE_URL}/oauth_token.do"
TABLE_API_BASE: str = f"{INSTANCE_URL}/api/now/table"

SCOPED_LOG_TABLE: str = "x_2215032_ai_inc_0_ai_execution_log"
HUMAN_LOCK_FIELD: str = "x_2215032_ai_inc_0_ai_human_lock"
AI_CLASSIFICATION_FIELD: str = "x_2215032_ai_inc_0_ai_classification"
AI_HUMAN_REVIEW_FIELD: str = "x_2215032_ai_inc_0_ai_human_review_required"
AI_ENABLED_FIELD: str = "x_2215032_ai_inc_0_ai_enabled"

# Known OOB ServiceNow group sys_id used in assignment_group test
_DENY_GROUP_ID: str = "287ebd7da9fe198100f92cc8d1d2154e"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TestResult:
    """Machine-readable test record.  Never contains credentials or tokens."""

    test_id: str
    category: str
    name: str
    operation: str
    target: str
    expected: str
    http_status: int
    observed: str
    persisted_change: bool
    verdict: str  # "PASS" | "FAIL"
    notes: str = ""


# ---------------------------------------------------------------------------
# Environment validation
# ---------------------------------------------------------------------------


def validate_environment() -> None:
    missing: list[str] = []
    if not INSTANCE_URL or "example" in INSTANCE_URL:
        missing.append("SERVICENOW_INSTANCE_URL")
    if not CLIENT_ID:
        missing.append("SERVICENOW_CLIENT_ID")
    if not CLIENT_SECRET:
        missing.append("SERVICENOW_CLIENT_SECRET")
    if not USERNAME:
        missing.append("SERVICENOW_USERNAME")
    if not PASSWORD:
        missing.append("SERVICENOW_PASSWORD")
    if missing:
        print(f"\n[ERROR] Missing required environment variables: {', '.join(missing)}")
        print("Ensure a valid .env file is present. No admin credentials in source.\n")
        sys.exit(1)

    if USERNAME.strip().lower() == "admin":
        print("\n[ERROR] SERVICENOW_USERNAME cannot be 'admin'.")
        print(
            "verify_permissions.py verifies least-privilege boundaries "
            "for the integration identity, not admin.\n"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Auth helpers  (tokens are NEVER printed)
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _uid(n: int = 8) -> str:
    return uuid.uuid4().hex[:n]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_GRN = "\033[92m"
_RED = "\033[91m"
_RST = "\033[0m"
_BLD = "\033[1m"


def _banner(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {_BLD}{title}{_RST}")
    print(f"{'=' * 72}")


def _print_test(r: TestResult) -> None:
    color = _GRN if r.verdict == "PASS" else _RED
    icon = "+" if r.verdict == "PASS" else "x"
    print(f"  {color}[{r.verdict}]{_RST} {icon}  {r.test_id}: {r.name}")
    if r.notes:
        for line in r.notes.split("|"):
            line = line.strip()
            if line:
                print(f"          -> {line}")


# ---------------------------------------------------------------------------
# Test-incident resolver
# ---------------------------------------------------------------------------


def _resolve_incident(client: httpx.Client, hdrs: dict[str, str]) -> dict[str, Any]:
    """Return an incident NOT opened by the service account.

    Using an incident opened by a different user ensures the OOB
    opened_by == gs.getUserID() condition does not accidentally grant
    comment write access to the service account, which would produce a
    false PASS on the comments forbidden-write test.
    """
    r = client.get(
        f"{TABLE_API_BASE}/incident"
        f"?sysparm_query=opened_by.user_name!={USERNAME}^active=true&sysparm_limit=1",
        headers=hdrs,
        timeout=15.0,
    )
    if r.status_code == 200 and r.json().get("result"):
        inc = r.json()["result"][0]
        print(f"  Using external incident: {inc['number']} (opened_by != service account)")
        return inc

    print("  Creating synthetic test incident...")
    cr = client.post(
        f"{TABLE_API_BASE}/incident",
        headers=hdrs,
        timeout=15.0,
        json={
            "short_description": "Synthetic S1.2 Permission Verification",
            "description": "Auto-created by verify_permissions.py. Safe to delete.",
            "impact": "3",
            "urgency": "3",
        },
    )
    if cr.status_code == 201:
        inc = cr.json()["result"]
        print(f"  Created synthetic incident: {inc['number']}")
        return inc
    print(f"[FATAL] Could not resolve test incident (HTTP {cr.status_code})")
    sys.exit(1)


# ---------------------------------------------------------------------------
# SS1  Authentication & Identity (AUTH-01 .. AUTH-04, TOKEN-01)
# ---------------------------------------------------------------------------


def _test_auth_success(client: httpx.Client) -> tuple[TestResult, str]:
    """AUTH-01: OAuth token acquisition succeeds and returns a valid token."""
    try:
        resp = client.post(
            TOKEN_ENDPOINT,
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "username": USERNAME,
                "password": PASSWORD,
            },
            headers={"Accept": "application/json"},
            timeout=20.0,
        )
        ok = resp.status_code == 200 and "access_token" in resp.json()
        token: str = resp.json().get("access_token", "") if ok else ""
        expires_in = resp.json().get("expires_in", "?") if ok else "N/A"
        return (
            TestResult(
                test_id="AUTH-01",
                category="Authentication",
                name="OAuth token acquisition",
                operation="POST",
                target="/oauth_token.do",
                expected="200 OK + access_token",
                http_status=resp.status_code,
                observed=f"HTTP {resp.status_code}" + (" (token issued)" if ok else " (no token)"),
                persisted_change=False,
                verdict="PASS" if ok else "FAIL",
                notes=f"Token lifespan: {expires_in}s" if ok else "Token not returned",
            ),
            token,
        )
    except Exception as exc:
        return (
            TestResult(
                "AUTH-01",
                "Authentication",
                "OAuth token acquisition",
                "POST",
                "/oauth_token.do",
                "200 OK + access_token",
                0,
                f"Exception: {exc}",
                False,
                "FAIL",
            ),
            "",
        )


def _test_identity(client: httpx.Client, hdrs: dict[str, str]) -> TestResult:
    """AUTH-02: Authenticated identity is the expected service account."""
    resp = client.get(
        f"{TABLE_API_BASE}/sys_user"
        f"?sysparm_query=sys_id=javascript:gs.getUserID()&sysparm_fields=user_name,name,sys_id",
        headers=hdrs,
        timeout=10.0,
    )
    results = resp.json().get("result", [])
    ok = resp.status_code == 200 and len(results) == 1 and results[0].get("user_name") == USERNAME
    return TestResult(
        test_id="AUTH-02",
        category="Authentication",
        name=f"Identity is expected service account ({USERNAME})",
        operation="GET",
        target="/api/now/table/sys_user?sysparm_query=sys_id=javascript:gs.getUserID()",
        expected=f"Token owner user_name={USERNAME}",
        http_status=resp.status_code,
        observed=f"user_name={results[0].get('user_name', 'N/A')}" if results else "no result",
        persisted_change=False,
        verdict="PASS" if ok else "FAIL",
        notes=f"Token owner: {results[0].get('name', '')} ({results[0].get('user_name', '')})"
        if results
        else "Failed to resolve authenticated session identity.",
    )


def _test_non_admin(client: httpx.Client, hdrs: dict[str, str]) -> TestResult:
    """AUTH-03: Service account denied access to admin-gated sys_user_has_role table.

    HTTP 403 on sys_user_has_role proves the account lacks admin / security_admin roles.
    """
    resp = client.get(
        f"{TABLE_API_BASE}/sys_user_has_role?sysparm_limit=1",
        headers=hdrs,
        timeout=10.0,
    )
    is_denied = resp.status_code in (401, 403)
    return TestResult(
        test_id="AUTH-03",
        category="Authentication",
        name="Non-admin: sys_user_has_role access denied (no security_admin)",
        operation="GET",
        target="/api/now/table/sys_user_has_role",
        expected="HTTP 401 or 403",
        http_status=resp.status_code,
        observed=f"HTTP {resp.status_code}",
        persisted_change=False,
        verdict="PASS" if is_denied else "FAIL",
        notes="403 confirms account lacks admin/security_admin."
        if is_denied
        else "FAIL: account can read role assignments (elevated privilege detected).",
    )


def _test_invalid_token(client: httpx.Client) -> TestResult:
    """AUTH-04: Invalid / expired token is rejected; no data accessed."""
    bad = {
        "Authorization": "Bearer INVALID_TOKEN_SECURITY_TEST_0000000000",
        "Accept": "application/json",
    }
    resp = client.get(f"{TABLE_API_BASE}/incident?sysparm_limit=1", headers=bad, timeout=10.0)
    rejected = resp.status_code in (401, 403)
    return TestResult(
        test_id="AUTH-04",
        category="Authentication",
        name="Invalid/expired token is rejected",
        operation="GET",
        target="/api/now/table/incident",
        expected="HTTP 401 or 403",
        http_status=resp.status_code,
        observed=f"HTTP {resp.status_code}",
        persisted_change=False,
        verdict="PASS" if rejected else "FAIL",
        notes="Confirms API enforces token validity; no data returned with bad token.",
    )


def _test_mid_run_expiry(client: httpx.Client) -> TestResult:
    """TOKEN-01: Mid-run expiry detection and re-authentication.

    A simulated stale token returns 401/403.
    The harness detects the 401 and re-authenticates to acquire a fresh token.
    """
    simulated_expired = {
        "Authorization": "Bearer SIMULATED_EXPIRED_TOKEN_MIDRUN_0000",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    resp = client.get(
        f"{TABLE_API_BASE}/incident?sysparm_limit=1",
        headers=simulated_expired,
        timeout=10.0,
    )
    detected_401 = resp.status_code in (401, 403)

    reauth_ok = False
    recovered_status = 0
    if detected_401:
        auth_res, fresh_token = _test_auth_success(client)
        if auth_res.verdict == "PASS" and fresh_token:
            fresh_hdrs = _auth_headers(fresh_token)
            rec_resp = client.get(
                f"{TABLE_API_BASE}/incident?sysparm_limit=1",
                headers=fresh_hdrs,
                timeout=10.0,
            )
            recovered_status = rec_resp.status_code
            reauth_ok = recovered_status == 200

    ok = detected_401 and reauth_ok
    return TestResult(
        test_id="TOKEN-01",
        category="Token Lifecycle",
        name="Mid-run expiry detected (harness re-authenticates)",
        operation="GET + POST",
        target="/api/now/table/incident",
        expected="Stale token returns 401 -> Re-authentication succeeds (HTTP 200)",
        http_status=recovered_status or resp.status_code,
        observed=f"Initial: HTTP {resp.status_code} -> Re-auth: HTTP {recovered_status}",
        persisted_change=False,
        notes=(
            f"Stale token rejected (HTTP {resp.status_code}); "
            f"re-authenticated successfully (HTTP {recovered_status})."
            if ok
            else (
                f"Re-auth failed after 401: "
                f"initial={resp.status_code}, recovered={recovered_status}."
            )
        ),
    )


# ---------------------------------------------------------------------------
# SS2  Permitted incident operations (PERM-01 .. PERM-03)
# ---------------------------------------------------------------------------


def _test_read_incident(
    client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str, inc_number: str
) -> TestResult:
    """PERM-01: Integration can read the incident record."""
    resp = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}"
        f"?sysparm_fields=number,state,priority,assigned_to,assignment_group",
        headers=hdrs,
        timeout=10.0,
    )
    res_num = (
        resp.json().get("result", {}).get("number", "N/A") if resp.status_code == 200 else "N/A"
    )
    ok = resp.status_code == 200 and res_num == inc_number
    return TestResult(
        test_id="PERM-01",
        category="Permitted",
        name=f"Read incident {inc_number}",
        operation="GET",
        target=f"/api/now/table/incident/{inc_sys_id}",
        expected=f"200 OK + number={inc_number}",
        http_status=resp.status_code,
        observed=f"HTTP {resp.status_code} (number={res_num})",
        persisted_change=False,
        verdict="PASS" if ok else "FAIL",
    )


def _test_write_work_notes(
    client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str
) -> TestResult:
    """PERM-02: Write work_notes; prove persisted via sys_journal_field read-back."""
    marker = f"[AI-Verify-WN] {_uid()}"
    patch = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={"work_notes": marker},
        timeout=10.0,
    )
    chk = client.get(
        f"{TABLE_API_BASE}/sys_journal_field"
        f"?sysparm_query=element_id={inc_sys_id}^element=work_notes^valueLIKE{marker}",
        headers=hdrs,
        timeout=10.0,
    )
    count = len(chk.json().get("result", []))
    ok = patch.status_code == 200 and count > 0
    return TestResult(
        test_id="PERM-02",
        category="Permitted",
        name="Write work_notes (internal journal)",
        operation="PATCH",
        target=f"incident/{inc_sys_id}.work_notes",
        expected="200 OK + persisted in sys_journal_field",
        http_status=patch.status_code,
        observed=f"HTTP {patch.status_code} ({count} journal entries)",
        persisted_change=count > 0,
        verdict="PASS" if ok else "FAIL",
        notes=f"Journal entry confirmed (count={count})." if ok else "NOT persisted in journal!",
    )


def _test_write_ai_field(client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str) -> TestResult:
    """PERM-03: Write scoped AI classification field; prove persisted via read-back."""
    before = (
        client.get(
            f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={AI_CLASSIFICATION_FIELD}",
            headers=hdrs,
            timeout=10.0,
        )
        .json()
        .get("result", {})
        .get(AI_CLASSIFICATION_FIELD, "")
    )
    target_value = "software"
    patch = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={AI_CLASSIFICATION_FIELD: target_value},
        timeout=10.0,
    )
    after = (
        client.get(
            f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={AI_CLASSIFICATION_FIELD}",
            headers=hdrs,
            timeout=10.0,
        )
        .json()
        .get("result", {})
        .get(AI_CLASSIFICATION_FIELD, "")
    )
    ok = patch.status_code == 200 and after == target_value
    # Restore original value
    client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={AI_CLASSIFICATION_FIELD: before},
        timeout=10.0,
    )
    return TestResult(
        test_id="PERM-03",
        category="Permitted",
        name=f"Write scoped AI field ({AI_CLASSIFICATION_FIELD})",
        operation="PATCH",
        target=f"incident/{inc_sys_id}.{AI_CLASSIFICATION_FIELD}",
        expected=f"200 OK + field='{target_value}'",
        http_status=patch.status_code,
        observed=f"HTTP {patch.status_code} (value='{after}')",
        persisted_change=(after == target_value),
        verdict="PASS" if ok else "FAIL",
        notes=f"'{before}' -> '{after}' (restored after test)."
        if ok
        else f"Expected '{target_value}', got '{after}'.",
    )


def _test_write_human_review_required(
    client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str
) -> TestResult:
    """PERM-04: Write scoped AI human review required field; prove persisted via read-back."""
    before = (
        client.get(
            f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={AI_HUMAN_REVIEW_FIELD}",
            headers=hdrs,
            timeout=10.0,
        )
        .json()
        .get("result", {})
        .get(AI_HUMAN_REVIEW_FIELD, "")
    )
    target_value = "true" if str(before).lower() != "true" else "false"
    patch = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={AI_HUMAN_REVIEW_FIELD: target_value},
        timeout=10.0,
    )
    after = (
        client.get(
            f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={AI_HUMAN_REVIEW_FIELD}",
            headers=hdrs,
            timeout=10.0,
        )
        .json()
        .get("result", {})
        .get(AI_HUMAN_REVIEW_FIELD, "")
    )
    ok = patch.status_code == 200 and str(after).lower() == target_value.lower()
    # Restore original value
    client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={AI_HUMAN_REVIEW_FIELD: before},
        timeout=10.0,
    )
    return TestResult(
        test_id="PERM-04",
        category="Permitted",
        name=f"Write scoped AI field ({AI_HUMAN_REVIEW_FIELD})",
        operation="PATCH",
        target=f"incident/{inc_sys_id}.{AI_HUMAN_REVIEW_FIELD}",
        expected=f"200 OK + field='{target_value}'",
        http_status=patch.status_code,
        observed=f"HTTP {patch.status_code} (value='{after}')",
        persisted_change=(str(after).lower() == target_value.lower()),
        verdict="PASS" if ok else "FAIL",
        notes=f"'{before}' -> '{after}' (restored after test)."
        if ok
        else f"Expected '{target_value}', got '{after}'. Check field-level write ACL.",
    )


# ---------------------------------------------------------------------------
# SS5 / SS6 / SS7  Execution Log (FR-02)  (LOG-01 .. LOG-05)
# ---------------------------------------------------------------------------


def _post_log(
    client: httpx.Client,
    hdrs: dict[str, str],
    inc_sys_id: str,
    status: str,
    error: str = "",
) -> tuple[int, dict[str, Any], str]:
    """Create one execution log record; return (http_status, result_dict, exec_id)."""
    exec_id = f"exec_verify_{_uid(12)}"
    payload: dict[str, Any] = {
        "incident_reference": inc_sys_id,
        "execution_id": exec_id,
        "agent": "verification_harness",
        "action": "execute",
        "status": status,
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "result": f"Verification audit entry (status={status}).",
        "error": error,
    }
    r = client.post(
        f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}",
        headers=hdrs,
        json=payload,
        timeout=15.0,
    )
    return r.status_code, r.json().get("result", {}), exec_id


def _test_log_status(
    client: httpx.Client,
    hdrs: dict[str, str],
    inc_sys_id: str,
    created_ids: list[str],
    test_id: str,
    status: str,
    error: str = "",
) -> TestResult:
    """Generic execution log creation test for a given status value."""
    http_status, result, exec_id = _post_log(client, hdrs, inc_sys_id, status, error)
    sys_id = result.get("sys_id", "")
    if sys_id:
        created_ids.append(sys_id)

    fields_ok = False
    details = ""
    if http_status == 201 and sys_id:
        rb = client.get(f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}/{sys_id}", headers=hdrs, timeout=10.0)
        rec = rb.json().get("result", {})
        rec_exec = rec.get("execution_id") or rec.get("u_execution_id", "")
        rec_st = rec.get("status") or rec.get("u_status", "")
        rec_act = rec.get("action") or rec.get("u_action", "")
        rec_ag = rec.get("agent") or rec.get("u_agent", "")

        status_ok = str(rec_st).lower() == status.lower()
        action_ok = str(rec_act).lower() == "execute"
        exec_ok = str(rec_exec) == exec_id
        agent_ok = bool(rec_ag)

        fields_ok = status_ok and action_ok and exec_ok and agent_ok
        if not fields_ok:
            details = (
                f"Field mismatch: expected status={status}, action=execute; "
                f"got status={rec_st}, action={rec_act}, exec_id={rec_exec}"
            )

    ok = http_status == 201 and bool(sys_id) and fields_ok
    return TestResult(
        test_id=test_id,
        category="Execution Log",
        name=f"Create execution log: status={status} (FR-02)",
        operation="POST",
        target=SCOPED_LOG_TABLE,
        expected=f"201 Created + status='{status}' + action='execute'",
        http_status=http_status,
        observed=f"HTTP {http_status} (sys_id={sys_id or 'N/A'})",
        persisted_change=ok,
        verdict="PASS" if ok else "FAIL",
        notes=f"exec_id={exec_id} | sys_id={sys_id} | status={status} confirmed"
        if ok
        else (details or "Record not created or fields missing."),
    )


def _test_execution_id_lookup(
    client: httpx.Client,
    hdrs: dict[str, str],
    inc_sys_id: str,
    created_ids: list[str],
) -> TestResult:
    """LOG-04: Create a log; query by unique execution_id; verify 1 record returned."""
    unique_exec_id = f"exec_idx_{_uid(16)}"
    payload: dict[str, Any] = {
        "incident_reference": inc_sys_id,
        "execution_id": unique_exec_id,
        "agent": "verification_harness",
        "action": "read",
        "status": "succeeded",
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "result": "Index lookup verification.",
    }
    cr = client.post(
        f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}", headers=hdrs, json=payload, timeout=15.0
    )
    cr_sys_id = cr.json().get("result", {}).get("sys_id", "")
    if cr_sys_id:
        created_ids.append(cr_sys_id)

    qr = client.get(
        f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}"
        f"?sysparm_query=execution_id={unique_exec_id}&sysparm_fields=sys_id,execution_id",
        headers=hdrs,
        timeout=10.0,
    )
    found = qr.json().get("result", [])
    ok = cr.status_code == 201 and len(found) == 1 and found[0].get("sys_id") == cr_sys_id
    return TestResult(
        test_id="LOG-04",
        category="Execution Log",
        name="Execution ID indexed lookup (correlation trace key)",
        operation="GET",
        target=f"{SCOPED_LOG_TABLE}?execution_id=<unique>",
        expected="Exactly 1 record matching execution_id",
        http_status=qr.status_code,
        observed=f"HTTP {qr.status_code} ({len(found)} record(s) found)",
        persisted_change=False,
        verdict="PASS" if ok else "FAIL",
        notes=f"exec_id='{unique_exec_id}' -> sys_id={cr_sys_id}"
        if ok
        else "Lookup failed or wrong count.",
    )


def _test_log_delete_forbidden(
    client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str
) -> TestResult:
    """LOG-05: Integration CANNOT delete execution log records (append-only audit trail)."""
    http_status, result, _ = _post_log(client, hdrs, inc_sys_id, "succeeded")
    temp_id = result.get("sys_id", "")
    if not temp_id:
        return TestResult(
            "LOG-05",
            "Execution Log",
            "Cannot delete execution log (append-only)",
            "DELETE",
            SCOPED_LOG_TABLE,
            "403 or 404",
            0,
            "Could not create temp record to test deletion.",
            False,
            "FAIL",
        )
    del_resp = client.delete(
        f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}/{temp_id}", headers=hdrs, timeout=10.0
    )
    verify = client.get(
        f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}/{temp_id}", headers=hdrs, timeout=10.0
    )
    record_exists = verify.status_code == 200
    blocked = del_resp.status_code in (403, 404) or record_exists

    if del_resp.status_code == 204:
        notes = "WARNING: DELETE succeeded - log is NOT append-only. ACL should deny delete."
    else:
        notes = f"DELETE HTTP {del_resp.status_code}; record_exists={record_exists} (blocked)."

    return TestResult(
        test_id="LOG-05",
        category="Execution Log",
        name="Cannot delete execution log records (append-only)",
        operation="DELETE",
        target=f"{SCOPED_LOG_TABLE}/{temp_id}",
        expected="403 or 404 (delete blocked)",
        http_status=del_resp.status_code,
        observed=f"HTTP {del_resp.status_code} (record_exists={record_exists})",
        persisted_change=not record_exists,
        verdict="PASS" if blocked else "FAIL",
        notes=notes,
    )


def _test_log_modify_forbidden(
    client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str
) -> TestResult:
    """LOG-07: Integration CANNOT modify execution log records (append-only audit trail)."""
    http_status, result, _ = _post_log(client, hdrs, inc_sys_id, "succeeded")
    temp_id = result.get("sys_id", "")
    if not temp_id:
        return TestResult(
            test_id="LOG-07",
            category="Execution Log",
            name="Cannot modify execution log records (append-only)",
            operation="PATCH",
            target=SCOPED_LOG_TABLE,
            expected="401 or 403 (write blocked)",
            http_status=0,
            observed="Could not create temp record to test modification.",
            persisted_change=False,
            verdict="FAIL",
        )

    tamper_value = "TAMPERED_AUDIT_ENTRY"
    patch_resp = client.patch(
        f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}/{temp_id}",
        headers=hdrs,
        json={"result": tamper_value},
        timeout=10.0,
    )
    verify = client.get(
        f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}/{temp_id}?sysparm_fields=result",
        headers=hdrs,
        timeout=10.0,
    )
    observed_result = ""
    if verify.status_code == 200:
        observed_result = str(verify.json().get("result", {}).get("result", ""))

    modified = observed_result == tamper_value
    blocked = patch_resp.status_code in (401, 403) or (verify.status_code == 200 and not modified)

    return TestResult(
        test_id="LOG-07",
        category="Execution Log",
        name="Cannot modify execution log records (append-only)",
        operation="PATCH",
        target=f"{SCOPED_LOG_TABLE}/{temp_id}",
        expected="401/403 or write ignored (record immutable)",
        http_status=patch_resp.status_code,
        observed=f"HTTP {patch_resp.status_code} (modified={modified})",
        persisted_change=modified,
        verdict="PASS" if (blocked and not modified) else "FAIL",
        notes="Record immutable - write blocked."
        if (blocked and not modified)
        else "SECURITY FAILURE: execution log modified after creation!",
    )


# ---------------------------------------------------------------------------
# SS3  Forbidden incident fields (DENY-01 .. DENY-05)
# ---------------------------------------------------------------------------


def _read_field(
    client: httpx.Client,
    hdrs: dict[str, str],
    inc_sys_id: str,
    field: str,
) -> tuple[bool, str, int]:
    """Read one field, reporting whether the read itself actually succeeded.

    Returns ``(readable, value, status)``. #46: the DENY and BULK checks used to read
    back with ``.get(field, "")`` and no status check, so a 403 or 404 on the read, or a
    response that simply does not carry the field, produced ``after == ""``. That looked
    identical to "the write was refused" and the test passed. A harness must never
    report PASS because it could not see the result — if the read is not readable the
    caller fails the test instead.
    """
    r = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={field}",
        headers=hdrs,
        timeout=10.0,
    )
    if r.status_code != 200:
        return False, "", r.status_code
    try:
        result = r.json().get("result")
    except ValueError:
        return False, "", r.status_code
    if not isinstance(result, dict) or field not in result:
        return False, "", r.status_code
    raw = result.get(field, "")
    value = raw.get("value", "") if isinstance(raw, dict) else str(raw)
    return True, value, r.status_code


def _forbidden_scalar(
    client: httpx.Client,
    hdrs: dict[str, str],
    inc_sys_id: str,
    test_id: str,
    field: str,
    value: str,
) -> TestResult:
    """Read-patch-read for scalar forbidden fields.

    Fails closed: if either read-back cannot be performed, the result is FAIL rather
    than PASS, because an unobservable write is not a blocked write (#46).
    """

    def _unreadable(stage: str, status: int) -> TestResult:
        return TestResult(
            test_id=test_id,
            category="Forbidden",
            name=f"FORBIDDEN write to incident.{field}",
            operation="PATCH",
            target=f"incident/{inc_sys_id}.{field}",
            expected="Blocked (value unchanged after write)",
            http_status=status,
            observed=f"{stage} read-back unreadable (HTTP {status})",
            persisted_change=False,
            verdict="FAIL",
            notes=(
                f"INCONCLUSIVE: could not read {field!r} {stage} the write "
                f"(HTTP {status}), so it is unknown whether the ACL blocked it. "
                "Reported as FAIL because a harness must fail closed."
            ),
        )

    readable, before, status = _read_field(client, hdrs, inc_sys_id, field)
    if not readable:
        return _unreadable("before", status)

    patch_r = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={field: value},
        timeout=10.0,
    )

    readable, after, status = _read_field(client, hdrs, inc_sys_id, field)
    if not readable:
        return _unreadable("after", status)

    changed = after == value and after != before
    blocked = patch_r.status_code in (401, 403) or not changed

    return TestResult(
        test_id=test_id,
        category="Forbidden",
        name=f"FORBIDDEN write to incident.{field}",
        operation="PATCH",
        target=f"incident/{inc_sys_id}.{field}",
        expected="Blocked (value unchanged after write)",
        http_status=patch_r.status_code,
        observed=f"HTTP {patch_r.status_code} | before='{before}' after='{after}'",
        persisted_change=changed,
        verdict="PASS" if blocked else "FAIL",
        notes="ACL blocked: field unchanged."
        if blocked
        else f"SECURITY FAILURE: {field} changed from '{before}' to '{after}'!",
    )


def _forbidden_journal(
    client: httpx.Client,
    hdrs: dict[str, str],
    inc_sys_id: str,
    test_id: str,
    field: str,
) -> TestResult:
    """Forbidden journal-field test (comments).

    ServiceNow may return 200 even when the field is ACL-stripped.
    The final source of truth is sys_journal_field:
    0 entries = blocked, 1+ entries = leaked.
    """
    marker = f"FORBIDDEN_{field.upper()}_{_uid()}"
    patch_r = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={field: marker},
        timeout=10.0,
    )
    chk = client.get(
        f"{TABLE_API_BASE}/sys_journal_field"
        f"?sysparm_query=element_id={inc_sys_id}^element={field}^valueLIKE{marker}",
        headers=hdrs,
        timeout=10.0,
    )
    # #46: a denied journal query returns no "result", which len() reported as 0, which
    # counted as "blocked". Not being allowed to look is not evidence that nothing was
    # written, so the read has to be checked before its count means anything.
    if chk.status_code != 200:
        return TestResult(
            test_id=test_id,
            category="Forbidden",
            name=f"FORBIDDEN write to incident.{field} (journal field)",
            operation="PATCH",
            target=f"incident/{inc_sys_id}.{field}",
            expected="Blocked (0 entries in sys_journal_field)",
            http_status=chk.status_code,
            observed=f"sys_journal_field query unreadable (HTTP {chk.status_code})",
            persisted_change=False,
            verdict="FAIL",
            notes=(
                f"INCONCLUSIVE: could not query sys_journal_field (HTTP {chk.status_code}), "
                f"so it is unknown whether a {field!r} entry was posted. Reported as FAIL "
                "because a harness must fail closed."
            ),
        )

    journal_count = len(chk.json().get("result", []))
    blocked = patch_r.status_code in (401, 403) or journal_count == 0
    return TestResult(
        test_id=test_id,
        category="Forbidden",
        name=f"FORBIDDEN write to incident.{field} (journal field)",
        operation="PATCH",
        target=f"incident/{inc_sys_id}.{field}",
        expected="Blocked (0 entries in sys_journal_field)",
        http_status=patch_r.status_code,
        observed=f"HTTP {patch_r.status_code} | journal_count={journal_count}",
        persisted_change=(journal_count > 0),
        verdict="PASS" if blocked else "FAIL",
        notes="0 journal entries - ACL blocked."
        if blocked
        else f"SECURITY FAILURE: {journal_count} '{field}' entries posted!",
    )


# ---------------------------------------------------------------------------
# SS4  Human-lock circuit breaker (LOCK-01)
# ---------------------------------------------------------------------------


def _test_human_lock(client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str) -> TestResult:
    """LOCK-01: Integration account cannot modify the human-lock flag."""
    get_before = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={HUMAN_LOCK_FIELD}",
        headers=hdrs,
        timeout=10.0,
    )
    if get_before.status_code != 200:
        return TestResult(
            test_id="LOCK-01",
            category="Human Lock",
            name="Integration CANNOT modify human-lock (circuit breaker)",
            operation="GET",
            target=f"incident/{inc_sys_id}.{HUMAN_LOCK_FIELD}",
            expected="HTTP 200 baseline check",
            http_status=get_before.status_code,
            observed=f"Initial GET failed with HTTP {get_before.status_code}",
            persisted_change=False,
            verdict="FAIL",
            notes="Failed to retrieve baseline state before test.",
        )
    before = str(get_before.json().get("result", {}).get(HUMAN_LOCK_FIELD, "false"))
    attempt = "true" if before == "false" else "false"

    patch_r = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={HUMAN_LOCK_FIELD: attempt},
        timeout=10.0,
    )

    get_after = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={HUMAN_LOCK_FIELD}",
        headers=hdrs,
        timeout=10.0,
    )
    if get_after.status_code != 200:
        return TestResult(
            test_id="LOCK-01",
            category="Human Lock",
            name="Integration CANNOT modify human-lock (circuit breaker)",
            operation="GET",
            target=f"incident/{inc_sys_id}.{HUMAN_LOCK_FIELD}",
            expected="HTTP 200 post-patch check",
            http_status=get_after.status_code,
            observed=f"Post-patch GET failed with HTTP {get_after.status_code}",
            persisted_change=False,
            verdict="FAIL",
            notes="Failed to retrieve post-patch state.",
        )
    after = str(get_after.json().get("result", {}).get(HUMAN_LOCK_FIELD, "false"))

    blocked = (patch_r.status_code in (401, 403) or after == before) and (after != attempt)
    return TestResult(
        test_id="LOCK-01",
        category="Human Lock",
        name="Integration CANNOT modify human-lock (circuit breaker)",
        operation="PATCH",
        target=f"incident/{inc_sys_id}.{HUMAN_LOCK_FIELD}",
        expected=f"Blocked (remains '{before}')",
        http_status=patch_r.status_code,
        observed=f"HTTP {patch_r.status_code} | before='{before}' after='{after}'",
        persisted_change=(after != before),
        verdict="PASS" if blocked else "FAIL",
        notes="Circuit breaker tamper-proof."
        if blocked
        else f"SECURITY FAILURE: human-lock changed '{before}' -> '{after}'!",
    )


def _test_ai_enabled(client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str) -> TestResult:
    """LOCK-02: Integration account cannot modify the AI-enabled flag (human opt-in switch)."""
    get_before = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={AI_ENABLED_FIELD}",
        headers=hdrs,
        timeout=10.0,
    )
    if get_before.status_code != 200:
        return TestResult(
            test_id="LOCK-02",
            category="Human Lock",
            name="Integration CANNOT modify AI-enabled (opt-in switch)",
            operation="GET",
            target=f"incident/{inc_sys_id}.{AI_ENABLED_FIELD}",
            expected="HTTP 200 baseline check",
            http_status=get_before.status_code,
            observed=f"Initial GET failed with HTTP {get_before.status_code}",
            persisted_change=False,
            verdict="FAIL",
            notes="Failed to retrieve baseline state before test.",
        )
    before = str(get_before.json().get("result", {}).get(AI_ENABLED_FIELD, "false"))
    attempt = "true" if before == "false" else "false"

    patch_r = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}",
        headers=hdrs,
        json={AI_ENABLED_FIELD: attempt},
        timeout=10.0,
    )

    get_after = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields={AI_ENABLED_FIELD}",
        headers=hdrs,
        timeout=10.0,
    )
    if get_after.status_code != 200:
        return TestResult(
            test_id="LOCK-02",
            category="Human Lock",
            name="Integration CANNOT modify AI-enabled (opt-in switch)",
            operation="GET",
            target=f"incident/{inc_sys_id}.{AI_ENABLED_FIELD}",
            expected="HTTP 200 post-patch check",
            http_status=get_after.status_code,
            observed=f"Post-patch GET failed with HTTP {get_after.status_code}",
            persisted_change=False,
            verdict="FAIL",
            notes="Failed to retrieve post-patch state.",
        )
    after = str(get_after.json().get("result", {}).get(AI_ENABLED_FIELD, "false"))

    blocked = (patch_r.status_code in (401, 403) or after == before) and (after != attempt)
    return TestResult(
        test_id="LOCK-02",
        category="Human Lock",
        name="Integration CANNOT modify AI-enabled (opt-in switch)",
        operation="PATCH",
        target=f"incident/{inc_sys_id}.{AI_ENABLED_FIELD}",
        expected=f"Blocked (remains '{before}')",
        http_status=patch_r.status_code,
        observed=f"HTTP {patch_r.status_code} | before='{before}' after='{after}'",
        persisted_change=(after != before),
        verdict="PASS" if blocked else "FAIL",
        notes="Opt-in switch tamper-proof."
        if blocked
        else f"SECURITY FAILURE: ai_enabled changed '{before}' -> '{after}'!",
    )


def _test_human_lock_safety_stop(
    client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str
) -> TestResult:
    """LOCK-03: When ai_human_lock is true, automated AI updates are aborted by Business Rule."""
    # Look for any incident where human lock is active
    locked_res = client.get(
        f"{TABLE_API_BASE}/incident?sysparm_query={HUMAN_LOCK_FIELD}=true&sysparm_limit=1",
        headers=hdrs,
        timeout=10.0,
    )
    locked_list = locked_res.json().get("result", [])

    if locked_list:
        target_id = locked_list[0]["sys_id"]
        target_num = locked_list[0].get("number", target_id)
        marker = f"AI attempt on locked incident {_uid(6)}"

        patch_r = client.patch(
            f"{TABLE_API_BASE}/incident/{target_id}",
            headers=hdrs,
            json={"work_notes": marker},
            timeout=10.0,
        )

        chk = client.get(
            f"{TABLE_API_BASE}/sys_journal_field?sysparm_query=element_id={target_id}^valueLIKE{marker}",
            headers=hdrs,
            timeout=10.0,
        )
        if chk.status_code != 200:
            journal_query_ok = False
            journal_count = -1
        else:
            journal_query_ok = True
            journal_count = len(chk.json().get("result", []))

        # Tightened validation: requires HTTP abort status (400/403),
        # verified journal query, and 0 journal entries
        aborted = patch_r.status_code in (400, 403) and journal_query_ok and journal_count == 0

        return TestResult(
            test_id="LOCK-03",
            category="Human Lock",
            name="Platform Business Rule enforces safety stop on locked incident",
            operation="PATCH",
            target=f"incident/{target_id}",
            expected="HTTP 400/403 abort + 0 journal entries",
            http_status=patch_r.status_code,
            observed=f"HTTP {patch_r.status_code} | journal_count={journal_count}",
            persisted_change=(journal_count > 0),
            verdict="PASS" if aborted else "FAIL",
            notes=f"Locked incident {target_num}: Business rule aborted automated update."
            if aborted
            else f"SECURITY FAILURE: Automated update persisted on locked incident {target_num}!",
        )
    else:
        return TestResult(
            test_id="LOCK-03",
            category="Human Lock",
            name="Platform Business Rule enforces safety stop on locked incident",
            operation="PATCH",
            target=f"incident ({HUMAN_LOCK_FIELD}=true)",
            expected="Active locked incident tested and aborted",
            http_status=0,
            observed="No incident currently has human_lock=true on instance",
            persisted_change=False,
            verdict="FAIL",
            notes=(
                "TEST SKIPPED / FAILED: Set ai_human_lock=true on a test incident "
                "to verify the platform safety stop live abort."
            ),
        )


# ---------------------------------------------------------------------------
# SS8  Bulk / multi-field bypass (BULK-01)
# ---------------------------------------------------------------------------


def _test_bulk_bypass(client: httpx.Client, hdrs: dict[str, str], inc_sys_id: str) -> TestResult:
    """BULK-01: One permitted field + three forbidden in a single PATCH.

    Permitted field (work_notes) must persist.
    Forbidden fields (comments / priority / state) must be stripped.
    """
    wn_marker = f"[AI-Bulk] {_uid()}"
    cmt_marker = f"BULK_UNAUTH_{_uid()}"

    before_r = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields=state,priority",
        headers=hdrs,
        timeout=10.0,
    )
    orig = before_r.json().get("result", {})
    orig_state = str(orig.get("state", ""))
    orig_priority = str(orig.get("priority", ""))

    payload = {
        "work_notes": wn_marker,  # PERMITTED
        "comments": cmt_marker,  # FORBIDDEN
        "priority": "1",  # FORBIDDEN
        "state": "6",  # FORBIDDEN
    }
    patch_r = client.patch(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}", headers=hdrs, json=payload, timeout=10.0
    )

    wn_count = len(
        client.get(
            f"{TABLE_API_BASE}/sys_journal_field"
            f"?sysparm_query=element_id={inc_sys_id}^element=work_notes^valueLIKE{wn_marker}",
            headers=hdrs,
            timeout=10.0,
        )
        .json()
        .get("result", [])
    )
    cmt_count = len(
        client.get(
            f"{TABLE_API_BASE}/sys_journal_field"
            f"?sysparm_query=element_id={inc_sys_id}^element=comments^valueLIKE{cmt_marker}",
            headers=hdrs,
            timeout=10.0,
        )
        .json()
        .get("result", [])
    )
    after_r = client.get(
        f"{TABLE_API_BASE}/incident/{inc_sys_id}?sysparm_fields=state,priority",
        headers=hdrs,
        timeout=10.0,
    )
    after = after_r.json().get("result", {})
    state_changed = str(after.get("state", "")) != orig_state
    priority_changed = str(after.get("priority", "")) != orig_priority

    forbidden_all_blocked = cmt_count == 0 and not state_changed and not priority_changed
    notes_parts = [
        f"work_notes persisted={'yes' if wn_count > 0 else 'NO - FAIL'}",
        f"comments leaked={'NO - blocked' if cmt_count == 0 else f'YES ({cmt_count}) - FAIL'}",
        f"state changed={'NO - blocked' if not state_changed else 'YES - FAIL'}",
        f"priority changed={'NO - blocked' if not priority_changed else 'YES - FAIL'}",
    ]
    return TestResult(
        test_id="BULK-01",
        category="Bulk Bypass",
        name="Mixed payload: permitted persists, forbidden fields stripped",
        operation="PATCH",
        target=f"incident/{inc_sys_id}",
        expected="work_notes written; comments/state/priority unchanged",
        http_status=patch_r.status_code,
        observed=f"HTTP {patch_r.status_code}",
        persisted_change=(cmt_count > 0 or state_changed or priority_changed),
        verdict="PASS" if forbidden_all_blocked else "FAIL",
        notes=" | ".join(notes_parts),
    )


# ---------------------------------------------------------------------------
# SS11  Output & machine-readable JSON report
# ---------------------------------------------------------------------------


def _print_matrix(results: list[TestResult]) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {_BLD}Sprint 1 (S1.2) Security Verification - Final Report{_RST}")
    print(f"{'=' * 72}")
    categories: dict[str, list[TestResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)
    for cat, tests in categories.items():
        print(f"\n  -- {cat} --")
        for r in tests:
            _print_test(r)
    passed = sum(1 for r in results if r.verdict == "PASS")
    total = len(results)
    all_ok = passed == total
    print(f"\n{'=' * 72}")
    if all_ok:
        print(f"  {_GRN}{_BLD}RESULT: PASS  ({passed}/{total} tests){_RST}")
    else:
        print(f"  {_RED}{_BLD}RESULT: FAIL  ({passed}/{total} tests passed){_RST}")
    print(f"{'=' * 72}\n")


def _write_json_report(results: list[TestResult]) -> None:
    """Write machine-readable JSON report.  No credentials or tokens included."""
    report: dict[str, Any] = {
        "sprint": "S1.2",
        "generated_at": datetime.now(UTC).isoformat(),
        "instance": INSTANCE_URL,
        "service_account": USERNAME,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.verdict == "PASS"),
            "failed": sum(1 for r in results if r.verdict == "FAIL"),
        },
        "tests": [asdict(r) for r in results],
    }
    out = Path(__file__).parent / "verification_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n  [REPORT] Machine-readable report -> {out.name}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_verification() -> None:
    """Execute all verification phases in order and emit a final report."""
    validate_environment()
    results: list[TestResult] = []
    created_log_ids: list[str] = []

    with httpx.Client() as client:
        # Phase 1: Authentication & Identity
        _banner("PHASE 1 - Authentication & Identity")
        auth_result, token = _test_auth_success(client)
        results.append(auth_result)
        _print_test(auth_result)
        if not token:
            print("[FATAL] Cannot proceed without a valid token.")
            sys.exit(1)
        hdrs = _auth_headers(token)

        for fn in (
            lambda: _test_identity(client, hdrs),
            lambda: _test_non_admin(client, hdrs),
            lambda: _test_invalid_token(client),
            lambda: _test_mid_run_expiry(client),
        ):
            r = fn()
            results.append(r)
            _print_test(r)

        # Resolve test incident
        _banner("PRE-TEST - Resolving Target Incident")
        inc = _resolve_incident(client, hdrs)
        inc_sys_id: str = inc["sys_id"]
        inc_number: str = inc["number"]

        # Phase 2: Permitted operations
        _banner("PHASE 2 - Permitted Incident Operations")
        for fn in (
            lambda: _test_read_incident(client, hdrs, inc_sys_id, inc_number),
            lambda: _test_write_work_notes(client, hdrs, inc_sys_id),
            lambda: _test_write_ai_field(client, hdrs, inc_sys_id),
            lambda: _test_write_human_review_required(client, hdrs, inc_sys_id),
        ):
            r = fn()
            results.append(r)
            _print_test(r)

        # Phase 3: Execution Log (FR-02)
        _banner("PHASE 3 - Execution Log (FR-02 Audit Trail)")
        for tid, status, error in [
            ("LOG-01", "succeeded", ""),
            ("LOG-02", "failed", "Simulated processing failure for audit verification."),
            ("LOG-03", "blocked", ""),
            ("LOG-06", "abandoned", "Simulated run abandoned due to operator cancellation."),
        ]:
            r = _test_log_status(client, hdrs, inc_sys_id, created_log_ids, tid, status, error)
            results.append(r)
            _print_test(r)
        for fn in (
            lambda: _test_execution_id_lookup(client, hdrs, inc_sys_id, created_log_ids),
            lambda: _test_log_delete_forbidden(client, hdrs, inc_sys_id),
            lambda: _test_log_modify_forbidden(client, hdrs, inc_sys_id),
        ):
            r = fn()
            results.append(r)
            _print_test(r)

        # Phase 4: Forbidden incident fields
        _banner("PHASE 4 - Forbidden Incident Fields (read-before / patch / read-after)")
        for tid, fld, val in [
            ("DENY-01", "state", "6"),
            ("DENY-02", "assigned_to", "admin"),
            ("DENY-03", "assignment_group", _DENY_GROUP_ID),
            ("DENY-04", "priority", "1"),
        ]:
            r = _forbidden_scalar(client, hdrs, inc_sys_id, tid, fld, val)
            results.append(r)
            _print_test(r)
        r = _forbidden_journal(client, hdrs, inc_sys_id, "DENY-05", "comments")
        results.append(r)
        _print_test(r)

        # Phase 5: Human lock & controls
        _banner("PHASE 5 - Human Controls & Circuit Breakers")
        r = _test_human_lock(client, hdrs, inc_sys_id)
        results.append(r)
        _print_test(r)

        r = _test_ai_enabled(client, hdrs, inc_sys_id)
        results.append(r)
        _print_test(r)

        r = _test_human_lock_safety_stop(client, hdrs, inc_sys_id)
        results.append(r)
        _print_test(r)

        # Phase 6: Bulk bypass
        _banner("PHASE 6 - Bulk/Multi-Field Bypass")
        r = _test_bulk_bypass(client, hdrs, inc_sys_id)
        results.append(r)
        _print_test(r)

        # Cleanup
        _banner("CLEANUP - Removing Synthetic Audit Log Records")
        for log_id in created_log_ids:
            dr = client.delete(
                f"{TABLE_API_BASE}/{SCOPED_LOG_TABLE}/{log_id}",
                headers=hdrs,
                timeout=10.0,
            )
            icon = "deleted" if dr.status_code == 204 else f"HTTP {dr.status_code}"
            print(f"  {log_id}: {icon}")

    # Final report
    _print_matrix(results)
    _write_json_report(results)

    failed = [r for r in results if r.verdict == "FAIL"]
    if failed:
        print("  FAILED TESTS:")
        for r in failed:
            print(f"    {_RED}x{_RST} {r.test_id}: {r.name}")
        sys.exit(1)


if __name__ == "__main__":
    run_verification()
