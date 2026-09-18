"""Tests for Dead-Letter Queue (DLQ) router and Operator RBAC enforcement.

The replay endpoint now delegates entirely to
:func:`app.workers.replay.replay_event` (the Sprint-2.3 worker utility).
All tests patch that function — they never assert Redis pipeline calls
directly, which was wrong before (raw lpush caused KeyError: 'properties'
in the Celery worker).
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from api.schemas.dlq import DLQReplayResponse
from app.main import create_app
from app.workers.replay import ReplayOutcome
from tests.helpers import mock_settings

VALID_TOKEN = "dev-webhook-secret-token"
AUTH_HEADERS = {"Authorization": f"Bearer {VALID_TOKEN}"}
OPERATOR_HEADERS = {**AUTH_HEADERS, "X-User-Role": "operator"}

_FAKE_EXECUTION_ID = UUID("12345678-1234-5678-1234-567812345678")

# Path to the function the router delegates to
_REPLAY_PATH = "api.routers.dlq._run_replay_in_thread"


@pytest.fixture
def app_instance():
    """Create test application instance with mocked resources."""
    settings = mock_settings(webhook_auth_token=VALID_TOKEN)
    app = create_app(settings=settings)
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.redis = MagicMock()
    return app


# ---------------------------------------------------------------------------
# Authentication / authorisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_dlq_requires_auth(app_instance) -> None:
    """Ensure GET /api/v1/dlq rejects unauthorized requests with 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/dlq")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_list_dlq_returns_list(app_instance) -> None:
    """Ensure GET /api/v1/dlq returns 200 and schema-valid list."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/dlq", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_replay_dlq_requires_auth(app_instance) -> None:
    """Ensure POST /api/v1/dlq/{event_id}/replay rejects requests without token with 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/dlq/evt-test-001/replay")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_replay_dlq_requires_operator_role(app_instance) -> None:
    """Ensure POST /api/v1/dlq/{event_id}/replay returns 403 Forbidden for non-operator users."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        # Without any X-User-Role header
        resp_no_role = await client.post("/api/v1/dlq/evt-test-001/replay", headers=AUTH_HEADERS)
        assert resp_no_role.status_code == 403
        assert resp_no_role.json()["error"]["code"] == "PERMISSION_DENIED"

        # With an unauthorized role
        resp_viewer = await client.post(
            "/api/v1/dlq/evt-test-001/replay",
            headers={**AUTH_HEADERS, "X-User-Role": "viewer"},
        )
        assert resp_viewer.status_code == 403
        assert resp_viewer.json()["error"]["code"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Successful replay path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_dlq_succeeds_for_operator(app_instance) -> None:
    """202 + 'replayed' status when replay_event succeeds."""
    outcome = ReplayOutcome(
        event_id="evt-test-999",
        execution_id=_FAKE_EXECUTION_ID,
        replayed=True,
        removed_records=1,
    )
    with patch(_REPLAY_PATH, new=AsyncMock(return_value=outcome)):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-test-999/replay",
                headers=OPERATOR_HEADERS,
            )

    assert resp.status_code == 202
    data = resp.json()
    validated = DLQReplayResponse.model_validate(data)
    assert validated.event_id == "evt-test-999"
    assert validated.status == "replayed"
    assert "replayed" in validated.message
    assert "evt-test-999" in validated.message


@pytest.mark.asyncio
async def test_replay_dlq_correlation_id_propagated(app_instance) -> None:
    """Custom correlation ID header is preserved on DLQ replay."""
    custom_corr = "corr-dlq-audit-77"
    outcome = ReplayOutcome(
        event_id="evt-test-corr",
        execution_id=_FAKE_EXECUTION_ID,
        replayed=True,
        removed_records=1,
    )
    with patch(_REPLAY_PATH, new=AsyncMock(return_value=outcome)):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-test-corr/replay",
                headers={**OPERATOR_HEADERS, "X-Correlation-ID": custom_corr},
            )

    assert resp.status_code == 202
    assert resp.headers.get("X-Correlation-ID") == custom_corr
    assert resp.json()["correlation_id"] == custom_corr


@pytest.mark.asyncio
async def test_replay_dlq_message_includes_removed_records(app_instance) -> None:
    """Response message reports how many DLQ records were removed."""
    outcome = ReplayOutcome(
        event_id="evt-multi-dlq",
        execution_id=_FAKE_EXECUTION_ID,
        replayed=True,
        removed_records=3,
    )
    with patch(_REPLAY_PATH, new=AsyncMock(return_value=outcome)):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-multi-dlq/replay",
                headers=OPERATOR_HEADERS,
            )

    assert resp.status_code == 202
    assert "3 DLQ record(s) removed" in resp.json()["message"]


# ---------------------------------------------------------------------------
# Edge-case: event not in Postgres (404)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_dlq_unknown_event_returns_404(app_instance) -> None:
    """404 when event_id is not found in the Postgres events table."""
    outcome = ReplayOutcome(
        event_id="evt-ghost",
        replayed=False,
        reason="unknown event_id: evt-ghost",
    )
    with patch(_REPLAY_PATH, new=AsyncMock(return_value=outcome)):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-ghost/replay",
                headers=OPERATOR_HEADERS,
            )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_replay_dlq_no_execution_returns_404(app_instance) -> None:
    """404 when event exists in events table but has no execution row."""
    outcome = ReplayOutcome(
        event_id="evt-no-exec",
        replayed=False,
        reason="no execution exists for event_id: evt-no-exec",
    )
    with patch(_REPLAY_PATH, new=AsyncMock(return_value=outcome)):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-no-exec/replay",
                headers=OPERATOR_HEADERS,
            )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Edge-case: event not parked (409 Conflict)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_dlq_live_event_returns_409(app_instance) -> None:
    """409 when the event is still running — refuse to reset a live worker."""
    outcome = ReplayOutcome(
        event_id="evt-running",
        execution_id=_FAKE_EXECUTION_ID,
        replayed=False,
        reason="event is not parked (retry_state='running') — only 'exhausted'/'cancelled' events replay",
    )
    with patch(_REPLAY_PATH, new=AsyncMock(return_value=outcome)):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-running/replay",
                headers=OPERATOR_HEADERS,
            )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "RESOURCE_CONFLICT"


@pytest.mark.asyncio
async def test_replay_dlq_succeeded_event_returns_409(app_instance) -> None:
    """409 when the event already succeeded — no point replaying."""
    outcome = ReplayOutcome(
        event_id="evt-done",
        execution_id=_FAKE_EXECUTION_ID,
        replayed=False,
        reason="event is not parked (retry_state='succeeded') — only 'exhausted'/'cancelled' events replay",
    )
    with patch(_REPLAY_PATH, new=AsyncMock(return_value=outcome)):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-done/replay",
                headers=OPERATOR_HEADERS,
            )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "RESOURCE_CONFLICT"


# ---------------------------------------------------------------------------
# Edge-case: infrastructure failure (503)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_dlq_infra_failure_returns_503(app_instance) -> None:
    """503 when Postgres or Redis is unreachable during replay."""
    with patch(_REPLAY_PATH, new=AsyncMock(side_effect=RuntimeError("Connection refused"))):
        async with AsyncClient(
            transport=ASGITransport(app=app_instance), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/dlq/evt-db-down/replay",
                headers=OPERATOR_HEADERS,
            )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# GET /api/v1/dlq — list from Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_dlq_returns_items_from_redis(app_instance) -> None:
    """Ensure GET /api/v1/dlq parses and returns items stored in Redis DLQ."""
    import json
    from datetime import datetime

    fake_dlq_item = json.dumps(
        {
            "event_id": "evt-dlq-001",
            "payload": {"number": "INC001", "sys_id": "sys123"},
            "failure_reason": "ServiceNow API timeout",
            "retry_count": 3,
            "failed_at": datetime.now(UTC).isoformat(),
        }
    )
    mock_redis = AsyncMock()
    mock_redis.lrange.return_value = [fake_dlq_item]
    app_instance.state.redis = mock_redis

    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/dlq", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["event_id"] == "evt-dlq-001"
    assert data[0]["failure_reason"] == "ServiceNow API timeout"
    assert data[0]["retry_count"] == 3
