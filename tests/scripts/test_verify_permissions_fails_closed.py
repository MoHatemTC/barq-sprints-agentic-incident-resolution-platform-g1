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
    """#46: DENY-02 writes "admin" to assigned_to, which stores a sys_id.

    The verdict used to require ``after == value``. A reference field can never
    satisfy that, so the write landing looked exactly like the write being refused,
    and DENY-02 could not fail under any circumstances. The evidence that matters is
    that the stored value changed at all.
    """
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
    """Regression guard: a genuinely refused write is still a PASS."""
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
    """Regression guard: 403 with the record still there is the real blocked case."""
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
