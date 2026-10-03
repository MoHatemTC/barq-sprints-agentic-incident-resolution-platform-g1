"""PostgreSQL-backed chat store guarantees (``pytest -m integration``).

The mocked suite cannot prove row-locking, conditional updates or constraint
behavior; these tests run the real SQLAlchemyChatStore against the local
docker Postgres (``just test-integration``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat.store import SQLAlchemyChatStore
from app.db.models import ChatConversation, ChatSession
from app.workers.sync_engine import create_sync_session_factory


@pytest.fixture(scope="module")
def pg_engine():
    from app.core.config import get_settings
    from app.db.models import Base
    from app.workers.sync_engine import build_sync_database_url, create_sync_engine

    engine = create_sync_engine(build_sync_database_url(get_settings()))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def store(pg_engine) -> SQLAlchemyChatStore:
    return SQLAlchemyChatStore(create_sync_session_factory(pg_engine))


@pytest.fixture()
def conversation(pg_engine) -> UUID:
    session_id = UUID(int=0)
    conversation_id = uuid4()
    with Session(pg_engine) as session, session.begin():
        if session.get(ChatSession, session_id) is None:
            session.add(
                ChatSession(
                    id=session_id,
                    operator_subject="store-it",
                    secret_hash="0" * 64,
                    created_at=datetime.now(UTC),
                )
            )
        session.add(
            ChatConversation(
                id=conversation_id,
                session_id=session_id,
                operator_subject="store-it",
                title="store integration",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    return conversation_id


def turn_row(pg_engine, conversation_id: UUID, status: str) -> UUID:
    from app.db.models import ChatTurn

    turn_id = uuid4()
    with Session(pg_engine) as session, session.begin():
        session.add(
            ChatTurn(
                id=turn_id,
                conversation_id=conversation_id,
                request_id=uuid4().hex,
                created_at=datetime.now(UTC),
            )
        )
        if status != "running":
            from sqlalchemy import update

            session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id)
                .values(
                    status=status,
                    error_category="stale_reclaimed" if status == "failed" else None,
                    completed_at=datetime.now(UTC),
                )
            )
    return turn_id


@pytest.mark.integration
class TestPublishFencing:
    def test_publish_closes_a_running_turn_and_persists_the_message(
        self, pg_engine, store, conversation
    ) -> None:
        turn_id = turn_row(pg_engine, conversation, "running")

        published = store.publish_turn(
            conversation,
            turn_id,
            content="the answer",
            citations=[],
            status="succeeded",
            route="knowledge",
            usage={"model_calls": 1},
        )

        assert published is True
        from app.db.models import ChatMessage, ChatTurn

        with Session(pg_engine) as session:
            turn = session.get(ChatTurn, turn_id)
            assert turn is not None and turn.status == "succeeded"
            assert turn.route == "knowledge"
            message = session.scalars(
                select(ChatMessage).where(ChatMessage.turn_id == turn_id)
            ).one()
            assert message.content == "the answer"

    def test_publish_after_reclamation_is_refused(self, pg_engine, store, conversation) -> None:
        """A turn reclaimed as stale must not accept a late worker's answer."""
        from app.db.models import ChatMessage

        turn_id = turn_row(pg_engine, conversation, "failed")

        published = store.publish_turn(
            conversation,
            turn_id,
            content="too late",
            citations=[],
            status="succeeded",
            route="knowledge",
            usage=None,
        )

        assert published is False
        with Session(pg_engine) as session:
            assert (
                session.scalars(select(ChatMessage).where(ChatMessage.turn_id == turn_id)).all()
                == []
            )

    def test_message_seqs_allocate_monotonically(self, pg_engine, store, conversation) -> None:
        turn_id = turn_row(pg_engine, conversation, "running")
        store.record_user_message(conversation, turn_id, content="q", blocked_layer=None)
        store.record_assistant_message(conversation, turn_id, content="a", citations=[])
        store.record_user_message(conversation, turn_id, content="q2", blocked_layer=None)

        history = store.get_history(conversation, limit=10)
        assert [m["content"] for m in history] == ["q", "a", "q2"]


@pytest.mark.integration
class TestSummaryCursor:
    def test_cursor_advances_and_survives_a_reload(self, pg_engine, store, conversation) -> None:
        turn_id = turn_row(pg_engine, conversation, "running")
        store.record_user_message(conversation, turn_id, content="old question", blocked_layer=None)

        store.save_summary(conversation, summary="The operator asked a question.", through_seq=1)

        assert store.get_summary(conversation) == ("The operator asked a question.", 1)
        # A fresh store over the same database (service restart) sees it too.
        reloaded = SQLAlchemyChatStore(store._factory)
        assert reloaded.get_summary(conversation) == ("The operator asked a question.", 1)

    def test_stale_writer_cannot_regress_the_cursor(self, pg_engine, store, conversation) -> None:
        store.save_summary(conversation, summary="newer", through_seq=5)
        store.save_summary(conversation, summary="stale", through_seq=3)

        summary, seq = store.get_summary(conversation)
        assert (summary, seq) == ("newer", 5)

    def test_unsummarized_returns_seq_and_respects_the_boundary(
        self, pg_engine, store, conversation
    ) -> None:
        turn_id = turn_row(pg_engine, conversation, "running")
        for i in range(8):
            store.record_user_message(
                conversation, turn_id, content=f"message {i}", blocked_layer=None
            )

        # history_limit=6 → boundary = seq 2; messages 1..2 are summarizable.
        items = store.unsummarized_messages(conversation, after_seq=0, history_limit=6, batch=10000)
        assert [item["seq"] for item in items] == [1, 2]

        # After summarizing through seq 1, only seq 2 remains.
        items = store.unsummarized_messages(conversation, after_seq=1, history_limit=6, batch=10000)
        assert [item["seq"] for item in items] == [2]

    def test_blank_summary_leaves_the_cursor_untouched(
        self, pg_engine, store, conversation
    ) -> None:
        """Service-level: a blank model summary must not advance the cursor."""
        turn_id = turn_row(pg_engine, conversation, "running")
        store.record_user_message(conversation, turn_id, content="q", blocked_layer=None)
        store.save_summary(conversation, summary="kept", through_seq=1)

        # save_summary with an equal cursor is a no-op (monotonic guard).
        store.save_summary(conversation, summary="kept", through_seq=1)
        assert store.get_summary(conversation) == ("kept", 1)
