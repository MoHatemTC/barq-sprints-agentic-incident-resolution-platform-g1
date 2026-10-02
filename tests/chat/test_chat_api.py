"""Router contract tests for /api/v1/chat (mocked DB, service, no network)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

import tests.helpers as h
from app.chat.config import ChatSettings
from app.chat.dependencies import get_chat_service
from app.chat.service import ChatTurnService, TurnOutcome
from app.db.models import ChatConversation, ChatMessage, ChatSession, ChatTurn
from app.main import create_app
from tests.helpers import build_session_factory, mock_settings

OPERATOR_SUBJECT = "barq-operator"
CHAT_SECRET = secrets.token_urlsafe(16)
REQUEST_ID = "req-abc-12345"

_ENABLED_SETTINGS = ChatSettings(
    _env_file=None,
    chat_enabled=True,
    chat_price_input_per_mtok=0.5,
    chat_price_output_per_mtok=2.0,
)


def _now():
    return datetime.now(UTC)


def _session_row() -> ChatSession:
    return ChatSession(
        id=uuid4(),
        operator_subject=OPERATOR_SUBJECT,
        secret_hash=hashlib.sha256(CHAT_SECRET.encode()).hexdigest(),
        created_at=_now(),
    )


def _conversation_row(session_id: UUID) -> ChatConversation:
    return ChatConversation(
        id=uuid4(),
        session_id=session_id,
        operator_subject=OPERATOR_SUBJECT,
        title="New conversation",
        created_at=_now(),
        updated_at=_now(),
    )


def _turn_row(conversation_id: UUID, *, status: str = "succeeded") -> ChatTurn:
    return ChatTurn(
        id=uuid4(),
        conversation_id=conversation_id,
        request_id=REQUEST_ID,
        route="knowledge" if status == "succeeded" else None,
        status=status,
        created_at=_now(),
        completed_at=_now() if status != "running" else None,
        usage={"model_calls": 4},
    )


def _message_row(conversation_id: UUID, turn_id: UUID, seq: int, role: str) -> ChatMessage:
    return ChatMessage(
        id=uuid4(),
        conversation_id=conversation_id,
        turn_id=turn_id,
        seq=seq,
        role=role,
        content=f"{role} content {seq}",
        citations=[],
        created_at=_now(),
    )


def _db_session(get_values: dict) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, pk: get_values.get(model))
    session.execute = AsyncMock(return_value=_execute_result([]))
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    async def _refresh(row, *args, **kwargs):
        # Emulate the server defaults a real refresh reads back.
        if getattr(row, "created_at", None) is None:
            row.created_at = _now()
        if getattr(row, "updated_at", None) is None:
            row.updated_at = _now()

    session.refresh = AsyncMock(side_effect=_refresh)
    session.delete = AsyncMock()
    return session


def _execute_result(rows: list):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    return result


def _integrity_error(constraint: str) -> IntegrityError:
    error = IntegrityError("INSERT", {}, Exception("duplicate"))
    error.orig = SimpleNamespace(constraint_name=constraint)
    return error


def _fake_service(outcome: TurnOutcome | None = None) -> MagicMock:
    service = MagicMock(spec=ChatTurnService)
    service.handle_turn = MagicMock(
        return_value=outcome
        or TurnOutcome(status="succeeded", route="knowledge", error_category=None)
    )
    return service


def _app(session: MagicMock, service: MagicMock, settings: ChatSettings | None = None):
    app = create_app(settings=mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN))
    app.state.session_factory = build_session_factory(session)
    app.state.chat_settings = settings or _ENABLED_SETTINGS
    app.dependency_overrides[get_chat_service] = lambda: service
    return app


def _auth() -> dict:
    return {"Authorization": f"Bearer {h.make_operator_token()}"}


def _session_headers(session_row: ChatSession) -> dict:
    return {
        **_auth(),
        "X-Chat-Session-Id": str(session_row.id),
        "X-Chat-Session-Secret": CHAT_SECRET,
    }


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# -- sessions ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_chat_answers_503_even_with_a_valid_token() -> None:
    app = _app(
        _db_session({}),
        _fake_service(),
        ChatSettings(_env_file=None, chat_enabled=False),
    )
    async with await _client(app) as client:
        resp = await client.post("/api/v1/chat/sessions", headers=_auth())

    assert resp.status_code == 503
    assert "disabled" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_create_session_returns_secret_once() -> None:
    app = _app(_db_session({}), _fake_service())
    async with await _client(app) as client:
        resp = await client.post("/api/v1/chat/sessions", headers=_auth())

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["chat_secret"]
    UUID(body["session_id"])


@pytest.mark.asyncio
async def test_missing_session_headers_answer_401() -> None:
    session_row = _session_row()
    app = _app(_db_session({ChatSession: session_row}), _fake_service())
    async with await _client(app) as client:
        resp = await client.get("/api/v1/chat/conversations", headers=_auth())
        wrong = await client.get(
            "/api/v1/chat/conversations",
            headers={
                **_auth(),
                "X-Chat-Session-Id": str(session_row.id),
                "X-Chat-Session-Secret": "nope",
            },
        )

    assert resp.status_code == 401
    assert wrong.status_code == 401


# -- conversations -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_conversations() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    session = _db_session({ChatSession: session_row})
    session.execute = AsyncMock(return_value=_execute_result([conversation]))
    app = _app(session, _fake_service())
    headers = _session_headers(session_row)

    async with await _client(app) as client:
        created = await client.post(
            "/api/v1/chat/conversations", json={"title": "KER question"}, headers=headers
        )
        listed = await client.get("/api/v1/chat/conversations", headers=headers)

    assert created.status_code == 201, created.text
    assert created.json()["title"] == "KER question"
    assert [c["id"] for c in listed.json()] == [str(conversation.id)]


@pytest.mark.asyncio
async def test_cross_session_conversation_answers_404() -> None:
    session_row = _session_row()
    other_conversation = _conversation_row(uuid4())  # belongs to another session
    app = _app(
        _db_session({ChatSession: session_row, ChatConversation: other_conversation}),
        _fake_service(),
    )

    async with await _client(app) as client:
        resp = await client.get(
            f"/api/v1/chat/conversations/{other_conversation.id}/messages",
            headers=_session_headers(session_row),
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_conversation_returns_204() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    app = _app(
        _db_session({ChatSession: session_row, ChatConversation: conversation}), _fake_service()
    )

    async with await _client(app) as client:
        resp = await client.delete(
            f"/api/v1/chat/conversations/{conversation.id}", headers=_session_headers(session_row)
        )

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_rename_conversation_updates_title() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    app = _app(
        _db_session({ChatSession: session_row, ChatConversation: conversation}), _fake_service()
    )
    headers = _session_headers(session_row)

    async with await _client(app) as client:
        renamed = await client.patch(
            f"/api/v1/chat/conversations/{conversation.id}",
            json={"title": "KER question"},
            headers=headers,
        )
        empty = await client.patch(
            f"/api/v1/chat/conversations/{conversation.id}", json={"title": "  "}, headers=headers
        )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "KER question"
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_rename_other_sessions_conversation_answers_404() -> None:
    session_row = _session_row()
    other_conversation = _conversation_row(uuid4())
    app = _app(
        _db_session({ChatSession: session_row, ChatConversation: other_conversation}),
        _fake_service(),
    )

    async with await _client(app) as client:
        resp = await client.patch(
            f"/api/v1/chat/conversations/{other_conversation.id}",
            json={"title": "stolen"},
            headers=_session_headers(session_row),
        )

    assert resp.status_code == 404


# -- messages & turns ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_message_runs_service_and_returns_turn_with_messages() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    turn = _turn_row(conversation.id)
    messages = [
        _message_row(conversation.id, turn.id, 1, "user"),
        _message_row(conversation.id, turn.id, 2, "assistant"),
    ]
    session = _db_session(
        {ChatSession: session_row, ChatConversation: conversation, ChatTurn: turn}
    )
    # execute order: stale-turn reclaim, fresh turn re-read, turn messages.
    fresh_turn = MagicMock()
    fresh_turn.scalar_one_or_none.return_value = turn
    session.execute = AsyncMock(side_effect=[MagicMock(), fresh_turn, _execute_result(messages)])
    service = _fake_service()
    app = _app(session, service)

    async with await _client(app) as client:
        resp = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"content": "Explain the known error register.", "request_id": REQUEST_ID},
            headers=_session_headers(session_row),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["route"] == "knowledge"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    request = service.handle_turn.call_args.args[0]
    assert request.user_message == "Explain the known error register."
    assert request.operator_subject == OPERATOR_SUBJECT


@pytest.mark.asyncio
async def test_duplicate_request_id_returns_existing_turn_without_rerun() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    existing_turn = _turn_row(conversation.id)
    session = _db_session(
        {ChatSession: session_row, ChatConversation: conversation, ChatTurn: existing_turn}
    )
    # commit #1 is the session last_seen touch; commit #2 is the turn claim.
    session.commit = AsyncMock(
        side_effect=[None, _integrity_error("uq_chat_turns_conversation_request")]
    )
    reclaim_update = MagicMock()  # stale-turn reclamation; no rows consumed
    claim_select = MagicMock()
    claim_select.scalar_one_or_none.return_value = existing_turn
    empty_messages = MagicMock()
    empty_messages.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(side_effect=[reclaim_update, claim_select, empty_messages])
    service = _fake_service()
    app = _app(session, service)

    async with await _client(app) as client:
        resp = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"content": "again", "request_id": REQUEST_ID},
            headers=_session_headers(session_row),
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["request_id"] == REQUEST_ID
    service.handle_turn.assert_not_called()


@pytest.mark.asyncio
async def test_second_concurrent_turn_conflicts_409() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    session = _db_session({ChatSession: session_row, ChatConversation: conversation})
    session.commit = AsyncMock(side_effect=[None, _integrity_error("uq_chat_turns_one_active")])
    app = _app(session, _fake_service())

    async with await _client(app) as client:
        resp = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"content": "hello", "request_id": REQUEST_ID},
            headers=_session_headers(session_row),
        )

    assert resp.status_code == 409
    assert "already running" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_turn_timeout_returns_persisted_status() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    running_turn = _turn_row(conversation.id, status="running")
    session = _db_session(
        {ChatSession: session_row, ChatConversation: conversation, ChatTurn: running_turn}
    )
    service = MagicMock(spec=ChatTurnService)
    service.handle_turn = MagicMock(side_effect=lambda _req: None)  # returns, but slowly enough
    # execute order: stale-turn reclaim, then the fresh turn re-read that the
    # timeout path reports instead of a fabricated outcome.
    fresh_turn = MagicMock()
    fresh_turn.scalar_one_or_none.return_value = running_turn
    empty_messages = MagicMock()
    empty_messages.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(side_effect=[MagicMock(), fresh_turn, empty_messages])
    settings = ChatSettings(
        _env_file=None,
        chat_enabled=True,
        chat_price_input_per_mtok=0.5,
        chat_price_output_per_mtok=2.0,
        chat_turn_timeout_seconds=0.05,
    )
    app = _app(session, service, settings)

    async with await _client(app) as client:
        resp = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"content": "hello", "request_id": REQUEST_ID},
            headers=_session_headers(session_row),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


@pytest.mark.asyncio
async def test_get_turn_belongs_to_conversation() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    turn = _turn_row(conversation.id)
    session = _db_session(
        {ChatSession: session_row, ChatConversation: conversation, ChatTurn: turn}
    )
    session.execute = AsyncMock(return_value=_execute_result([]))
    app = _app(session, _fake_service())

    async with await _client(app) as client:
        ok = await client.get(
            f"/api/v1/chat/conversations/{conversation.id}/turns/{turn.id}",
            headers=_session_headers(session_row),
        )
        missing = await client.get(
            f"/api/v1/chat/conversations/{uuid4()}/turns/{turn.id}",
            headers=_session_headers(session_row),
        )

    assert ok.status_code == 200
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_message_contract_limits_are_enforced() -> None:
    session_row = _session_row()
    conversation = _conversation_row(session_row.id)
    app = _app(
        _db_session({ChatSession: session_row, ChatConversation: conversation}), _fake_service()
    )

    async with await _client(app) as client:
        too_long = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"content": "x" * 6001, "request_id": REQUEST_ID},
            headers=_session_headers(session_row),
        )
        short_request_id = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"content": "hello", "request_id": "tiny"},
            headers=_session_headers(session_row),
        )

    assert too_long.status_code == 422
    assert short_request_id.status_code == 422
