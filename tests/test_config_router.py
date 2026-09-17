"""Tests for runtime configuration router (GET /api/v1/config) and secret redaction."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from api.schemas.config import REDACTED_SENTINEL, RedactedConfigResponse
from app.main import create_app
from tests.helpers import mock_settings

VALID_TOKEN = "dev-webhook-secret-token"
AUTH_HEADERS = {"Authorization": f"Bearer {VALID_TOKEN}"}

RAW_SERVICENOW_PW = "raw-secret-sn-pw-xyz"
RAW_CLIENT_SECRET = "raw-client-secret-abc-123"
RAW_POSTGRES_PW = "raw-pg-secret-pw-456"
RAW_REDIS_PW = "raw-redis-secret-pw-789"


@pytest.fixture
def app_with_secrets():
    """Create test application configured with explicit raw secrets."""
    settings = mock_settings(
        webhook_auth_token=VALID_TOKEN,
        servicenow_password=SecretStr(RAW_SERVICENOW_PW),
        servicenow_client_secret=SecretStr(RAW_CLIENT_SECRET),
        postgres_password=SecretStr(RAW_POSTGRES_PW),
        redis_password=SecretStr(RAW_REDIS_PW),
    )
    app = create_app(settings=settings)
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.redis = MagicMock()
    return app


@pytest.mark.asyncio
async def test_config_requires_authentication(app_with_secrets) -> None:
    """Ensure GET /api/v1/config rejects unauthorized requests with 401."""
    app = app_with_secrets

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Missing auth header
        resp_missing = await client.get("/api/v1/config")
        assert resp_missing.status_code == 401
        assert resp_missing.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        # Invalid auth token
        resp_invalid = await client.get(
            "/api/v1/config",
            headers={"Authorization": "Bearer totally-wrong-token"},
        )
        assert resp_invalid.status_code == 401
        assert resp_invalid.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_config_redacts_all_runtime_secrets(app_with_secrets) -> None:
    """Ensure all sensitive credentials are strictly redacted to '***REDACTED***'."""
    app = app_with_secrets

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/config", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    data = resp.json()

    # 1. Verify schema contract validity
    validated = RedactedConfigResponse.model_validate(data)
    assert validated.app_name == "incident-resolution-platform"
    assert validated.environment == "development"
    assert validated.retrieval_mode == "hybrid"
    assert validated.active_feature_flags.get("hitl_approvals") is True
    assert data["retrieval_mode"] == "hybrid"
    assert isinstance(data["active_feature_flags"], dict)

    # 2. Verify all required runtime secrets are redacted
    assert data["servicenow_password"] == REDACTED_SENTINEL
    assert data["client_secret"] == REDACTED_SENTINEL
    assert data["servicenow_client_secret"] == REDACTED_SENTINEL
    assert data["postgres_password"] == REDACTED_SENTINEL
    assert data["redis_password"] == REDACTED_SENTINEL
    assert data["webhook_auth_token"] == REDACTED_SENTINEL
    assert "webhook_secret" not in data

    # 3. Assert that NO raw secret string leaked anywhere in the raw response text
    raw_response_text = json.dumps(data)
    assert RAW_SERVICENOW_PW not in raw_response_text
    assert RAW_CLIENT_SECRET not in raw_response_text
    assert RAW_POSTGRES_PW not in raw_response_text
    assert RAW_REDIS_PW not in raw_response_text
    assert VALID_TOKEN not in raw_response_text


@pytest.mark.asyncio
async def test_config_correlation_id_propagated(app_with_secrets) -> None:
    """Ensure correlation ID header is preserved on config endpoint."""
    app = app_with_secrets
    custom_corr = "config-audit-corr-id-99"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/config",
            headers={**AUTH_HEADERS, "X-Correlation-ID": custom_corr},
        )

    assert resp.status_code == 200
    assert resp.headers.get("X-Correlation-ID") == custom_corr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["dense_only", "hybrid", "hybrid_reranked"],
)
async def test_config_retrieval_mode_variants(mode: str) -> None:
    """Ensure all RetrievalMode variants ('dense_only', 'hybrid', 'hybrid_reranked') are supported."""
    from app.core.config import RetrievalMode

    settings = mock_settings(
        webhook_auth_token=VALID_TOKEN,
        retrieval_mode=RetrievalMode(mode),
    )
    app = create_app(settings=settings)
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.redis = MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/config", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["retrieval_mode"] == mode

