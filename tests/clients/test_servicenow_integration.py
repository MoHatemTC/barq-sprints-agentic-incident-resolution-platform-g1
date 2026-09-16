import os
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr, ValidationError, field_validator

from app.clients.servicenow_client import ServiceNowClient
from app.core.config import Settings
from app.exceptions.servicenow import (
    ServiceNowAuthorizationError,
    ServiceNowHumanLockError,
    ServiceNowWriteRejectedError,
)
from app.models.execution_log import (
    ExecutionAction,
    ExecutionLogCreatePayload,
    ExecutionStatus,
)
from app.models.incident import AIProcessingState, IncidentUpdatePayload


class LiveServiceNowTestSettings(Settings):
    servicenow_test_incident_sys_id: str

    @field_validator(
        "servicenow_client_id",
        "servicenow_client_secret",
        "servicenow_username",
        "servicenow_password",
        "servicenow_test_incident_sys_id",
    )
    @classmethod
    def required_value_must_not_be_blank(cls, value: str | SecretStr) -> str | SecretStr:
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not raw_value.strip():
            raise ValueError("must not be blank")
        return value


def _load_live_test_settings() -> tuple[LiveServiceNowTestSettings | None, str]:
    if os.environ.get("SERVICENOW_LIVE_TESTS") != "1":
        return None, "Live PDI tests require explicit SERVICENOW_LIVE_TESTS=1 opt-in"

    try:
        return LiveServiceNowTestSettings(_env_file=".env"), ""
    except ValidationError as exc:
        invalid_fields = sorted(
            {str(error["loc"][0]).upper() for error in exc.errors() if error["loc"]}
        )
        return None, (
            "Live PDI tests require complete, valid ServiceNow configuration; "
            f"missing or invalid: {', '.join(invalid_fields)}"
        )


_LIVE_TEST_SETTINGS, _LIVE_TEST_SKIP_REASON = _load_live_test_settings()

pytestmark = pytest.mark.skipif(
    _LIVE_TEST_SETTINGS is None,
    reason=_LIVE_TEST_SKIP_REASON,
)


@pytest.fixture
def settings() -> LiveServiceNowTestSettings:
    assert _LIVE_TEST_SETTINGS is not None
    return _LIVE_TEST_SETTINGS


@pytest.fixture
async def client(settings: Settings) -> ServiceNowClient:
    async with ServiceNowClient(settings) as c:
        yield c


@pytest.fixture
def test_sys_id(settings: LiveServiceNowTestSettings) -> str:
    return settings.servicenow_test_incident_sys_id


@pytest.fixture
def locked_test_sys_id() -> str:
    """An incident with AI Human Lock already set to true.

    This cannot be produced by the suite itself, and that is the security control
    working: verified against dev407364 on 2026-09-16, a PATCH of
    ``x_2215032_ai_inc_0_ai_human_lock`` as ``ai_orchestrator_svc`` returns HTTP 200
    with the value still ``false`` - the ACL strips it. Per #69 the write ACL grants
    ``itil`` and ``admin``, so a human has to set the flag.

    Setup: open any incident in the PDI as an itil user, tick **AI Human Lock**, then
    export SERVICENOW_TEST_LOCKED_INCIDENT_SYS_ID with that sys_id.

    ``test_live_integration_identity_cannot_set_human_lock`` below covers the other
    half of the control and needs no fixture, so the lock is not wholly untested while
    this one is unset.
    """
    sys_id = os.environ.get("SERVICENOW_TEST_LOCKED_INCIDENT_SYS_ID")
    if not sys_id:
        pytest.skip(
            "SERVICENOW_TEST_LOCKED_INCIDENT_SYS_ID not set. Lock an incident as an "
            "itil user (see fixture docstring) - the integration identity cannot set "
            "the flag itself, by design."
        )
    return sys_id


