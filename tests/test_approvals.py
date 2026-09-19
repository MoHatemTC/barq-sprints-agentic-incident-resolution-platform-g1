"""Tests for HITL approvals router endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Approval, Execution
from app.main import create_app
import tests.helpers as h
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
async def test_approvals_require_authentication(app_with_db) -> None:
    app, _ = app_with_db
    approval_id = uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. GET /api/v1/approvals without auth
        resp = await client.get("/api/v1/approvals")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        # 2. GET /api/v1/approvals with invalid token
        resp_invalid = await client.get(
            "/api/v1/approvals",
            headers={"Authorization": "Bearer invalid-token-xyz"},
        )
        assert resp_invalid.status_code == 401
        assert resp_invalid.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        # 3. GET /api/v1/approvals/{id} without auth
        resp = await client.get(f"/api/v1/approvals/{approval_id}")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        # 4. POST /api/v1/approvals/{id}/decide without auth
        resp = await client.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json={"decision": "approved", "decided_by": "operator@test.com"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_approvals_invalid_uuid_returns_422(app_with_db) -> None:
    """Ensure malformed UUID in path returns 422 contract validation failure."""
    app, _ = app_with_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/approvals/not-a-valid-uuid", headers=AUTH_HEADERS)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_approvals_correlation_id_propagated(app_with_db) -> None:
    """Ensure X-Correlation-ID header is echoed or generated and returned."""
    app, mock_session = app_with_db
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    custom_corr_id = "test-corr-id-12345"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/approvals",
            headers={**AUTH_HEADERS, "X-Correlation-ID": custom_corr_id},
        )
    assert resp.status_code == 200
    assert resp.headers.get("X-Correlation-ID") == custom_corr_id


@pytest.mark.asyncio
async def test_list_approvals_empty(app_with_db) -> None:
    app, mock_session = app_with_db

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/approvals", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_approvals_with_records_and_filter(app_with_db) -> None:
    app, mock_session = app_with_db

    approval_id = uuid4()
    execution_id = uuid4()
    sample_approval = Approval(
        id=approval_id,
        execution_id=execution_id,
        workflow_state_id=None,
        decision="approved",
        decided_by="lead_ops",
        reason="Verified safety constraints",
        evidence={"risk_score": 0.12},
        decided_at=datetime.now(UTC),
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [sample_approval]
    mock_session.execute.return_value = mock_result

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Filter by execution_id
        resp = await client.get(
            f"/api/v1/approvals?execution_id={execution_id}",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == str(approval_id)
        assert data[0]["execution_id"] == str(execution_id)
        assert data[0]["decision"] == "approved"
        assert data[0]["decided_by"] == "lead_ops"

        # Filter by decided_by
        resp_user = await client.get(
            "/api/v1/approvals?decided_by=lead_ops",
            headers=AUTH_HEADERS,
        )
        assert resp_user.status_code == 200
        data_user = resp_user.json()
        assert len(data_user) == 1
        assert data_user[0]["decided_by"] == "lead_ops"

        # Filter by both execution_id and decided_by
        resp_both = await client.get(
            f"/api/v1/approvals?execution_id={execution_id}&decided_by=lead_ops",
            headers=AUTH_HEADERS,
        )
        assert resp_both.status_code == 200
        assert len(resp_both.json()) == 1


@pytest.mark.asyncio
async def test_get_approval_by_id_success(app_with_db) -> None:
    app, mock_session = app_with_db

    approval_id = uuid4()
    execution_id = uuid4()
    sample_approval = Approval(
        id=approval_id,
        execution_id=execution_id,
        workflow_state_id=None,
        decision="rejected",
        decided_by="admin",
        reason="Remediation risky",
        evidence={"blast_radius": "high"},
        decided_at=datetime.now(UTC),
    )
    mock_session.get.return_value = sample_approval

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/approvals/{approval_id}", headers=AUTH_HEADERS)

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(approval_id)
    assert data["decision"] == "rejected"
    assert data["decided_by"] == "admin"
    assert data["reason"] == "Remediation risky"


@pytest.mark.asyncio
async def test_get_approval_by_id_not_found(app_with_db) -> None:
    app, mock_session = app_with_db
    approval_id = uuid4()
    mock_session.get.return_value = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/approvals/{approval_id}", headers=AUTH_HEADERS)

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert f"Approval '{approval_id}' not found" in body["error"]["message"]


@pytest.mark.asyncio
async def test_decide_approval_rejects_invalid_decision_values(app_with_db) -> None:
    app, _ = app_with_db
    approval_id = uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json={"decision": "unknown_status", "decided_by": "operator"},
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_decide_approval_rejects_extra_fields(app_with_db) -> None:
    app, _ = app_with_db
    approval_id = uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json={
                "decision": "approved",
                "decided_by": "operator",
                "unauthorized_extra_field": "hack",
            },
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_decide_approval_stub_fallback(app_with_db) -> None:
    """When neither Approval nor Execution is in DB, contract stub returns schema-valid response."""
    app, mock_session = app_with_db
    approval_id = uuid4()
    mock_session.get.return_value = None

    payload = {
        "decision": "approved",
        "decided_by": "ops_analyst_1",
        "reason": "Safe to proceed with restart",
        "evidence": {"service": "redis", "memory_ok": True},
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json=payload,
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(approval_id)
    assert data["decision"] == "approved"
    assert data["decided_by"] == "ops_analyst_1"
    assert data["reason"] == "Safe to proceed with restart"
    assert data["evidence"]["service"] == "redis"
    assert data["decided_at"] is not None


@pytest.mark.asyncio
async def test_decide_approval_rejects_mutation_of_existing_approval(app_with_db) -> None:
    """Ensure deciding already-decided approval returns 409 Conflict due to audit immutability."""
    app, mock_session = app_with_db
    approval_id = uuid4()
    execution_id = uuid4()

    existing_approval = Approval(
        id=approval_id,
        execution_id=execution_id,
        workflow_state_id=None,
        decision="cancelled",
        decided_by="system",
        reason=None,
        evidence=None,
        decided_at=datetime.now(UTC),
    )

    async def mock_get(model, pk):
        if model is Approval and pk == approval_id:
            return existing_approval
        return None

    mock_session.get.side_effect = mock_get

    payload = {
        "decision": "approved",
        "decided_by": "lead_operator",
        "reason": "Manual override approved",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json=payload,
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "RESOURCE_CONFLICT"
    assert f"Approval '{approval_id}' has already been decided" in body["error"]["message"]
    mock_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_decide_approval_creates_for_existing_execution(app_with_db) -> None:
    app, mock_session = app_with_db
    target_id = uuid4()

    existing_execution = Execution(
        execution_id=target_id,
        event_record_id=uuid4(),
        incident_sys_id="0123456789abcdef0123456789abcdef",
        status="awaiting_approval",
    )

    async def mock_get(model, pk):
        if model is Approval:
            return None
        if model is Execution and pk == target_id:
            return existing_execution
        return None

    mock_session.get.side_effect = mock_get

    payload = {
        "decision": "rejected",
        "decided_by": "security_officer",
        "reason": "Risk threshold exceeded",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/approvals/{target_id}/decide",
            json=payload,
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 200
    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited_once()
    created_approval = mock_session.add.call_args[0][0]
    assert isinstance(created_approval, Approval)
    assert created_approval.execution_id == target_id
    assert created_approval.decision == "rejected"
    assert created_approval.decided_by == "security_officer"


@pytest.mark.asyncio
async def test_approvals_db_failure_returns_503(app_with_db) -> None:
    """Verify that actual SQLAlchemy database errors raise ServiceUnavailableError returning 503."""
    from sqlalchemy.exc import OperationalError

    app, mock_session = app_with_db
    approval_id = uuid4()
    db_err = OperationalError("SELECT 1", {}, Exception("connection closed"))

    # 1. GET /api/v1/approvals failure
    mock_session.execute.side_effect = db_err
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/approvals", headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # 2. GET /api/v1/approvals/{id} failure
    mock_session.get.side_effect = db_err
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/v1/approvals/{approval_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    # 3. POST /api/v1/approvals/{id}/decide failure
    mock_session.get.side_effect = db_err
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json={"decision": "approved", "decided_by": "operator"},
            headers=AUTH_HEADERS,
        )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_approvals_programming_errors_not_masked(app_with_db) -> None:
    """Ensure programming bugs (MissingGreenlet, AttributeError) are NOT converted to 503."""
    from sqlalchemy.exc import MissingGreenlet

    app, mock_session = app_with_db

    # MissingGreenlet should bubble up as 500, not 503
    mock_session.execute.side_effect = MissingGreenlet("async lazy loading violation")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(MissingGreenlet):
            await client.get("/api/v1/approvals", headers=AUTH_HEADERS)

    # AttributeError should bubble up as 500, not 503
    mock_session.get.side_effect = AttributeError("'NoneType' object has no attribute 'val'")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(AttributeError):
            await client.get(f"/api/v1/approvals/{uuid4()}", headers=AUTH_HEADERS)
