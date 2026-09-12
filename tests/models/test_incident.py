from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.incident import AIProcessingState, Incident, IncidentUpdatePayload


def test_incident_sanitize_servicenow_fields():
    # Test empty string conversion
    data = {
        "sys_id": "sys1",
        "number": "INC001",
        "short_description": "",
        "active": "",
        "x_2215032_ai_inc_0_ai_enabled": "",
        "x_2215032_ai_inc_0_ai_processing_state": "",
    }
    incident = Incident(**data)
    assert incident.short_description is None
    assert incident.active is False
    assert incident.ai_enabled is False
    assert incident.ai_processing_state == AIProcessingState.PENDING

    # Test string boolean parsing
    data_true = {
        "sys_id": "sys2",
        "number": "INC002",
        "active": "true",
        "x_2215032_ai_inc_0_ai_enabled": "1",
    }
    incident_true = Incident(**data_true)
    assert incident_true.active is True
    assert incident_true.ai_enabled is True


def test_incident_assume_utc():
    # Naive datetime gets timezone attached
    dt_naive = datetime(2023, 1, 1, 12, 0, 0)
    incident = Incident(
        sys_id="sys1",
        number="INC001",
        ai_processing_start=dt_naive,
        ai_processing_end=None,
    )
    assert incident.ai_processing_start is not None
    assert incident.ai_processing_start.tzinfo == UTC


def test_incident_update_payload_validation():
    # Require timezone
    with pytest.raises(ValidationError, match="datetime must include timezone information"):
        IncidentUpdatePayload(ai_processing_start=datetime(2023, 1, 1, 12, 0, 0))

    # Failed state requires failure reason
    with pytest.raises(
        ValidationError, match="processing_state=failed requires a non-empty ai_failure_reason"
    ):
        IncidentUpdatePayload(ai_processing_state=AIProcessingState.FAILED)

    # Complete state requires end time
    with pytest.raises(
        ValidationError, match="processing_state=complete requires ai_processing_end"
    ):
        IncidentUpdatePayload(ai_processing_state=AIProcessingState.COMPLETE, ai_resolution="Fixed")

    # Complete state requires resolution
    dt_utc = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(
        ValidationError, match="processing_state=complete requires a non-empty ai_resolution"
    ):
        IncidentUpdatePayload(
            ai_processing_state=AIProcessingState.COMPLETE, ai_processing_end=dt_utc
        )

    # End must not precede start
    dt_start = datetime(2023, 1, 1, 13, 0, 0, tzinfo=UTC)
    dt_end = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(
        ValidationError, match="ai_processing_end must not precede ai_processing_start"
    ):
        IncidentUpdatePayload(
            ai_processing_start=dt_start,
            ai_processing_end=dt_end,
        )


def test_incident_update_payload_to_table_api_body():
    # Test timezone conversion to UTC
    tz_east = timezone(timedelta(hours=2))
    dt_east = datetime(2023, 1, 1, 14, 0, 0, tzinfo=tz_east)  # Equivalent to 12:00 UTC

    payload = IncidentUpdatePayload(
        work_notes="Test note",
        ai_processing_state=AIProcessingState.IN_PROGRESS,
        ai_confidence=0.95,
        ai_human_review_required=True,
        ai_processing_start=dt_east,  # Should format as UTC string
    )

    body = payload.to_table_api_body()

    assert "work_notes" in body
    assert body["work_notes"] == "Test note"
    assert body["x_2215032_ai_inc_0_ai_processing_state"] == "in_progress"
    assert body["x_2215032_ai_inc_0_ai_confidence"] == "0.95"
    assert body["x_2215032_ai_inc_0_ai_human_review_required"] == "true"
    assert body["x_2215032_ai_inc_0_ai_processing_start"] == "2023-01-01 12:00:00"

    payload_no_notes = IncidentUpdatePayload(ai_confidence=0.8)
    body_no_notes = payload_no_notes.to_table_api_body()
    assert "work_notes" not in body_no_notes