@pytest.mark.asyncio
async def test_live_field_level_idempotency(client: ServiceNowClient, test_sys_id: str) -> None:
    """Checks for field-level idempotency by making the same update multiple times."""
    payload = IncidentUpdatePayload(
        ai_processing_state=AIProcessingState.IN_PROGRESS,
        ai_confidence=0.99,
        ai_classification="network",
    )

    # First update
    incident_1 = await client.update_incident(test_sys_id, payload)
    assert incident_1.ai_processing_state == AIProcessingState.IN_PROGRESS
    assert incident_1.ai_confidence == 0.99

    # Second update (Idempotent)
    incident_2 = await client.update_incident(test_sys_id, payload)
    assert incident_2.ai_processing_state == AIProcessingState.IN_PROGRESS
    assert incident_2.ai_confidence == 0.99


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        ExecutionStatus.STARTED,
        ExecutionStatus.BLOCKED,
        ExecutionStatus.FAILED,
        ExecutionStatus.ABANDONED,
        ExecutionStatus.SUCCEEDED,
    ],
)
async def test_live_verify_log_entries_across_outcomes(
    client: ServiceNowClient, test_sys_id: str, status: ExecutionStatus
) -> None:
    """Verify log entries across every terminal outcome without manual cleanup."""
    exec_id = f"test_exec_{status.value}_{int(datetime.now(UTC).timestamp())}"
    payload = ExecutionLogCreatePayload(
        incident_sys_id=test_sys_id,
        execution_id=exec_id,
        agent="integration_test_agent",
        action=ExecutionAction.EXECUTE,
        status=status,
        result="Test completed" if status == ExecutionStatus.SUCCEEDED else None,
        error=(
            "Test error"
            if status
            in [
                ExecutionStatus.BLOCKED,
                ExecutionStatus.FAILED,
                ExecutionStatus.ABANDONED,
            ]
            else None
        ),
    )

    log_entry = await client.write_execution_log(payload)
    assert log_entry is not None
    assert log_entry.execution_id == exec_id
    assert log_entry.status == status


@pytest.mark.asyncio
async def test_live_execution_log_write_failure_safely_handled(
    client: ServiceNowClient, test_sys_id: str
) -> None:
    """Ensure an execution log write failure logs safely without interrupting the main caller."""
    exec_id = f"test_fail_{int(datetime.now(UTC).timestamp())}"
    payload = ExecutionLogCreatePayload(
        incident_sys_id=test_sys_id,
        execution_id=exec_id,
        agent="integration_test_agent",
        action=ExecutionAction.EXECUTE,
        status=ExecutionStatus.FAILED,
        error="Simulated failure",
    )

    # Use httpx.ConnectError so it gets translated to a ServiceNowError
    with patch.object(
        client._http,
        "request",
        side_effect=httpx.ConnectError("Simulated Network Error"),
    ):
        # This should return None and not raise an exception
        log_entry = await client.write_execution_log(payload)
        assert log_entry is None


async def _a_different_group_sys_id(client: ServiceNowClient, current: str) -> str:
    """A real sys_user_group sys_id that is not the incident's current value.

    A fabricated sys_id would be rejected as an invalid reference, which looks exactly
    like an ACL refusal - the same fail-open shape this test exists to remove.
    """
    groups = await client._request(
        "GET",
        "/api/now/table/sys_user_group",
        params={"sysparm_limit": 5, "sysparm_fields": "sys_id"},
    )
    rows = groups if isinstance(groups, list) else []
    for row in rows:
        candidate = str(row.get("sys_id", ""))
        if candidate and candidate != current:
            return candidate
    raise AssertionError(
        "No sys_user_group is readable, so no valid reference value can be built. "
        "Fix the fixture rather than skipping: an invalid sys_id would be refused for "
        "the wrong reason and the test would pass without proving anything."
    )


def _raw_field(record: dict[str, object], field: str) -> str:
    """Read a Table API field, unwrapping the {'value', 'link'} reference form."""
    value = record.get(field)
    if isinstance(value, dict):
        return str(value.get("value", ""))
    return str(value or "")


