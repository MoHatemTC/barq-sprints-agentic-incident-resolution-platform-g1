"""#46: the permission harness must never report PASS because it could not look.

`_forbidden_scalar` and `_forbidden_journal` used to read back with `.get(field, "")`
and no HTTP status check. A 403 or 404 on the read, or a response that simply omitted
the field, produced `after == ""` — indistinguishable from "the write was refused" — and
the test passed. Against a permissive instance where the integration role *could* write
the forbidden fields but not read them back, the harness scored a clean sweep.

These tests drive the two functions with an in-process fake ServiceNow and assert that
each unobservable case is reported as FAIL, not PASS.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

SCRIPT_PATH = Path("scripts/verify_permissions.py")
INC = "inc-sys-id-0000000000000001"
FIELD = "state"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("verify_permissions_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves sys.modules[cls.__module__]; without this it is None.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# _forbidden_scalar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("read_status", [403, 404, 401, 500])
def test_scalar_unreadable_read_back_is_a_failure(read_status: int) -> None:
    """The write may well have landed — we simply cannot see it. That is not a pass."""
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(read_status, json={"error": "forbidden"})
        return httpx.Response(200, json={"result": {}})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-01", FIELD, "3")

    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


def test_scalar_missing_field_in_response_is_a_failure() -> None:
    """A 200 that simply omits the field also means we cannot see the result."""
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"result": {}})  # field absent
        return httpx.Response(200, json={"result": {}})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-01", FIELD, "3")

    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


def test_scalar_permissive_instance_is_a_failure() -> None:
    """The write lands and is readable: the ACL did not block it."""
    module = _load_module()
    state = {"value": "1"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            state["value"] = "3"
            return httpx.Response(200, json={"result": {FIELD: "3"}})
        return httpx.Response(200, json={"result": {FIELD: state["value"]}})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-01", FIELD, "3")

    assert result.verdict == "FAIL"
    assert result.persisted_change is True


def test_scalar_genuinely_blocked_still_passes() -> None:
    """The real blocked case must keep passing, or the guard is useless."""
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(403, json={"error": "acl"})
        return httpx.Response(200, json={"result": {FIELD: "1"}})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-01", FIELD, "3")

    assert result.verdict == "PASS"
    assert result.persisted_change is False


# ---------------------------------------------------------------------------
# _forbidden_journal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("read_status", [403, 404])
def test_journal_unreadable_query_is_a_failure(read_status: int) -> None:
    """A denied sys_journal_field query returned no rows, which counted as 0 == blocked."""
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(read_status, json={"error": "forbidden"})
        return httpx.Response(200, json={"result": {}})

    with _client(handler) as client:
        result = module._forbidden_journal(client, {}, INC, "DENY-05", "comments")

    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


def test_journal_leak_is_a_failure() -> None:
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"result": [{"sys_id": "j1"}]})
        return httpx.Response(200, json={"result": {}})

    with _client(handler) as client:
        result = module._forbidden_journal(client, {}, INC, "DENY-05", "comments")

    assert result.verdict == "FAIL"
    assert result.persisted_change is True


def test_journal_genuinely_blocked_still_passes() -> None:
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"result": []})
        return httpx.Response(200, json={"result": {}})

    with _client(handler) as client:
        result = module._forbidden_journal(client, {}, INC, "DENY-05", "comments")

    assert result.verdict == "PASS"
    assert result.persisted_change is False


# ---------------------------------------------------------------------------
# DENY-02: a reference field never reads back as the value that was sent
# ---------------------------------------------------------------------------


def test_scalar_reference_field_change_is_detected() -> None:
    """A changed reference field is a FAIL even though it reads back as a sys_id."""
    module = _load_module()
    reads = iter(["", "6816f79cc0a8016401c5a33be04be441"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"result": {"assigned_to": next(reads)}})
        return httpx.Response(200, json={"result": {}})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-02", "assigned_to", "admin")

    assert result.verdict == "FAIL", "a persisted write must not be reported as blocked"
    assert result.persisted_change is True
    assert "SECURITY FAILURE" in result.notes


def test_scalar_unchanged_reference_field_still_passes() -> None:
    """A refused write is a PASS."""
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"result": {"assigned_to": "unchanged"}})
        return httpx.Response(403, json={"error": "ACL"})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-02", "assigned_to", "admin")

    assert result.verdict == "PASS"
    assert result.persisted_change is False


# ---------------------------------------------------------------------------
# LOG-05: a 404 on DELETE is what a *successful* delete looks like
# ---------------------------------------------------------------------------


def _log_handler(delete_status: int, verify_status: int) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"result": {"sys_id": "log-row-1"}})
        if request.method == "DELETE":
            return httpx.Response(delete_status, json={})
        if verify_status == 200:
            return httpx.Response(200, json={"result": {"sys_id": "log-row-1"}})
        return httpx.Response(verify_status, json={"error": "not found"})

    return handler


def test_log_delete_that_removed_the_record_is_a_failure() -> None:
    """The record is gone, so the delete was not blocked - whatever DELETE returned."""
    module = _load_module()

    with _client(_log_handler(delete_status=404, verify_status=404)) as client:
        result = module._test_log_delete_forbidden(client, {}, INC)

    assert result.verdict == "FAIL", "a 404 on DELETE is not evidence the ACL held"
    assert "SECURITY FAILURE" in result.notes


def test_log_delete_refused_with_record_present_passes() -> None:
    """A 403 with the record still present is a PASS."""
    module = _load_module()

    with _client(_log_handler(delete_status=403, verify_status=200)) as client:
        result = module._test_log_delete_forbidden(client, {}, INC)

    assert result.verdict == "PASS"
    assert "still present" in result.notes


@pytest.mark.parametrize("verify_status", [401, 403, 500])
def test_log_delete_unreadable_verification_is_a_failure(verify_status: int) -> None:
    """Not being allowed to look is not evidence the record survived."""
    module = _load_module()

    with _client(_log_handler(delete_status=403, verify_status=verify_status)) as client:
        result = module._test_log_delete_forbidden(client, {}, INC)

    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


# ---------------------------------------------------------------------------
# LOCK-01 / LOCK-02: a missing flag is not "unlocked"
# ---------------------------------------------------------------------------


def _lock_handler(initial: str, *, patch_lands: bool, return_field: bool = True) -> Any:
    state = {"value": initial}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            if not patch_lands:
                return httpx.Response(403, json={"error": "acl"})
            body = json.loads(request.content)
            state["value"] = next(iter(body.values()))  # whichever flag the test patched
            return httpx.Response(200, json={"result": {}})
        field = request.url.params.get("sysparm_fields", "")
        if not return_field:
            return httpx.Response(200, json={"result": {}})  # field deliberately omitted
        return httpx.Response(200, json={"result": {field: state["value"]}})

    return handler


@pytest.mark.parametrize("test_id", ["LOCK-01", "LOCK-02"])
def test_lock_write_lands_but_field_is_not_returned_is_a_failure(test_id: str) -> None:
    """The stored flag is now the flipped one; the response simply hides it."""
    module = _load_module()
    fn = module._test_human_lock if test_id == "LOCK-01" else module._test_ai_enabled
    field = module.HUMAN_LOCK_FIELD if test_id == "LOCK-01" else module.AI_ENABLED_FIELD

    state = {"value": "false"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            state["value"] = json.loads(request.content)[field]
            return httpx.Response(200, json={"result": {}})
        # Baseline read shows the field; the post-patch read omits it.
        if state["value"] == "false":
            return httpx.Response(200, json={"result": {field: "false"}})
        return httpx.Response(200, json={"result": {}})

    with _client(handler) as client:
        result = fn(client, {}, INC)

    assert state["value"] == "true", "the write must actually land for this to mean anything"
    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


@pytest.mark.parametrize("read_status", [401, 403, 404])
@pytest.mark.parametrize("test_id", ["LOCK-01", "LOCK-02"])
def test_lock_unreadable_read_back_is_a_failure(test_id: str, read_status: int) -> None:
    module = _load_module()
    fn = module._test_human_lock if test_id == "LOCK-01" else module._test_ai_enabled

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(read_status, json={"error": "forbidden"})

    with _client(handler) as client:
        result = fn(client, {}, INC)

    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


@pytest.mark.parametrize("test_id", ["LOCK-01", "LOCK-02"])
def test_lock_permissive_instance_is_a_failure(test_id: str) -> None:
    """Everything readable, and the flag still moved: the ACL did not hold."""
    module = _load_module()
    fn = module._test_human_lock if test_id == "LOCK-01" else module._test_ai_enabled

    with _client(_lock_handler("false", patch_lands=True)) as client:
        result = fn(client, {}, INC)

    assert result.verdict == "FAIL"
    assert result.persisted_change is True


@pytest.mark.parametrize("test_id", ["LOCK-01", "LOCK-02"])
def test_lock_genuinely_blocked_still_passes(test_id: str) -> None:
    module = _load_module()
    fn = module._test_human_lock if test_id == "LOCK-01" else module._test_ai_enabled

    with _client(_lock_handler("false", patch_lands=False)) as client:
        result = fn(client, {}, INC)

    assert result.verdict == "PASS"
    assert result.persisted_change is False


# ---------------------------------------------------------------------------
# BULK-01: every read is checked, and the permitted write has to land
# ---------------------------------------------------------------------------


def _bulk_handler(*, forbidden_stripped: bool, journal_status: int = 200) -> Any:
    """A fake incident whose PATCH either strips the forbidden fields or does not."""
    state = {"state": "1", "priority": "3"}
    journals: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            body = json.loads(request.content)
            journals.append(("work_notes", body["work_notes"]))
            if not forbidden_stripped:
                journals.append(("comments", body["comments"]))
                state["state"] = body["state"]
                state["priority"] = body["priority"]
            return httpx.Response(200, json={"result": {}})

        path = request.url.path
        if path.endswith("/sys_journal_field"):
            if journal_status != 200:
                return httpx.Response(journal_status, json={"error": "forbidden"})
            query = request.url.params.get("sysparm_query", "")
            element = query.split("element=")[1].split("^")[0] if "element=" in query else ""
            marker = query.split("valueLIKE")[1] if "valueLIKE" in query else ""
            rows = [
                {"value": value}
                for (entry_element, value) in journals
                if entry_element == element and marker in value
            ]
            return httpx.Response(200, json={"result": rows})

        field = request.url.params.get("sysparm_fields", "")
        return httpx.Response(200, json={"result": {field: state[field]}})

    return handler


def test_bulk_permissive_instance_is_a_failure() -> None:
    """Writes land and reads are honest: BULK-01 must report the leak."""
    module = _load_module()

    with _client(_bulk_handler(forbidden_stripped=False)) as client:
        result = module._test_bulk_bypass(client, {}, INC)

    assert result.verdict == "FAIL"
    assert result.persisted_change is True
    assert "comments leaked=YES" in result.notes


def test_bulk_blocked_instance_still_passes() -> None:
    module = _load_module()

    with _client(_bulk_handler(forbidden_stripped=True)) as client:
        result = module._test_bulk_bypass(client, {}, INC)

    assert result.verdict == "PASS"
    assert "work_notes persisted=yes" in result.notes


@pytest.mark.parametrize("journal_status", [403, 404, 500])
def test_bulk_unreadable_journal_query_is_a_failure(journal_status: int) -> None:
    """A denied journal query has no rows, and no rows also means 'blocked'."""
    module = _load_module()

    with _client(_bulk_handler(forbidden_stripped=True, journal_status=journal_status)) as client:
        result = module._test_bulk_bypass(client, {}, INC)

    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


def test_bulk_permitted_write_that_did_not_persist_is_a_failure() -> None:
    """The verdict used to ignore work_notes, so a silent instance still passed."""
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(200, json={"result": {}})
        if request.url.path.endswith("/sys_journal_field"):
            return httpx.Response(200, json={"result": []})  # nothing ever persists
        field = request.url.params.get("sysparm_fields", "")
        return httpx.Response(200, json={"result": {field: {"state": "1", "priority": "3"}[field]}})

    with _client(handler) as client:
        result = module._test_bulk_bypass(client, {}, INC)

    assert result.verdict == "FAIL"
    assert "work_notes persisted=NO" in result.notes


# ---------------------------------------------------------------------------
# DENY-02: a reference field has to be compared against a real sys_id
# ---------------------------------------------------------------------------

SYS_ID = "6816f79cc0a8016401c5a33be04be441"


def test_deny02_permissive_instance_is_a_failure() -> None:
    """A sys_id that stored cleanly must read back as changed, not as 'admin'."""
    module = _load_module()
    assigned = {"value": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            assigned["value"] = json.loads(request.content)["assigned_to"]
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(200, json={"result": {"assigned_to": assigned["value"]}})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-02", "assigned_to", SYS_ID)

    assert assigned["value"] == SYS_ID
    assert result.verdict == "FAIL"
    assert result.persisted_change is True


def test_deny02_genuinely_blocked_still_passes() -> None:
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(403, json={"error": "acl"})
        return httpx.Response(200, json={"result": {"assigned_to": ""}})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-02", "assigned_to", SYS_ID)

    assert result.verdict == "PASS"
    assert result.persisted_change is False


def test_deny02_unreadable_read_back_is_a_failure() -> None:
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(403, json={"error": "forbidden"})

    with _client(handler) as client:
        result = module._forbidden_scalar(client, {}, INC, "DENY-02", "assigned_to", SYS_ID)

    assert result.verdict == "FAIL"
    assert "INCONCLUSIVE" in result.notes


def test_service_account_lookup_reports_unreadable_sys_id() -> None:
    """The DENY-02 pre-check must not hand back a usable-looking empty value."""
    module = _load_module()

    with _client(lambda request: httpx.Response(403, json={"error": "forbidden"})) as client:
        sys_id, status = module._resolve_service_account_sys_id(client, {})

    assert sys_id == ""
    assert status == 403
