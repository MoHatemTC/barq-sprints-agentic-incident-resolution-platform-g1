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
