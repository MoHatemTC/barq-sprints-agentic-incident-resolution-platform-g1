import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.clients.servicenow_client import ServiceNowClient
from app.core.config import Settings
from app.exceptions.servicenow import (
    ServiceNowAuthorizationError,
    ServiceNowError,
    ServiceNowValidationError,
)
from app.models.execution_log import ExecutionLogCreatePayload, ExecutionStatus
from app.models.incident import AIProcessingState, IncidentUpdatePayload

pytestmark = pytest.mark.skipif(
    not os.environ.get("SERVICENOW_TEST_INCIDENT_SYS_ID")
    or not os.environ.get("SERVICENOW_PASSWORD"),
    reason="Live PDI credentials and test incident not configured",
)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
async def client(settings: Settings) -> ServiceNowClient:
    async with ServiceNowClient(settings) as c:
        yield c


@pytest.fixture
def test_sys_id() -> str:
    return os.environ["SERVICENOW_TEST_INCIDENT_SYS_ID"]


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
            "PATCH", "/api/now/table/sys_audit/invalid_sys_id", json={"documentkey": "123"}
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
        action="verify_outcomes",
        status=status,
        result="Test completed" if status == ExecutionStatus.SUCCEEDED else None,
        error="Test error"
        if status in [ExecutionStatus.BLOCKED, ExecutionStatus.FAILED, ExecutionStatus.ABANDONED]
        else None,
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
        action="verify_failure_handling",
        status=ExecutionStatus.FAILED,
        error="Simulated failure",
    )

    # We force an exception in the HTTP client just for the log write
    with patch.object(client._http, "request", side_effect=Exception("Simulated Network Error")):
        # This should return None and not raise an exception
        log_entry = await client.write_execution_log(payload)
        assert log_entry is None
