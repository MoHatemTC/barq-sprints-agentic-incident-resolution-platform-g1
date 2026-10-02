"""Persistence port for the chat graph, plus the SQLAlchemy implementation.

The graph runs synchronously on a worker thread (the model client and Qdrant
search are sync), so its store is sync too — the same pattern the incident
checkpointer uses with its own engine. Each conversation owns its rows; the
one-active-turn and (conversation, request_id) database constraints are the
real concurrency gates, not this code.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import structlog
from sqlalchemy import func, select, update

from app.db.models import ChatConversation, ChatMessage, ChatTurn
from app.workers.sync_engine import SyncSessionFactory, sync_session_scope

logger = structlog.get_logger(__name__)


class ChatStore(Protocol):
    """Sync persistence seam for the chat graph and turn service."""

    def get_history(self, conversation_id: UUID, *, limit: int) -> list[dict[str, str]]: ...

    def record_user_message(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        blocked_layer: str | None,
        autotitle: bool = True,
    ) -> None: ...

    def record_assistant_message(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        citations: list[dict[str, object]],
    ) -> None: ...

    def publish_turn(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        citations: list[dict[str, object]],
        status: str,
        route: str | None,
        usage: dict[str, object] | None,
    ) -> bool: ...

    def complete_turn(
        self,
        turn_id: UUID,
        *,
        status: str,
        route: str | None,
        usage: dict[str, object] | None,
    ) -> None: ...

    def attach_usage(
        self,
        turn_id: UUID,
        *,
        status: str,
        route: str | None,
        usage: dict[str, object] | None,
    ) -> None: ...

    def fail_turn(self, turn_id: UUID, *, error_category: str) -> None: ...


class SQLAlchemyChatStore:
    """ChatStore over the sync engine (psycopg), one short session per operation."""

    def __init__(self, session_factory: SyncSessionFactory) -> None:
        self._factory = session_factory

    def get_history(self, conversation_id: UUID, *, limit: int) -> list[dict[str, str]]:
        with sync_session_scope(self._factory) as session:
            rows = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.seq.desc())
                .limit(limit)
            ).all()
            return [{"role": row.role, "content": row.content} for row in reversed(list(rows))]

    def record_user_message(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        blocked_layer: str | None,
        autotitle: bool = True,
    ) -> None:
        self._insert_message(
            conversation_id,
            turn_id,
            role="user",
            content=content,
            autotitle=autotitle,
        )

    def record_assistant_message(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        citations: list[dict[str, object]],
    ) -> None:
        self._insert_message(
            conversation_id, turn_id, role="assistant", content=content, citations=citations
        )

    def _insert_message(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        role: str,
        content: str,
        citations: list[dict[str, object]] | None = None,
        autotitle: bool = False,
    ) -> None:
        with sync_session_scope(self._factory) as session:
            seq = _next_seq(session, conversation_id)
            session.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    seq=seq,
                    role=role,
                    content=content,
                    citations=citations or [],
                )
            )
            if autotitle and seq == 1 and role == "user":
                # Auto-title: the first user message (already sanitized) names
                # the conversation unless the operator chose an explicit title.
                _autotitle(session, conversation_id, content)
            session.commit()

    def publish_turn(
        self,
        conversation_id: UUID,
        turn_id: UUID,
        *,
        content: str,
        citations: list[dict[str, object]],
        status: str,
        route: str | None,
        usage: dict[str, object] | None,
    ) -> bool:
        """Close the turn and persist the assistant message in one transaction.

        The UPDATE is guarded on ``status = 'running'`` so a worker whose turn
        was already reclaimed as stale cannot publish an orphaned answer after
        a new turn started. Returns False when the turn was no longer running.
        """
        with sync_session_scope(self._factory) as session:
            result: Any = session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id, ChatTurn.status == "running")
                .values(status=status, route=route, usage=usage, completed_at=func.now())
            )
            if result.rowcount == 0:
                session.rollback()
                logger.warning("chat_turn_publish_skipped_not_running", turn_id=str(turn_id))
                return False
            seq = _next_seq(session, conversation_id)
            session.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    seq=seq,
                    role="assistant",
                    content=content,
                    citations=citations or [],
                )
            )
            session.commit()
            return True

    def complete_turn(
        self,
        turn_id: UUID,
        *,
        status: str,
        route: str | None,
        usage: dict[str, object] | None,
    ) -> None:
        with sync_session_scope(self._factory) as session:
            session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id)
                .values(status=status, route=route, usage=usage, completed_at=func.now())
            )
            session.commit()

    def attach_usage(
        self,
        turn_id: UUID,
        *,
        status: str,
        route: str | None,
        usage: dict[str, object] | None,
    ) -> None:
        """Attach final usage to an already-published turn (never resurrect one).

        Guarded to turns ``publish_turn`` already closed, so a reclaimed or
        failed turn keeps its terminal status.
        """
        with sync_session_scope(self._factory) as session:
            session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id, ChatTurn.status.in_(("succeeded", "blocked")))
                .values(status=status, route=route, usage=usage, completed_at=func.now())
            )
            session.commit()

    def fail_turn(self, turn_id: UUID, *, error_category: str) -> None:
        with sync_session_scope(self._factory) as session:
            session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id, ChatTurn.status == "running")
                .values(status="failed", error_category=error_category, completed_at=func.now())
            )
            session.commit()


def _next_seq(session: Any, conversation_id: UUID) -> int:
    current = session.scalar(
        select(func.max(ChatMessage.seq)).where(ChatMessage.conversation_id == conversation_id)
    )
    return (current or 0) + 1


#: Auto-titling only replaces the UI's placeholder, never an operator's title.
_PLACEHOLDER_TITLES = frozenset({"new conversation", "new chat"})
_AUTOTITLE_MAX_CHARS = 80


def _autotitle(session: Any, conversation_id: UUID, first_message: str) -> None:
    conversation = session.get(ChatConversation, conversation_id)
    if conversation is None or conversation.title.strip().lower() not in _PLACEHOLDER_TITLES:
        return
    stripped = first_message.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if first_line:
        conversation.title = (
            f"{first_line[:_AUTOTITLE_MAX_CHARS]}…"
            if len(first_line) > _AUTOTITLE_MAX_CHARS
            else first_line
        )


__all__ = ["ChatStore", "SQLAlchemyChatStore"]
