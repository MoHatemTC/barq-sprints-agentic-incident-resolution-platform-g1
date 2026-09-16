from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.redis.keys import INCIDENT_EVENTS_QUEUE
from app.main import create_app
from app.repositories.idempotency import EventAcceptanceResult, EventAcceptanceStatus
from tests.helpers import mock_settings

VALID_SYS_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
VALID_NUMBER = "INC0010001"
VALID_EVENT_ID = "evt-20260916-0001"
VALID_TOKEN = "dev-webhook-secret-token"

VALID_PAYLOAD = {
    "event_id": VALID_EVENT_ID,
    "sys_id": VALID_SYS_ID,
    "number": VALID_NUMBER,
    "event_type": "incident.created",
}


@pytest.fixture
def app_with_mocks():
    """Create test FastAPI application with mocked database and Redis resources."""
    settings = mock_settings(webhook_auth_token=VALID_TOKEN)
    app = create_app(settings=settings)

    mock_engine = MagicMock()
    mock_session_factory = MagicMock()
    mock_redis = MagicMock()
    mock_redis.lpush = AsyncMock(return_value=1)
    mock_redis.ping = AsyncMock(return_value=True)

    app.state.engine = mock_engine
    app.state.session_factory = mock_session_factory
    app.state.redis = mock_redis

    return app, mock_session_factory, mock_redis


@pytest.mark.asyncio
async def test_webhook_rejects_missing_authorization_header(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/webhook/incident", json=VALID_PAYLOAD)

    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "Missing or invalid Bearer token" in body["error"]["message"]


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_bearer_token(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook/incident",
            json=VALID_PAYLOAD,
            headers={"Authorization": "Bearer wrong-token-value"},
        )

    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "Invalid Bearer token" in body["error"]["message"]


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_contract_missing_field(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    incomplete_payload = {
        "event_id": VALID_EVENT_ID,
        "number": VALID_NUMBER,
        "event_type": "incident.created",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook/incident",
            json=incomplete_payload,
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_sys_id_pattern(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    invalid_payload = {**VALID_PAYLOAD, "sys_id": "invalid_sys_id_not_32_hex"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook/incident",
            json=invalid_payload,
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_webhook_rejects_extra_forbidden_fields(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    payload_with_extra = {**VALID_PAYLOAD, "unexpected_field": "disallowed"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook/incident",
            json=payload_with_extra,
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_webhook_rejects_unknown_contract_version(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    payload_v2 = {**VALID_PAYLOAD, "contract_version": "v2"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook/incident",
            json=payload_v2,
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "UNKNOWN_CONTRACT_VERSION"
    assert "Unsupported contract version" in body["error"]["message"]


@pytest.mark.asyncio
async def test_webhook_accepts_valid_new_event(app_with_mocks) -> None:
    app, session_factory, mock_redis = app_with_mocks

    acceptance_res = EventAcceptanceResult(
        status=EventAcceptanceStatus.ACCEPTED,
        event_id=VALID_EVENT_ID,
        event_record_id=uuid4(),
        execution_id=uuid4(),
    )

    with patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as mock_accept:
        mock_accept.return_value = acceptance_res

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhook/incident",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {VALID_TOKEN}"},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["event_id"] == VALID_EVENT_ID
        assert body["idempotent_replay"] is False
        assert "correlation_id" in body

        mock_accept.assert_awaited_once()
        mock_redis.lpush.assert_awaited_once()
        call_args = mock_redis.lpush.call_args[0]
        assert call_args[0] == INCIDENT_EVENTS_QUEUE
        assert VALID_EVENT_ID in call_args[1]


@pytest.mark.asyncio
async def test_webhook_idempotency_duplicate_skips_redis_enqueue(app_with_mocks) -> None:
    app, session_factory, mock_redis = app_with_mocks

    duplicate_res = EventAcceptanceResult(
        status=EventAcceptanceStatus.DUPLICATE,
        event_id=VALID_EVENT_ID,
    )

    with patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as mock_accept:
        mock_accept.return_value = duplicate_res

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhook/incident",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {VALID_TOKEN}"},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["event_id"] == VALID_EVENT_ID
        assert body["idempotent_replay"] is True

        mock_accept.assert_awaited_once()
        # Redis enqueue MUST be skipped for duplicate replay
        mock_redis.lpush.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_returns_503_on_db_failure(app_with_mocks) -> None:
    app, _, _ = app_with_mocks

    with patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as mock_accept:
        mock_accept.side_effect = RuntimeError("DB connection dropped")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhook/incident",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {VALID_TOKEN}"},
            )

        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_webhook_returns_503_on_redis_enqueue_failure(app_with_mocks) -> None:
    app, _, mock_redis = app_with_mocks
    mock_redis.lpush.side_effect = RuntimeError("Redis connection broken")

    acceptance_res = EventAcceptanceResult(
        status=EventAcceptanceStatus.ACCEPTED,
        event_id=VALID_EVENT_ID,
        event_record_id=uuid4(),
        execution_id=uuid4(),
    )

    with patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as mock_accept:
        mock_accept.return_value = acceptance_res

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhook/incident",
                json=VALID_PAYLOAD,
                headers={"Authorization": f"Bearer {VALID_TOKEN}"},
            )

        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_health_liveness_endpoint(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_endpoint_connected(app_with_mocks) -> None:
    app, _, mock_redis = app_with_mocks
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()

    class AsyncConnContext:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            pass

    mock_engine.connect = MagicMock(return_value=AsyncConnContext())
    app.state.engine = mock_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "connected"
    assert body["redis"] == "connected"


@pytest.mark.asyncio
async def test_ready_endpoint_unhealthy_returns_503(app_with_mocks) -> None:
    app, _, mock_redis = app_with_mocks
    # Simulate DB down
    app.state.engine = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["database"] == "unavailable"