@pytest.mark.asyncio
async def test_live_forbidden_field_write_is_rejected(
    client: ServiceNowClient, test_sys_id: str
) -> None:
    """A field the integration identity must not write is refused, and stays unchanged.

    Uses ``assignment_group`` (the harness's DENY-03), not ``priority``. ServiceNow
    derives priority from impact and urgency, so a refused write and a value the
    platform recalculated are indistinguishable - the test would pass either way.

    Verified against dev407364 on 2026-09-16: a PATCH of ``assignment_group`` as
    ``ai_orchestrator_svc`` returns **HTTP 200 with the field silently stripped**, which
    is how ServiceNow drops a write the ACL forbids. That is why the assertion is on the
    stored value and not on the status code.

    The write *sets* a value rather than clearing one. Clearing requires the field to
    already be populated, and this identity cannot populate it - so that version skipped
    on any incident it could actually create, which is no test at all. Setting works
    from either starting state and never skips.

    Both outcomes prove the ACL held:
      * ``ServiceNowWriteRejectedError`` - the PATCH was accepted but did not persist.
      * ``ServiceNowAuthorizationError`` - the instance answered 403 outright.
    """
    raw_before = await client._request("GET", f"/api/now/table/incident/{test_sys_id}")
    assert isinstance(raw_before, dict)
    group_before = _raw_field(raw_before, "assignment_group")

    target = await _a_different_group_sys_id(client, group_before)

    with pytest.raises((ServiceNowWriteRejectedError, ServiceNowAuthorizationError)):
        result = await client._request(
            "PATCH",
            f"/api/now/table/incident/{test_sys_id}",
            json={"assignment_group": target},
        )
        client._verify_write_persisted(
            requested={"assignment_group": target}, persisted=result, sys_id=test_sys_id
        )

    raw_after = await client._request("GET", f"/api/now/table/incident/{test_sys_id}")
    assert isinstance(raw_after, dict)
    assert _raw_field(raw_after, "assignment_group") == group_before


@pytest.mark.asyncio
async def test_live_human_lock_blocks_update(
    client: ServiceNowClient, locked_test_sys_id: str
) -> None:
    """SECURITY.md: a locked incident must refuse both update_incident and
    add_work_note, and must never reach the PATCH."""
    payload = IncidentUpdatePayload(ai_confidence=0.5)
    with pytest.raises(ServiceNowHumanLockError):
        await client.update_incident(locked_test_sys_id, payload)


@pytest.mark.asyncio
async def test_live_human_lock_blocks_work_note(
    client: ServiceNowClient, locked_test_sys_id: str
) -> None:
    with pytest.raises(ServiceNowHumanLockError):
        await client.add_work_note(locked_test_sys_id, "should be refused")


@pytest.mark.asyncio
async def test_live_integration_identity_cannot_set_human_lock(
    client: ServiceNowClient, test_sys_id: str
) -> None:
    """The integration identity must not be able to lift or set its own kill switch.

    FR-06 and the S1.1 field model both say AI Human Lock is written by humans, never
    by the integration user - otherwise the agent could unlock an incident a human
    deliberately froze. The S1.2 export grants that write ACL to ``itil`` and ``admin``
    only (#69).

    Verified live against dev407364: the PATCH returns **HTTP 200 with the value still
    ``false``**, so ServiceNow accepts the request and silently drops the field. That is
    why this asserts on the stored value rather than the status code - a status-only
    assertion would pass even if the write had landed.

    This needs no locked-incident fixture, so it holds the lock control under test even
    when SERVICENOW_TEST_LOCKED_INCIDENT_SYS_ID is unset.
    """
    field = "x_2215032_ai_inc_0_ai_human_lock"

    before = await client._request(
        "GET", f"/api/now/table/incident/{test_sys_id}", params={"sysparm_fields": field}
    )
    assert isinstance(before, dict)
    assert _raw_field(before, field).lower() != "true", (
        "fixture incident is already locked; this test needs an unlocked one"
    )

    await client._request("PATCH", f"/api/now/table/incident/{test_sys_id}", json={field: "true"})

    after = await client._request(
        "GET", f"/api/now/table/incident/{test_sys_id}", params={"sysparm_fields": field}
    )
    assert isinstance(after, dict)
    assert _raw_field(after, field).lower() != "true", (
        "SECURITY FAILURE: the integration identity set AI Human Lock. It can now "
        "unlock any incident a human froze."
    )
