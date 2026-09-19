"""Tests for Evaluation and Benchmark router (POST /api/v1/eval/run, GET /api/v1/eval/results)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.schemas.eval import EvalResultResponse, EvalRunResponse
from app.main import create_app
import tests.helpers as h
from tests.helpers import mock_settings

AUTH_HEADERS = h.AUTH_HEADERS


@pytest.fixture
def app_instance():
    """Create test application instance with mocked resources."""
    settings = mock_settings()
    app = create_app(settings=settings)
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.redis = MagicMock()
    return app


@pytest.mark.asyncio
async def test_eval_endpoints_require_auth(app_instance) -> None:
    """Ensure both eval endpoints reject unauthorized calls with 401."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp_post = await client.post("/api/v1/eval/run", json={"dataset_name": "bench-v1"})
        assert resp_post.status_code == 401

        resp_get = await client.get("/api/v1/eval/results")
        assert resp_get.status_code == 401


@pytest.mark.asyncio
async def test_eval_run_validates_contract(app_instance) -> None:
    """Ensure POST /api/v1/eval/run rejects payloads missing dataset_name with 422."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        # Missing required dataset_name
        resp = await client.post("/api/v1/eval/run", json={"sample_size": 10}, headers=AUTH_HEADERS)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_eval_run_rejects_extra_fields(app_instance) -> None:
    """Ensure POST /api/v1/eval/run rejects extra fields due to extra='forbid'."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/eval/run",
            json={"dataset_name": "bench-v1", "unexpected_field": "disallowed"},
            headers=AUTH_HEADERS,
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_eval_run_succeeds(app_instance) -> None:
    """Ensure POST /api/v1/eval/run accepts valid requests with 202 Accepted."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/eval/run",
            json={"dataset_name": "incident-corpus-gold-v1", "sample_size": 50},
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 202
    data = resp.json()
    validated = EvalRunResponse.model_validate(data)
    assert validated.status == "started"
    assert validated.run_id.startswith("eval-")


@pytest.mark.asyncio
async def test_eval_results_returns_metrics(app_instance) -> None:
    """Ensure GET /api/v1/eval/results returns 200 with schema-valid metrics."""
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/eval/results?run_id=eval-run-456", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    validated = EvalResultResponse.model_validate(data[0])
    assert validated.run_id == "eval-run-456"
    assert 0.0 <= validated.benchmark_score <= 1.0
    assert 0.0 <= validated.accuracy <= 1.0
    assert validated.p95_latency_ms > 0
