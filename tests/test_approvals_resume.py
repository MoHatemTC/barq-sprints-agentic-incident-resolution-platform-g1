"""The decide endpoint resumes the execution it paused (S3.4, FR-17).

This is the loop the brief calls out as missing: the graph genuinely parks, the
API resumes *that* thread with ``Command(resume=...)``, and the approval row is
written only once the graph has taken the decision.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

import tests.helpers as h
from agent.audit_store import MemoryGraphAuditStore
from agent.runtime import build_runtime, invoke_incident_graph, resume_incident_graph
from api.routers import approvals as approvals_router
from app.db.models import Approval, Execution
from app.main import create_app
from tests.agent_support import (
    EXECUTION_ID,
    ORDER_P1,
    FakeLLM,
    FakeServiceNow,
    event_for,
    make_deps,
    vpn_answers,
)
from tests.helpers import mock_settings

AUTH_HEADERS = h.AUTH_HEADERS


@pytest.fixture
def app_with_db():
    """Test application with a mocked database session (mirrors test_approvals)."""
    app = create_app(settings=mock_settings())

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

    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock(return_value=MockAsyncSessionContext())
    app.state.redis = MagicMock()
    return app, mock_session


def _parked_execution(store: MemoryGraphAuditStore, backend: FakeServiceNow):
    """Run a P1 through the graph and leave it paused in act."""
    deps = make_deps(llm=FakeLLM(vpn_answers()), servicenow=backend)
    deps.audit = store
    runtime = build_runtime(deps, checkpointer=InMemorySaver())
    result = invoke_incident_graph(
        event_for(ORDER_P1),
        runtime=runtime,
        execution_id=EXECUTION_ID,
        correlation_id="corr-api-resume",
        attempt=1,
    )
    assert result["paused"] is True
    assert backend.updates == []
    return runtime, deps


@pytest.mark.asyncio
async def test_decide_resumes_the_parked_execution(app_with_db) -> None:
    store = MemoryGraphAuditStore()
    backend = FakeServiceNow()
    runtime, deps = _parked_execution(store, backend)

    app, mock_session = app_with_db
    execution = Execution(
        execution_id=UUID(EXECUTION_ID),
        event_record_id=uuid4(),
        incident_sys_id=ORDER_P1["sys_id"],
        status="awaiting_approval",
    )

    async def mock_get(model, pk):
        if model is Approval:
            return None
        if model is Execution and pk == UUID(EXECUTION_ID):
            return execution
        return None

    mock_session.get.side_effect = mock_get

    payload = {"decision": "approved", "decided_by": "ops_analyst_1", "reason": "P1 change window"}
    with (
        patch.object(approvals_router, "get_audit_store", return_value=store),
        patch.object(
            approvals_router,
            "resume_incident_graph",
            side_effect=lambda **kw: resume_incident_graph(**kw, runtime=runtime),
        ) as resume,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/approvals/{EXECUTION_ID}/decide", json=payload, headers=AUTH_HEADERS
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "approved"
    assert body["facts"]["outcome"] == "escalated_high_risk"
    assert body["brief"]["judgment_required"]

    # The decision reached the very thread that paused, and only then wrote.
    assert resume.call_count == 1
    assert resume.call_args.kwargs["execution_id"] == EXECUTION_ID
    assert resume.call_args.kwargs["decision"]["decision"] == "approved"
    assert len(backend.updates) == 1

    # The approval row is recorded against that execution.
    mock_session.add.assert_called_once()
    recorded = mock_session.add.call_args[0][0]
    assert isinstance(recorded, Approval)
    assert recorded.execution_id == UUID(EXECUTION_ID)
    assert recorded.decision == "approved"
    mock_session.commit.assert_awaited_once()

    # The audit receipt distinguishes this from a direct run.
    receipt = deps.audit.get_receipt(EXECUTION_ID)
    assert receipt is not None
    assert receipt["lifecycle"] == "interrupt_resume"


@pytest.mark.asyncio
async def test_decide_without_a_pause_keeps_the_stub_contract(app_with_db) -> None:
    """No interrupt stored → nothing to resume, and the S2.1 stub still answers."""
    store = MemoryGraphAuditStore()
    app, mock_session = app_with_db
    mock_session.get.return_value = None

    with (
        patch.object(approvals_router, "get_audit_store", return_value=store),
        patch.object(approvals_router, "resume_incident_graph") as resume,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/approvals/{uuid4()}/decide",
                json={"decision": "approved", "decided_by": "lead_ops", "reason": "ok"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    assert resp.json()["decision"] == "approved"
    resume.assert_not_called()
    mock_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_pending_endpoint_returns_the_brief_before_a_decision(app_with_db) -> None:
    """A reviewer must be able to read the brief *before* deciding (FR-17)."""
    store = MemoryGraphAuditStore()
    backend = FakeServiceNow()
    _parked_execution(store, backend)

    app, _ = app_with_db
    with patch.object(approvals_router, "get_audit_store", return_value=store):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/approvals/pending/{EXECUTION_ID}", headers=AUTH_HEADERS
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_approval"
    assert body["decision"] is None
    assert body["brief"]["judgment_required"]
    assert body["facts"]["outcome"] == "escalated_high_risk"
    assert body["facts"]["incident"]["number"] == ORDER_P1["number"]
    assert backend.updates == []
