"""Tests for the suggestion review/acceptance routes (S3.4 closure).

These cover the gap that made the product look broken: a straight-through run left
the incident at ``ai_processing_state=awaiting_approval`` but had no interrupt, so
the existing approval routes 404'd and 409'd on it. Nothing could accept a draft,
and because no code wrote ``ai_resolution`` or ``ai_processing_end``, the
``complete`` state was unreachable (dev407364, verified 2026-09-28 across 12
incidents).

The behaviours pinned here are the ones a reviewer would otherwise have to take on
trust: the accept write carries all three fields the field model demands, the
decision is immutable, ``decided_by`` comes from the token and never the body, and
a rejection never fabricates a resolution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

import tests.helpers as h
from app.db.models import Approval, Execution
from app.main import create_app
from app.models.incident import AIProcessingState
from tests.helpers import mock_settings

AUTH_HEADERS = h.AUTH_HEADERS
DRAFTED = "suggested"
SUGGESTION = (
    "1. Unlock the account in the identity console. [KB0005 v4.0 §Resolution]\n"
    "2. Ask the user to sign in again."
)


def _execution(**overrides) -> Execution:
    now = datetime.now(UTC)
    values: dict = dict(
        execution_id=uuid4(),
        event_record_id=uuid4(),
        incident_sys_id="a" * 32,
        status="succeeded",
        termination_cause=DRAFTED,
        started_at=now,
        ended_at=now,
        node_reached="act",
        agent_version="s3.4-graph-1.0.0",
    )
    values.update(overrides)
    return Execution(**values)


def _incident(**overrides) -> SimpleNamespace:
    # Every field snapshot_from_incident() reads, so the stub mirrors the real
    # Incident rather than growing one attribute per failure.
    values: dict = dict(
        sys_id="a" * 32,
        number="INC0010146",
        short_description="Outlook disconnected, no mail delivered",
        description="Outlook shows disconnected and no mail is delivered.",
        priority=5,
        impact=3,
        urgency=3,
        category="inquiry",
        service=None,
        ai_enabled=True,
        ai_suggestion=SUGGESTION,
        ai_confidence=0.95,
        ai_classification="access",
        ai_processing_state=AIProcessingState.AWAITING_APPROVAL,
        ai_processing_start=datetime.now(UTC),
        ai_processing_end=None,
        ai_human_review_required=True,
        ai_human_lock=False,
        ai_resolution=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def app_with_db():
    settings = mock_settings()
    app = create_app(settings=settings)

    mock_session = MagicMock()
    mock_session.get = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.add = MagicMock()
    # A real session answers "no rows" for the already-decided lookup. An
    # unconfigured AsyncMock hands back a truthy mock, which would make every
    # immutability check fire and mask the real behaviour.
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    empty.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = empty

    class _Ctx:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *exc):
            return False

    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock(return_value=_Ctx())
    app.state.redis = MagicMock()
    return app, mock_session


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ────────────────────────────── auth ──────────────────────────────


@pytest.mark.asyncio
async def test_suggestion_routes_require_authentication(app_with_db) -> None:
    app, _ = app_with_db
    eid = uuid4()
    async with _client(app) as client:
        assert (await client.get(f"/api/v1/suggestions/{eid}")).status_code == 401
        decided = await client.post(
            f"/api/v1/suggestions/{eid}/decide", json={"decision": "approved"}
        )
        assert decided.status_code == 401
        assert decided.json()["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_review_rejects_a_decision_other_than_approved_or_rejected(app_with_db) -> None:
    """The decision enum is closed: 'cancelled'/'expired' belong to the paused route."""
    app, _ = app_with_db
    async with _client(app) as client:
        for bad in ("cancelled", "expired", "APPROVED", ""):
            resp = await client.post(
                f"/api/v1/suggestions/{uuid4()}/decide",
                json={"decision": bad},
                headers=AUTH_HEADERS,
            )
            assert resp.status_code == 422, bad


# ────────────────────────────── review ──────────────────────────────


@pytest.mark.asyncio
async def test_review_returns_the_draft_awaiting_acceptance(app_with_db) -> None:
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.get(
                f"/api/v1/suggestions/{execution.execution_id}", headers=AUTH_HEADERS
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestion"] == SUGGESTION
    assert body["confidence"] == 0.95
    assert body["processing_state"] == "awaiting_approval"
    assert body["decided"] is None


@pytest.mark.asyncio
async def test_review_clears_suggestion_when_rejected(app_with_db) -> None:
    """A rejected suggestion is not returned as an active suggestion on review."""
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)
    rejected_approval = Approval(
        id=uuid4(),
        execution_id=execution.execution_id,
        decision="rejected",
        decided_by="barq-operator",
        reason="refused",
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = rejected_approval
    session.execute = AsyncMock(return_value=mock_result)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.get(
                f"/api/v1/suggestions/{execution.execution_id}", headers=AUTH_HEADERS
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestion"] == ""
    assert body["decided"] == "rejected"
    assert body["human_review_required"] is False


@pytest.mark.asyncio
async def test_review_404s_for_an_unknown_execution(app_with_db) -> None:
    app, session = app_with_db
    session.get = AsyncMock(return_value=None)
    async with _client(app) as http:
        resp = await http.get(f"/api/v1/suggestions/{uuid4()}", headers=AUTH_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_review_404s_for_a_run_that_did_not_draft(app_with_db) -> None:
    """An escalated run has no draft here; the paused route serves it.

    404 rather than an empty suggestion, so a caller cannot mistake "this run
    escalated" for "this run drafted nothing".
    """
    app, session = app_with_db
    execution = _execution(termination_cause="escalated_no_evidence")
    session.get = AsyncMock(return_value=execution)
    async with _client(app) as http:
        resp = await http.get(f"/api/v1/suggestions/{execution.execution_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 404
    assert "escalated_no_evidence" in resp.json()["error"]["message"]


# ────────────────────────────── acceptance ──────────────────────────────


@pytest.mark.asyncio
async def test_acceptance_writes_the_one_payload_complete_requires(app_with_db) -> None:
    """The whole point: ai_resolution + complete + processing_end, in one write.

    ``_enforce_validation_rules`` in app/models/incident.py rejects ``complete``
    without both, so before this existed no code path could ever produce it.
    """
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved", "reason": "matches KB0005"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approved"
    assert body["ai_resolution_written"] is True
    assert body["ai_processing_state"] == AIProcessingState.COMPLETE.value
    assert body["ai_processing_end"]

    instance.update_incident.assert_awaited_once()
    payload = instance.update_incident.await_args.args[1]
    # Constructing the payload already ran the validator; assert the contract
    # explicitly so a future relaxation of it is caught here.
    assert payload.ai_processing_state is AIProcessingState.COMPLETE
    assert payload.ai_processing_end is not None
    assert (payload.ai_resolution or "").strip()
    # No operator solution given, so the drafted suggestion is accepted as written.
    assert payload.ai_resolution == SUGGESTION
    assert payload.ai_human_review_required is False


@pytest.mark.asyncio
async def test_operators_own_words_become_the_resolution(app_with_db) -> None:
    """When the operator says what they actually did, that is the resolution.

    This is the S3.5 feedback path: recording the human fix rather than the model's
    proposal is what makes the next suggestion better.
    """
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)
    mine = "Unlocked the account manually and confirmed sign-in works."

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved", "solution": mine},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    payload = instance.update_incident.await_args.args[1]
    assert payload.ai_resolution == mine


@pytest.mark.asyncio
async def test_retry_after_servicenow_write_only_commits_missing_approval(app_with_db) -> None:
    """A failed audit commit cannot cause a second ServiceNow work note."""
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)
    completed_at = datetime.now(UTC)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(
            return_value=_incident(
                ai_processing_state=AIProcessingState.COMPLETE,
                ai_resolution=SUGGESTION,
                ai_processing_end=completed_at,
            )
        )
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    assert resp.json()["ai_processing_end"] == completed_at.isoformat()
    instance.update_incident.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_incident_with_different_resolution_is_not_overwritten(app_with_db) -> None:
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(
            return_value=_incident(
                ai_processing_state=AIProcessingState.COMPLETE,
                ai_resolution="Another operator resolved this incident.",
                ai_processing_end=datetime.now(UTC),
            )
        )
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 409
    instance.update_incident.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_acceptance_fails_loudly_if_serviceNow_refuses(app_with_db) -> None:
    """A decision that could not be applied must stay retryable, not be recorded.

    The approvals table is immutable by trigger, so recording first would make the
    retry hit 409 and strand the incident forever.
    """
    from app.exceptions.servicenow import ServiceNowAuthorizationError

    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock(
            side_effect=ServiceNowAuthorizationError("ACL denies the field")
        )
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 503
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_acceptance_refuses_when_there_is_nothing_to_accept(app_with_db) -> None:
    """Empty draft and no operator solution means nothing to write.

    Must refuse rather than write a blank resolution, which the field model would
    reject anyway and which would assert a resolution nobody gave.
    """
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident(ai_suggestion=""))
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 409
    instance.update_incident.assert_not_awaited()


# ────────────────────────────── rejection ──────────────────────────────


@pytest.mark.asyncio
async def test_rejection_records_the_decision_and_writes_nothing(app_with_db) -> None:
    """A rejected draft must not be dressed up as a resolution.

    There is nothing to record as ``ai_resolution``, so claiming ``complete`` would
    be a fabrication. The incident is left for a human to close.
    """
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "rejected", "reason": "wrong article, ticket is stale"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "rejected"
    assert body["ai_resolution_written"] is False
    assert body["ai_processing_end"] is None
    instance.update_incident.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_rejected_draft_with_an_operator_fix_completes_the_incident(
    app_with_db,
) -> None:
    """Rejected as a suggestion, but the operator did the work: that is a resolution."""
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "rejected", "solution": "Reset the MFA enrolment myself."},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    # Rejecting the AI's proposal while recording the real fix is a completion.
    assert resp.json()["ai_resolution_written"] is True


# ────────────────────────────── audit invariants ──────────────────────────────


@pytest.mark.asyncio
async def test_a_second_decision_is_409(app_with_db) -> None:
    """Approvals are immutable; a contradictory second decision must not be storable."""
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    decided = Approval(
        id=uuid4(),
        execution_id=execution.execution_id,
        decision="approved",
        decided_by="barq-operator",
        decided_at=datetime.now(UTC),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = decided
    session.execute = AsyncMock(return_value=result)

    async with _client(app) as http:
        resp = await http.post(
            f"/api/v1/suggestions/{execution.execution_id}/decide",
            json={"decision": "rejected"},
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 409
    assert "immutable" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_decided_by_comes_from_the_token_not_the_body(app_with_db) -> None:
    """The body must not be able to claim someone else's identity."""
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with patch("api.routers.suggestions.ServiceNowClient") as client_cls:
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={
                    "decision": "approved",
                    "reason": "fine",
                    "decided_by": "someone-else",
                    "decidedBy": "someone-else",
                },
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    assert resp.json()["decided_by"] == "barq-operator"
    # And the impersonation attempt is not even accepted as a field.
    assert "decided_by" not in resp.json() or resp.json()["decided_by"] != "someone-else"


