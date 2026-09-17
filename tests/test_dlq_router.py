"""Tests for Dead-Letter Queue (DLQ) router and Operator RBAC enforcement."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.schemas.dlq import DLQReplayResponse
from app.main import create_app
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
    return app


@pytest.mark.asyncio
async def test_list_dlq_requires_auth(app_instance) -> None:
    """Ensure GET /api/v1/dlq rejects unauthorized requests with 401."""
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://test") as client:
        resp = await client.get("/api/v1/dlq")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_list_dlq_returns_list(app_instance) -> None:
    """Ensure GET /api/v1/dlq returns 200 and schema-valid list."""
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://test") as client:
        resp = await client.get("/api/v1/dlq", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_replay_dlq_requires_auth(app_instance) -> None:
    """Ensure POST /api/v1/dlq/{event_id}/replay rejects requests without token with 401."""
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://test") as client:
        resp = await client.post("/api/v1/dlq/evt-test-001/replay")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_replay_dlq_requires_operator_role(app_instance) -> None:
    """Ensure POST /api/v1/dlq/{event_id}/replay returns 403 Forbidden for non-operator users."""
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://test") as client:
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
    async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/dlq/evt-test-corr/replay",
            headers={**OPERATOR_HEADERS, "X-Correlation-ID": custom_corr},
        )

    assert resp.status_code == 202
    assert resp.headers.get("X-Correlation-ID") == custom_corr
    assert resp.json()["correlation_id"] == custom_corr
