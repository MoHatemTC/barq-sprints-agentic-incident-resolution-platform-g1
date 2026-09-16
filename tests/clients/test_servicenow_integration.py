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
    ServiceNowError,
    ServiceNowValidationError,
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
async def test_live_acl_refusal(client: ServiceNowClient) -> None:
    """Checks for ACL refusal when trying to modify a restricted system table."""
    with pytest.raises((ServiceNowAuthorizationError, ServiceNowValidationError, ServiceNowError)):
        await client._request(
            "PATCH",
            "/api/now/table/sys_audit/invalid_sys_id",
            json={"documentkey": "123"},
        )


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
