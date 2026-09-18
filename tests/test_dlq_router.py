"""Tests for Dead-Letter Queue (DLQ) router and Operator RBAC enforcement."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api.schemas.dlq import DLQReplayResponse
from app.db.redis.keys import INCIDENT_DLQ_QUEUE
from app.main import create_app
from app.workers.db import WorkerRepo
from tests.helpers import mock_settings

VALID_TOKEN = "dev-webhook-secret-token"
AUTH_HEADERS = {"Authorization": f"Bearer {VALID_TOKEN}"}
OPERATOR_HEADERS = {**AUTH_HEADERS, "X-User-Role": "operator"}


@pytest.fixture
def app_instance():
    """Create test application instance with mocked resources."""
    settings = mock_settings(webhook_auth_token=VALID_TOKEN)
    app = create_app(settings=settings)
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.redis = MagicMock()

    mock_repo = MagicMock(spec=WorkerRepo)
    mock_repo.get_event_payload.return_value = {"number": "INC009", "sys_id": "sys999"}
    mock_repo.find_execution_id.return_value = uuid4()
    mock_repo.reset_for_replay.return_value = True
    app.state.sync_worker_repo = mock_repo

    mock_sync_redis = MagicMock()
    mock_sync_redis.lrange.return_value = []
    mock_sync_redis.lrem.return_value = 0
    app.state.sync_redis = mock_sync_redis
    return app


@pytest.fixture(autouse=True)
def mock_send_incident():
    """Mock Celery event producer across all tests."""
    with patch("app.workers.replay.send_incident_event") as mock_send:
        yield mock_send


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


@pytest.mark.asyncio
async def test_replay_dlq_succeeds_for_operator(app_instance) -> None:
    """Ensure POST /api/v1/dlq/{event_id}/replay succeeds with 202 for Operator role."""
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
    assert validated.status == "accepted"
    assert "evt-test-999" in validated.message


@pytest.mark.asyncio
async def test_replay_dlq_correlation_id_propagated(app_instance) -> None:
    """Ensure custom correlation ID header is preserved on DLQ replay."""
    custom_corr = "corr-dlq-audit-77"
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
async def test_list_dlq_returns_items_from_redis(app_instance) -> None:
    """Ensure GET /api/v1/dlq parses and returns items stored in Redis DLQ."""
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


@pytest.mark.asyncio
async def test_replay_dlq_moves_event_from_dlq_to_events_queue(
    app_instance, mock_send_incident
) -> None:
    """Ensure replay removes from DLQ and enqueues to incident events queue."""
    fake_dlq_item = json.dumps(
        {
            "event_id": "evt-dlq-replay-123",
            "payload": {"number": "INC009", "sys_id": "sys999"},
            "failure_reason": "Transient DB failure",
            "retry_count": 3,
        }
    )
    mock_sync_redis = MagicMock()
    mock_sync_redis.lrange.return_value = [fake_dlq_item]
    mock_sync_redis.lrem.return_value = 1
    app_instance.state.sync_redis = mock_sync_redis

    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/dlq/evt-dlq-replay-123/replay",
            headers=OPERATOR_HEADERS,
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["event_id"] == "evt-dlq-replay-123"
    assert "replayed" in data["message"]
    mock_sync_redis.lrem.assert_called_once_with(INCIDENT_DLQ_QUEUE, 0, fake_dlq_item)
    mock_send_incident.assert_called_once()


@pytest.mark.asyncio
async def test_replay_dlq_redis_failure_returns_503(app_instance) -> None:
    """Ensure Redis failure during replay raises 503 instead of reporting false success."""
    mock_sync_redis = MagicMock()
    mock_sync_redis.lrange.side_effect = RuntimeError("Redis connection lost")
    app_instance.state.sync_redis = mock_sync_redis

    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/dlq/evt-dlq-err-456/replay",
            headers=OPERATOR_HEADERS,
        )

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_replay_dlq_unknown_event_returns_404(app_instance) -> None:
    """Ensure replaying an unknown event returns 404."""
    app_instance.state.sync_worker_repo.get_event_payload.return_value = None

    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/dlq/evt-unknown/replay",
            headers=OPERATOR_HEADERS,
        )

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_replay_dlq_live_event_returns_409(app_instance) -> None:
    """Ensure attempting to replay a live (non-parked) event returns 409."""
    app_instance.state.sync_worker_repo.reset_for_replay.return_value = False
    app_instance.state.sync_worker_repo.get_retry_state.return_value = {"state": "running"}

    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/dlq/evt-running/replay",
            headers=OPERATOR_HEADERS,
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_CONFLICT"
