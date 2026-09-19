"""Tests for executions router endpoints and error handling."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

import tests.helpers as h
from app.db.models import Execution, ExecutionNodeState
from app.main import create_app
from tests.helpers import mock_settings

AUTH_HEADERS = h.AUTH_HEADERS


@pytest.fixture
def app_with_db():
    """Create test FastAPI application with mocked database session."""
    settings = mock_settings()
    app = create_app(settings=settings)

    mock_session = MagicMock()
    mock_session.get = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.add = MagicMock()

    class MockAsyncSessionContext:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_factory = MagicMock(return_value=MockAsyncSessionContext())

    app.state.engine = MagicMock()
    app.state.session_factory = mock_factory
    app.state.redis = MagicMock()

    return app, mock_session


@pytest.mark.asyncio
async def test_executions_require_authentication(app_with_db) -> None:
    app, _ = app_with_db
    exec_id = uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Missing token
        r1 = await client.get(f"/api/v1/executions/{exec_id}")
        assert r1.status_code == 401
        assert r1.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        # Invalid token
        r_invalid = await client.get(
            f"/api/v1/executions/{exec_id}",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert r_invalid.status_code == 401
        assert r_invalid.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        r2 = await client.get(f"/api/v1/executions/{exec_id}/trace")
        assert r2.status_code == 401
        assert r2.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        r3 = await client.get(f"/api/v1/incidents/{'a' * 32}/executions")
        assert r3.status_code == 401
        assert r3.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_executions_invalid_uuid_returns_422(app_with_db) -> None:
    """Ensure malformed UUID path parameters return 422 contract validation failure."""
    app, _ = app_with_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/api/v1/executions/not-a-uuid", headers=AUTH_HEADERS)
        assert r1.status_code == 422
        assert r1.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"

        r2 = await client.get("/api/v1/executions/not-a-uuid/trace", headers=AUTH_HEADERS)
        assert r2.status_code == 422
        assert r2.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_executions_correlation_id_propagated(app_with_db) -> None:
    """Ensure X-Correlation-ID header is propagated across executions endpoints."""
    app, mock_session = app_with_db
    mock_session.get.return_value = None
    custom_corr = "execution-trace-test-corr-id"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/executions/{uuid4()}",
            headers={**AUTH_HEADERS, "X-Correlation-ID": custom_corr},
        )
    assert resp.headers.get("X-Correlation-ID") == custom_corr


@pytest.mark.asyncio
async def test_get_execution_success_and_not_found(app_with_db) -> None:
    app, mock_session = app_with_db
    exec_id = uuid4()
    sample = Execution(
        execution_id=exec_id,
        event_record_id=uuid4(),
        incident_sys_id="0123456789abcdef0123456789abcdef",
        status="running",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # Success
    mock_session.get.return_value = sample
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/executions/{exec_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["execution_id"] == str(exec_id)

    # Not found
    mock_session.get.return_value = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/executions/{exec_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_execution_trace_success_and_not_found(app_with_db) -> None:
    app, mock_session = app_with_db
    exec_id = uuid4()
    sample_exec = Execution(
        execution_id=exec_id,
        event_record_id=uuid4(),
        incident_sys_id="0123456789abcdef0123456789abcdef",
        status="succeeded",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    node_id = uuid4()
    sample_node = ExecutionNodeState(
        id=node_id,
        execution_id=exec_id,
        node_name="triage",
        status="succeeded",
        sequence_number=1,
        attempt=1,
        started_at=datetime.now(UTC),
        evidence=[],
    )

    # Success
    mock_session.get.return_value = sample_exec
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [sample_node]
    mock_session.execute.return_value = mock_result

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/executions/{exec_id}/trace", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_id"] == str(exec_id)
    assert len(data["node_states"]) == 1
    assert data["node_states"][0]["node_name"] == "triage"

    # Not found
    mock_session.get.return_value = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/executions/{exec_id}/trace", headers=AUTH_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_incident_executions(app_with_db) -> None:
    app, mock_session = app_with_db
    sys_id = "0123456789abcdef0123456789abcdef"
    sample = Execution(
        execution_id=uuid4(),
        event_record_id=uuid4(),
        incident_sys_id=sys_id,
        status="succeeded",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [sample]
    mock_session.execute.return_value = mock_result

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/incidents/{sys_id}/executions", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["incident_sys_id"] == sys_id
    assert len(data["executions"]) == 1


@pytest.mark.asyncio
async def test_executions_db_failure_returns_503(app_with_db) -> None:
    """Verify that actual SQLAlchemy database errors raise ServiceUnavailableError returning 503."""
    from sqlalchemy.exc import OperationalError

    app, mock_session = app_with_db
    exec_id = uuid4()
    sys_id = "0123456789abcdef0123456789abcdef"
    db_err = OperationalError("SELECT 1", {}, Exception("DB timeout"))

    # 1. GET /api/v1/executions/{id}
    mock_session.get.side_effect = db_err
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/executions/{exec_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # 2. GET /api/v1/executions/{id}/trace (execution lookup failure)
    mock_session.get.side_effect = db_err
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/executions/{exec_id}/trace", headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # 3. GET /api/v1/executions/{id}/trace (trace nodes query failure)
    sample_exec = Execution(
        execution_id=exec_id,
        event_record_id=uuid4(),
        incident_sys_id=sys_id,
        status="succeeded",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    mock_session.get.side_effect = None
    mock_session.get.return_value = sample_exec
    mock_session.execute.side_effect = db_err
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/executions/{exec_id}/trace", headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # 4. GET /api/v1/incidents/{sys_id}/executions
    mock_session.execute.side_effect = db_err
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/incidents/{sys_id}/executions", headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_executions_programming_errors_not_masked(app_with_db) -> None:
    """Ensure programming bugs (MissingGreenlet, AttributeError) bubble up rather than 503."""
    from sqlalchemy.exc import MissingGreenlet

    app, mock_session = app_with_db

    mock_session.get.side_effect = MissingGreenlet("async lazy loading violation")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(MissingGreenlet):
            await client.get(f"/api/v1/executions/{uuid4()}", headers=AUTH_HEADERS)

    mock_session.get.side_effect = AttributeError("'Execution' object has no attribute 'missing'")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(AttributeError):
            await client.get(f"/api/v1/executions/{uuid4()}", headers=AUTH_HEADERS)