@pytest.mark.asyncio
async def test_a_paused_execution_is_refused_with_a_pointer_to_the_right_route(
    app_with_db,
) -> None:
    """A paused run belongs to /api/v1/approvals, not here. Say so instead of 409-ing blind."""
    app, session = app_with_db
    execution = _execution(status="awaiting_approval", termination_cause=None, ended_at=None)
    session.get = AsyncMock(return_value=execution)

    async with _client(app) as http:
        resp = await http.post(
            f"/api/v1/suggestions/{execution.execution_id}/decide",
            json={"decision": "approved"},
            headers=AUTH_HEADERS,
        )

    assert resp.status_code == 409
    assert "/api/v1/approvals" in resp.json()["error"]["message"]


# ────────────────────────────── knowledge capture ──────────────────────────────


@pytest.mark.asyncio
async def test_an_operator_resolution_is_captured_to_the_kb(app_with_db) -> None:
    """The S3.5 input, and it was missing from this route entirely.

    A straight-through acceptance is the most common way a human supplies a fix, and
    it taught the platform nothing: the approval came back with
    ``knowledge_capture: null`` (dev407364, 2026-09-28, INC0010160). Capture used to
    be reachable only from the paused-approval route, which had an interrupt payload
    to hand over, and that coupling confined learning to one of several paths.
    """
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)
    mine = "Recreated the mail profile so the cache rebuilt; mail flow confirmed."

    captured = SimpleNamespace(ingested=True, article_number="KB1012", point_count=1)
    with (
        patch("api.routers.suggestions.ServiceNowClient") as client_cls,
        patch(
            "api.routers.suggestions.capture_approved_solution",
            AsyncMock(return_value=captured),
        ) as capture,
    ):
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved", "solution": mine},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    assert resp.json()["knowledge_capture"] == {
        "status": "ingested",
        "article_number": "KB1012",
        "point_count": 1,
    }
    capture.assert_awaited_once()
    assert capture.await_args.kwargs["solution"] == mine
    assert capture.await_args.kwargs["incident"].number == "INC0010146"


