from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.execution_log import ExecutionLogCreatePayload, ExecutionLogEntry, ExecutionStatus


def test_execution_log_entry_assume_utc():
    # Naive datetime gets converted to UTC
    dt_naive = datetime(2023, 1, 1, 12, 0, 0)
    entry = ExecutionLogEntry(
        sys_id="sys1",
        execution_id="exec1",
        incident_reference="inc1",
        agent="agent1",
        action="action1",
        status=ExecutionStatus.STARTED,
        timestamp=dt_naive,
    )
    assert entry.timestamp is not None
    assert entry.timestamp.tzinfo == UTC
    assert entry.timestamp.isoformat() == "2023-01-01T12:00:00+00:00"

    # Aware datetime retains its timezone (or is processed properly)
    dt_aware = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
    entry2 = ExecutionLogEntry(
        sys_id="sys2",
        execution_id="exec2",
        incident_reference="inc2",
        agent="agent2",
        action="action2",
        status=ExecutionStatus.STARTED,
        timestamp=dt_aware,
    )
    assert entry2.timestamp == dt_aware


def test_execution_log_create_payload_validation():
    # Blank fields raise validation errors
    with pytest.raises(ValidationError, match="field must not be blank"):
        ExecutionLogCreatePayload(
            incident_sys_id="inc1",
            execution_id="  ",
            agent="agent1",
            action="action1",
            status=ExecutionStatus.STARTED,
        )

    # Missing timezone raises validation error
    with pytest.raises(ValidationError, match="datetime must include timezone information"):
        ExecutionLogCreatePayload(
            incident_sys_id="inc1",
            execution_id="exec1",
            agent="agent1",
            action="action1",
            status=ExecutionStatus.STARTED,
            timestamp=datetime(2023, 1, 1, 12, 0, 0),  # naive
        )


def test_execution_log_create_payload_to_table_api_body():
    dt_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
    payload = ExecutionLogCreatePayload(
        incident_sys_id="inc1",
        execution_id="exec1",
        agent="agent1",
        action="action1",
        status=ExecutionStatus.STARTED,
        timestamp=dt_utc,
        result="success",
        error=None,
    )
    body = payload.to_table_api_body()

    assert body == {
        "incident_reference": "inc1",
        "execution_id": "exec1",
        "agent": "agent1",
        "action": "action1",
        "status": "started",
        "timestamp": "2023-01-01 12:00:00",
        "result": "success",
    }
