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
    sys_id = os.environ.get("SERVICENOW_TEST_LOCKED_INCIDENT_SYS_ID")
    if not sys_id:
        pytest.skip("SERVICENOW_TEST_LOCKED_INCIDENT_SYS_ID not set")
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
    platform recalculated are indistinguishable — the test would pass either way, which
    is the class of fail-open this suite exists to avoid.

    Both outcomes below prove the ACL held:
      * ``ServiceNowAuthorizationError`` — the instance answered the PATCH with 403.
      * ``ServiceNowWriteRejectedError`` — the PATCH returned 200 but the field did not
        change, which is how ServiceNow silently drops a write the ACL forbids.
    """
    raw_before = await client._request("GET", f"/api/now/table/incident/{test_sys_id}")
    assert isinstance(raw_before, dict)
    group_before = _raw_field(raw_before, "assignment_group")

    if not group_before:
        pytest.skip(
            "The test incident has no assignment_group, so there is no non-derived "
            "value to attempt to change. Set one on "
            f"{test_sys_id} and re-run. Skipping rather than substituting a derived "
            "field, which could not prove refusal."
        )

    # Clearing a populated reference is a real change and is not recalculated by the
    # platform, so any difference afterwards is genuinely the write landing.
    forbidden_body: dict[str, object] = {"assignment_group": ""}

    with pytest.raises((ServiceNowWriteRejectedError, ServiceNowAuthorizationError)):
        result = await client._request(
            "PATCH",
            f"/api/now/table/incident/{test_sys_id}",
            json=forbidden_body,
        )
        client._verify_write_persisted(
            requested=forbidden_body, persisted=result, sys_id=test_sys_id
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
