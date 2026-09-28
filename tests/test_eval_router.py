"""Tests for Evaluation and Benchmark router (POST /api/v1/eval/run, GET /api/v1/eval/results)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import tests.helpers as h
from app.main import create_app
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
async def test_eval_run_refuses_while_the_flag_is_off(app_instance) -> None:
    """POST /api/v1/eval/run must refuse, not accept-and-discard.

    The Sprint 2 stub returned 202 with a generated run_id that never appeared in
    /results and produced no worker activity. `eval_benchmarks` ships False, so the
    request is refused with 501 and a reason a caller can act on.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/eval/run",
            json={"dataset_name": "incident-corpus-gold-v1", "sample_size": 50},
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_eval_results_are_empty_and_stable(app_instance) -> None:
    """GET /api/v1/eval/results returns an empty list, identically on every read.

    The stub returned a fixed 0.92 / 0.94 / 342.5 ms with `completed_at` taken from
    now(), so consecutive reads disagreed on when one unchanging score completed.
    An empty list is the truthful answer and is stable by construction.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app_instance), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/eval/results?run_id=eval-run-456", headers=AUTH_HEADERS)
        second = await client.get("/api/v1/eval/results?run_id=eval-run-456", headers=AUTH_HEADERS)

    assert first.status_code == 200
    assert first.json() == []
    assert second.json() == first.json()