@pytest.mark.asyncio
async def test_accepting_the_models_own_draft_teaches_nothing(app_with_db) -> None:
    """Acceptance with no operator solution must not become knowledge.

    Re-ingesting the model's own words would let the platform cite itself, inflating
    confidence on suggestions it had no independent evidence for. The gate is the
    operator's solution, never the act of accepting.
    """
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with (
        patch("api.routers.suggestions.ServiceNowClient") as client_cls,
        patch("api.routers.suggestions.capture_approved_solution", AsyncMock()) as capture,
    ):
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved"},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    # The draft was applied, so the incident is still completed...
    assert resp.json()["ai_resolution_written"] is True
    # ...but nothing was learned from the model's own text.
    assert resp.json()["knowledge_capture"] is None
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_capture_failure_does_not_undo_a_recorded_decision(app_with_db) -> None:
    """The decision is already committed and immutable; capture comes after it.

    A KB outage must not fail the acceptance or, worse, leave the operator unable to
    retry behind a 409.
    """
    app, session = app_with_db
    execution = _execution()
    session.get = AsyncMock(return_value=execution)

    with (
        patch("api.routers.suggestions.ServiceNowClient") as client_cls,
        patch(
            "api.routers.suggestions.capture_approved_solution",
            AsyncMock(side_effect=RuntimeError("Qdrant unreachable")),
        ),
    ):
        instance = client_cls.return_value
        instance.get_incident = AsyncMock(return_value=_incident())
        instance.update_incident = AsyncMock()
        instance.aclose = AsyncMock()
        async with _client(app) as http:
            resp = await http.post(
                f"/api/v1/suggestions/{execution.execution_id}/decide",
                json={"decision": "approved", "solution": "Fixed it by hand."},
                headers=AUTH_HEADERS,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approved"
    assert body["ai_resolution_written"] is True
    assert body["knowledge_capture"] == {"status": "failed"}
