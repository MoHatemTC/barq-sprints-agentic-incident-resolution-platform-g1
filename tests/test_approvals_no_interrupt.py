"""A decision needs a paused thread to apply it to.

``awaiting_approval`` on the executions row is not proof that a LangGraph interrupt
exists: semantic-cache waiters, runs from before the interrupt/resume fix and
orphaned rows all carry that status. Recording an immutable approval for such a run
asserts that a human approved something nobody was waiting on, and the immutable
trigger means it can never be corrected.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

import tests.helpers as h
from agent.audit_store import MemoryGraphAuditStore
from api.routers import approvals as approvals_router
from app.db.models import Approval, Execution
from tests.test_approvals_resume import app_with_db  # noqa: F401  (pytest fixture)

EXECUTION_ID = str(uuid4())


@pytest.mark.asyncio
async def test_decide_without_a_paused_thread_is_refused_and_records_nothing(
    app_with_db,  # noqa: F811
) -> None:
    app, session = app_with_db
    execution = Execution(
        execution_id=UUID(EXECUTION_ID),
        event_record_id=uuid4(),
        incident_sys_id="a" * 32,
        status="awaiting_approval",
    )

    async def get(model, pk):
        if model is Approval:
            return None
        return execution if model is Execution and pk == UUID(EXECUTION_ID) else None

    session.get.side_effect = get

    with patch.object(approvals_router, "get_audit_store", return_value=MemoryGraphAuditStore()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/v1/approvals/{EXECUTION_ID}/decide",
                json={"decision": "approved", "reason": "nothing is paused"},
                headers=h.AUTH_HEADERS,
            )

    assert response.status_code == 409, response.text
    session.add.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_decide_rejects_unbounded_reason_and_evidence(app_with_db) -> None:  # noqa: F811
    app, _session = app_with_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        too_long = await client.post(
            f"/api/v1/approvals/{EXECUTION_ID}/decide",
            json={"decision": "approved", "reason": "x" * 20_000},
            headers=h.AUTH_HEADERS,
        )
        too_big = await client.post(
            f"/api/v1/approvals/{EXECUTION_ID}/decide",
            json={"decision": "approved", "evidence": {"k": "v" * 200_000}},
            headers=h.AUTH_HEADERS,
        )

    assert too_long.status_code == 422, too_long.text
    assert too_big.status_code == 422, too_big.text
