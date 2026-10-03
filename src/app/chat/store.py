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

#: Upper bound on messages folded into one summarization call.
_BATCH_MESSAGE_COUNT = 20


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

    def attach_usage(self, turn_id: UUID, *, usage: dict[str, object] | None) -> None: ...

    def get_summary(self, conversation_id: UUID) -> tuple[str, int]: ...

    def save_summary(self, conversation_id: UUID, *, summary: str, through_seq: int) -> None: ...

    def unsummarized_messages(
        self, conversation_id: UUID, *, after_seq: int, history_limit: int, batch: int
    ) -> list[dict[str, Any]]: ...

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
            _lock_conversation(session, conversation_id)
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
            _lock_conversation(session, conversation_id)
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

    def attach_usage(self, turn_id: UUID, *, usage: dict[str, object] | None) -> None:
        """Attach reconciled usage to an already-published turn — usage only.

        Route, status and timestamps are set exclusively by publish_turn (the
        only writer that can satisfy the route/status CHECK constraints for
        every terminal path); this write must never alter the outcome.
        """
        with sync_session_scope(self._factory) as session:
            session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id, ChatTurn.status.in_(("succeeded", "blocked")))
                .values(usage=usage)
            )
            session.commit()

    def get_summary(self, conversation_id: UUID) -> tuple[str, int]:
        with sync_session_scope(self._factory) as session:
            conversation = session.get(ChatConversation, conversation_id)
            if conversation is None:
                return "", 0
            return conversation.history_summary or "", int(conversation.summary_seq or 0)

    def save_summary(self, conversation_id: UUID, *, summary: str, through_seq: int) -> None:
        """Monotonic cursor advance: a stale writer can never regress memory."""
        with sync_session_scope(self._factory) as session:
            session.execute(
                update(ChatConversation)
                .where(ChatConversation.id == conversation_id)
                .where(ChatConversation.summary_seq < through_seq)
                .values(history_summary=summary, summary_seq=through_seq, updated_at=func.now())
            )
            session.commit()

    def unsummarized_messages(
        self, conversation_id: UUID, *, after_seq: int, history_limit: int, batch: int
    ) -> list[dict[str, Any]]:
        """Sanitized messages older than the recent window and not yet summarized.

        Returns one item per message with its real ``seq`` so the summary
        cursor advances only over messages actually included, bounded to one
        ``batch`` of characters per summarization call.
        """
        with sync_session_scope(self._factory) as session:
            newest = session.scalar(
                select(func.max(ChatMessage.seq)).where(
                    ChatMessage.conversation_id == conversation_id
                )
            )
            boundary = (newest or 0) - history_limit
            if boundary <= after_seq:
                return []
            rows = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .where(ChatMessage.seq > after_seq)
                .where(ChatMessage.seq <= boundary)
                .order_by(ChatMessage.seq)
                .limit(_BATCH_MESSAGE_COUNT)
            ).all()
            items: list[dict[str, Any]] = [
                {"seq": row.seq, "role": row.role, "content": row.content} for row in rows
            ]
            # Keep whole messages within the batch char budget.
            bounded: list[dict[str, Any]] = []
            used = 0
            for item in items:
                size = len(str(item["content"]))
                if bounded and used + size > batch:
                    break
                bounded.append(item)
                used += size
            return bounded

    def fail_turn(self, turn_id: UUID, *, error_category: str) -> None:
        with sync_session_scope(self._factory) as session:
            session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id, ChatTurn.status == "running")
                .values(status="failed", error_category=error_category, completed_at=func.now())
            )
            session.commit()


def _lock_conversation(session: Any, conversation_id: UUID) -> None:
    """Serialize message-sequence allocation per conversation.

    ``max(seq)+1`` is only safe when writers queue behind a row lock; without
    it, a stale worker and the current worker can compute the same seq.
    """
    session.execute(
        select(ChatConversation.id).where(ChatConversation.id == conversation_id).with_for_update()
    )


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
