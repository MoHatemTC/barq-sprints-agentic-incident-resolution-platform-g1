"""Terminal failures and DLQ replay keep the visible incident state honest."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.exceptions.app_errors import ConflictError
from app.models.incident import AIProcessingState
from app.workers.incident_state import (
    mark_incident_failed,
    prepare_failed_incident_for_replay,
    prepare_servicenow_retry,
)
from tests.helpers import mock_settings

SYS_ID = "a" * 32
PAYLOAD = {"sys_id": SYS_ID, "number": "INC0010024"}


def _incident(**overrides):
    values = {
        "number": PAYLOAD["number"],
        "ai_enabled": True,
        "ai_human_lock": False,
        "ai_processing_state": AIProcessingState.IN_PROGRESS,
        "ai_processing_start": datetime.now(UTC),
        "ai_failure_reason": None,
        "ai_processing_end": None,
        "ai_retry_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _client():
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get_incident = AsyncMock()
    client.update_incident = AsyncMock()
    client._request = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_terminal_failure_writes_state_reason_and_end_together() -> None:
    client = _client()
    client.get_incident.return_value = _incident()
    execution_id = str(uuid4())
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        written = await mark_incident_failed(
            mock_settings(), PAYLOAD, execution_id, RuntimeError("model unavailable"), 3
        )

    assert written is True
    client.update_incident.assert_awaited_once()
    sys_id, update = client.update_incident.await_args.args
    assert sys_id == SYS_ID
    assert update.ai_processing_state is AIProcessingState.FAILED
    assert execution_id in update.ai_failure_reason
    assert "model unavailable" in update.ai_failure_reason
    assert update.ai_processing_end is not None
    body = update.to_table_api_body()
    assert body["x_2215032_ai_inc_0_ai_processing_state"] == "failed"
    assert body["x_2215032_ai_inc_0_ai_failure_reason"]
    assert body["x_2215032_ai_inc_0_ai_processing_end"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,locked",
    [
        (AIProcessingState.COMPLETE, False),
        (AIProcessingState.AWAITING_APPROVAL, False),
        (AIProcessingState.FAILED, False),
        (AIProcessingState.IN_PROGRESS, True),
    ],
)
async def test_terminal_failure_never_overwrites_finished_or_human_owned_incident(
    state: AIProcessingState, locked: bool
) -> None:
    client = _client()
    client.get_incident.return_value = _incident(ai_processing_state=state, ai_human_lock=locked)
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        written = await mark_incident_failed(
            mock_settings(), PAYLOAD, str(uuid4()), RuntimeError("error"), 1
        )
    assert written is False
    client.update_incident.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_failure_refuses_an_event_for_a_different_incident() -> None:
    client = _client()
    client.get_incident.return_value = _incident(number="INC-DIFFERENT")
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        written = await mark_incident_failed(
            mock_settings(), PAYLOAD, str(uuid4()), RuntimeError("error"), 1
        )
    assert written is False
    client.update_incident.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_clears_failed_state_before_requeue() -> None:
    client = _client()
    client.get_incident.side_effect = [
        _incident(ai_processing_state=AIProcessingState.FAILED, ai_failure_reason="old error"),
        _incident(
            ai_processing_state=AIProcessingState.PENDING,
            ai_processing_start=None,
            ai_processing_end=None,
        ),
    ]
    client._request.return_value = {"x_2215032_ai_inc_0_ai_processing_state": "pending"}
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        await prepare_failed_incident_for_replay(mock_settings(), PAYLOAD)
    client._request.assert_awaited_once()
    body = client._request.await_args.kwargs["json"]
    assert body == {
        "x_2215032_ai_inc_0_ai_processing_state": "pending",
        "x_2215032_ai_inc_0_ai_failure_reason": "",
        "x_2215032_ai_inc_0_ai_processing_start": "",
        "x_2215032_ai_inc_0_ai_processing_end": "",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,locked",
    [
        (AIProcessingState.COMPLETE, False),
        (AIProcessingState.AWAITING_APPROVAL, False),
        (AIProcessingState.FAILED, True),
    ],
)
async def test_replay_refuses_completed_paused_or_human_locked_incident(
    state: AIProcessingState, locked: bool
) -> None:
    client = _client()
    client.get_incident.return_value = _incident(ai_processing_state=state, ai_human_lock=locked)
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        with pytest.raises(ConflictError):
            await prepare_failed_incident_for_replay(mock_settings(), PAYLOAD)
    client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_refuses_pending_incident_with_stale_failure_fields() -> None:
    client = _client()
    client.get_incident.return_value = _incident(
        ai_processing_state=AIProcessingState.PENDING,
        ai_failure_reason="prior failure",
    )
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        with pytest.raises(ConflictError, match="clean pending"):
            await prepare_failed_incident_for_replay(mock_settings(), PAYLOAD)
    client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_servicenow_failed_transition_event_is_reset_for_its_retry() -> None:
    client = _client()
    failed = _incident(
        ai_processing_state=AIProcessingState.FAILED,
        ai_failure_reason="previous attempt failed",
        ai_retry_count=1,
    )
    client.get_incident.side_effect = [
        failed,
        failed,
        _incident(
            ai_processing_state=AIProcessingState.PENDING,
            ai_processing_start=None,
            ai_processing_end=None,
        ),
    ]
    client._request.return_value = {"x_2215032_ai_inc_0_ai_processing_state": "pending"}
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        prepared = await prepare_servicenow_retry(
            mock_settings(), {**PAYLOAD, "event_type": "incident.updated"}
        )
    assert prepared is True
    client._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_original_event_does_not_reset_a_failed_incident() -> None:
    client = _client()
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        prepared = await prepare_servicenow_retry(
            mock_settings(), {**PAYLOAD, "event_type": "incident.created"}
        )
    assert prepared is False
    client.get_incident.assert_not_awaited()


@pytest.mark.asyncio
async def test_stuck_awaiting_approval_without_a_decidable_pause_is_released() -> None:
    # INC0010252: ServiceNow said "awaiting approval" but no pause was ever stored.
    client = _client()
    client.get_incident.return_value = _incident(
        ai_processing_state=AIProcessingState.AWAITING_APPROVAL
    )
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        written = await mark_incident_failed(
            mock_settings(),
            PAYLOAD,
            str(uuid4()),
            RuntimeError("insert failed"),
            1,
            has_decidable_pause=lambda sys_id: False,
        )
    assert written is True
    update = client.update_incident.await_args.args[1]
    assert update.ai_processing_state is AIProcessingState.FAILED
    assert "released for an engineer" in update.ai_failure_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", [lambda sys_id: True, lambda sys_id: 1 / 0])
async def test_a_real_or_unknown_pause_is_never_released(lookup) -> None:
    client = _client()
    client.get_incident.return_value = _incident(
        ai_processing_state=AIProcessingState.AWAITING_APPROVAL
    )
    with patch("app.workers.incident_state.ServiceNowClient", return_value=client):
        written = await mark_incident_failed(
            mock_settings(),
            PAYLOAD,
            str(uuid4()),
            RuntimeError("error"),
            1,
            has_decidable_pause=lookup,
        )
    assert written is False
    client.update_incident.assert_not_awaited()
