"""Admin chatbot API: sessions, conversations, messages, turns (``/api/v1/chat``).

Auth is two-layered: a signed operator token (``operator`` role) plus a
server-issued chat-session secret whose hash is stored. Every conversation,
turn and message access verifies that the chat session owns the resource —
another session's ids answer 404, not 403, so ids cannot be probed.

Message submission is synchronous by design: the turn row is claimed first
(idempotent on the client's request id, one active turn per conversation at
the database level), then the graph runs on a worker thread and the checked
final answer is returned. The turn-status endpoint covers reconnects.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.chat import (
    ChatConversationResponse,
    ChatMessageResponse,
    ChatSessionCreatedResponse,
    ChatTurnResponse,
    CreateConversationRequest,
    RenameConversationRequest,
    SubmitMessageRequest,
)
from api.schemas.errors import ErrorResponse
from app.api.dependencies import get_db_session
from app.auth.auth import require_role, verify_bearer_token
from app.chat.config import ChatSettings
from app.chat.dependencies import (
    get_chat_service,
    require_chat_enabled,
    require_chat_session,
)
from app.chat.service import ChatTurnService, TurnRequest
from app.db.models import ChatConversation, ChatMessage, ChatSession, ChatTurn
from app.exceptions.app_errors import (
    ConflictError,
    ContractValidationError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.repositories.idempotency import _postgres_constraint_name

logger = structlog.getLogger("api.chat")

_IDEMPOTENCY_CONSTRAINT = "uq_chat_turns_conversation_request"
_ACTIVE_TURN_CONSTRAINT = "uq_chat_turns_one_active"

#: A turn left 'running' by a crash (e.g. the process died between claim and
#: completion) must not wedge the conversation forever: claims older than the
#: turn timeout plus this grace are reclaimed as failed. There is no chat
#: reaper; this runs inline at claim time.
_STALE_TURN_GRACE_SECONDS = 60.0

router = APIRouter(
    prefix="/api/v1/chat",
    tags=["Chat"],
    # Auth first (401), then role (403), then the feature gate (503) — a
    # disabled chat must still reject anonymous requests as anonymous, and a
    # role-less token must not use an older session either.
    dependencies=[
        Depends(verify_bearer_token),
        Depends(require_role("operator")),
        Depends(require_chat_enabled),
    ],
)


# -- sessions -------------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=ChatSessionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Establish an authenticated chat session",
    description=(
        "Exchanges a valid operator token for a chat session. The returned "
        "secret is shown exactly once; every later chat call must present it "
        "in X-Chat-Session-Secret alongside the operator token."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid operator token."},
        403: {"model": ErrorResponse, "description": "Token lacks the operator role."},
    },
)
async def create_chat_session(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    claims: Annotated[dict, Depends(verify_bearer_token)],
    _operator: Annotated[str, Depends(require_role("operator"))],
) -> ChatSessionCreatedResponse:
    """Issue a browser chat session bound to the token's operator subject.

    Conversations stay owned by the session that created them: the shared
    operator subject is never treated as proof of ownership, so logging in
    from another browser neither gains nor steals another session's history.
    """
    secret = secrets.token_urlsafe(32)
    session_row = ChatSession(
        id=uuid4(),
        operator_subject=str(claims.get("sub") or ""),
        secret_hash=hashlib.sha256(secret.encode("utf-8")).hexdigest(),
    )
    try:
        db.add(session_row)
        await db.commit()
    except SQLAlchemyError as exc:
        logger.exception("chat_session_create_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to create the chat session.") from exc
    return ChatSessionCreatedResponse(session_id=session_row.id, chat_secret=secret)


# -- conversations ----------------------------------------------------------------


@router.post(
    "/conversations",
    response_model=ChatConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation",
    responses={
        401: {"model": ErrorResponse, "description": "Missing chat session credentials."},
    },
)
async def create_conversation(
    payload: CreateConversationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: Annotated[ChatSession, Depends(require_chat_session)],
) -> ChatConversationResponse:
    conversation = ChatConversation(
        id=uuid4(),
        session_id=session.id,
        operator_subject=session.operator_subject,
        title=(payload.title or "").strip() or "New conversation",
    )
    try:
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
    except SQLAlchemyError as exc:
        logger.exception("chat_conversation_create_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to create the conversation.") from exc
    return ChatConversationResponse.model_validate(conversation)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ChatConversationResponse,
    status_code=status.HTTP_200_OK,
    summary="Rename a conversation",
    responses={
        401: {"model": ErrorResponse, "description": "Missing chat session credentials."},
        404: {"model": ErrorResponse, "description": "No such conversation for this session."},
    },
)
async def rename_conversation(
    conversation_id: UUID,
    payload: RenameConversationRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: Annotated[ChatSession, Depends(require_chat_session)],
) -> ChatConversationResponse:
    conversation = await _owned_conversation(db, conversation_id, session)
    title = payload.title.strip()
    if not title:
        raise ContractValidationError("Conversation title must not be empty.")
    conversation.title = title
    try:
        await db.commit()
        await db.refresh(conversation)
    except SQLAlchemyError as exc:
        logger.exception("chat_conversation_rename_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to rename the conversation.") from exc
    return ChatConversationResponse.model_validate(conversation)


@router.get(
    "/conversations",
    response_model=list[ChatConversationResponse],
    status_code=status.HTTP_200_OK,
    summary="List this session's conversations",
)
async def list_conversations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: Annotated[ChatSession, Depends(require_chat_session)],
) -> list[ChatConversationResponse]:
    try:
        rows = (
            (
                await db.execute(
                    select(ChatConversation)
                    .where(ChatConversation.session_id == session.id)
                    .order_by(ChatConversation.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        logger.exception("chat_conversations_query_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to list conversations.") from exc
    return [ChatConversationResponse.model_validate(row) for row in rows]


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and its history",
    responses={
        404: {"model": ErrorResponse, "description": "No such conversation for this session."},
    },
)
async def delete_conversation(
    conversation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: Annotated[ChatSession, Depends(require_chat_session)],
) -> None:
    conversation = await _owned_conversation(db, conversation_id, session)
    running = (
        await db.execute(
            select(ChatTurn.id)
            .where(ChatTurn.conversation_id == conversation.id)
            .where(ChatTurn.status == "running")
            .limit(1)
        )
    ).scalar_one_or_none()
    if running is not None:
        # Deleting under a live worker would strand its writes; refusal is the
        # bounded policy for this release (no cancellation machinery).
        raise ConflictError(
            "This conversation has an answer in progress; try deleting it again shortly."
        )
    try:
        await db.delete(conversation)
        await db.commit()
    except SQLAlchemyError as exc:
        logger.exception("chat_conversation_delete_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to delete the conversation.") from exc


# -- messages & turns ---------------------------------------------------------------


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ChatMessageResponse],
    status_code=status.HTTP_200_OK,
    summary="Retrieve paginated sanitized history",
)
async def list_messages(
    conversation_id: UUID,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: Annotated[ChatSession, Depends(require_chat_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    latest: Annotated[
        bool,
        Query(description="Fetch the NEWEST page instead of offset-from-first."),
    ] = False,
) -> list[ChatMessageResponse]:
    await _owned_conversation(db, conversation_id, session)
    try:
        total = (
            await db.execute(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
            )
        ).scalar_one()
        response.headers["X-Total-Count"] = str(total)
        query = (
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.seq)
        )
        if latest:
            # History is stored ascending, so the newest page is the tail:
            # without this, conversations beyond one page silently hide the
            # newest messages.
            query = query.offset(max(0, total - limit)).limit(limit)
        else:
            query = query.offset(offset).limit(limit)
        rows = (await db.execute(query)).scalars().all()
    except SQLAlchemyError as exc:
        logger.exception("chat_messages_query_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to read messages.") from exc
    return [ChatMessageResponse.model_validate(row) for row in rows]


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatTurnResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit a message and wait for the checked answer",
    description=(
        "Claims the turn atomically (idempotent on request_id; one active turn "
        "per conversation), runs the chat graph on a worker thread and returns "
        "the final verified answer. Resubmitting the same request_id returns "
        "the existing turn instead of running the model again."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Missing chat session credentials."},
        404: {"model": ErrorResponse, "description": "No such conversation for this session."},
        409: {"model": ErrorResponse, "description": "A turn is already running."},
        503: {"model": ErrorResponse, "description": "Chat disabled or database unavailable."},
    },
)
async def submit_message(
    conversation_id: UUID,
    payload: SubmitMessageRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: Annotated[ChatSession, Depends(require_chat_session)],
    service: Annotated[ChatTurnService, Depends(get_chat_service)],
    chat_settings: Annotated[ChatSettings, Depends(require_chat_enabled)],
) -> ChatTurnResponse:
    conversation = await _owned_conversation(db, conversation_id, session)

    turn, claimed = await _claim_turn(
        db,
        conversation,
        payload.request_id,
        stale_grace_seconds=chat_settings.chat_turn_timeout_seconds + _STALE_TURN_GRACE_SECONDS,
    )
    if not claimed:
        # Idempotent resubmission: return the existing turn untouched, whether
        # it already finished or is still running on the first request.
        return await _turn_response(db, turn)

    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                service.handle_turn,
                TurnRequest(
                    conversation_id=conversation.id,
                    turn_id=turn.id,
                    operator_subject=session.operator_subject,
                    user_message=payload.content,
                ),
            ),
            timeout=chat_settings.chat_turn_timeout_seconds,
        )
    except TimeoutError:
        logger.warning("chat_turn_timeout", turn_id=str(turn.id))
        # The worker thread keeps running and persists its own terminal state;
        # report the persisted status instead of a fabricated outcome.

    refreshed = await _fresh_turn(db, turn.id)
    if refreshed is None:  # pragma: no cover - the row was just created
        raise ResourceNotFoundError(f"Turn '{turn.id}' not found")
    return await _turn_response(db, refreshed)


@router.get(
    "/conversations/{conversation_id}/turns/{turn_id}",
    response_model=ChatTurnResponse,
    status_code=status.HTTP_200_OK,
    summary="Recover persisted turn status",
)
async def get_turn(
    conversation_id: UUID,
    turn_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    session: Annotated[ChatSession, Depends(require_chat_session)],
) -> ChatTurnResponse:
    await _owned_conversation(db, conversation_id, session)
    turn = await db.get(ChatTurn, turn_id)
    if turn is None or turn.conversation_id != conversation_id:
        raise ResourceNotFoundError(f"Turn '{turn_id}' not found")
    return await _turn_response(db, turn)


# -- helpers --------------------------------------------------------------------


async def _owned_conversation(
    db: AsyncSession, conversation_id: UUID, session: ChatSession
) -> ChatConversation:
    """Fetch the conversation or answer 404 (never reveal other sessions')."""
    try:
        conversation = await db.get(ChatConversation, conversation_id)
    except SQLAlchemyError as exc:
        logger.exception("chat_conversation_query_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to read the conversation.") from exc
    if conversation is None or conversation.session_id != session.id:
        raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found")
    return conversation


async def _claim_turn(
    db: AsyncSession,
    conversation: ChatConversation,
    request_id: str,
    *,
    stale_grace_seconds: float,
) -> tuple[ChatTurn, bool]:
    """Insert the running turn; the database arbitrates duplicates and focus.

    Returns the turn and whether THIS request owns executing it. Losing the
    (conversation, request_id) race means an identical submission exists — the
    caller returns that row instead of running the model twice. Losing the
    one-active-turn race is a 409. A running turn stale beyond
    ``stale_grace_seconds`` (crashed request) is reclaimed as failed first.
    """
    stale_before = datetime.now(UTC) - timedelta(seconds=stale_grace_seconds)
    await db.execute(
        update(ChatTurn)
        .where(ChatTurn.conversation_id == conversation.id)
        .where(ChatTurn.status == "running")
        .where(ChatTurn.created_at < stale_before)
        .values(status="failed", error_category="stale_reclaimed", completed_at=func.now())
    )
    turn = ChatTurn(
        id=uuid4(),
        conversation_id=conversation.id,
        request_id=request_id,
    )
    try:
        db.add(turn)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Which unique index fires first is not deterministic when a duplicate
        # request_id races an active turn: look up the idempotent case first,
        # then treat any remaining uniqueness conflict as "one active turn".
        existing = (
            await db.execute(
                select(ChatTurn)
                .where(ChatTurn.conversation_id == conversation.id)
                .where(ChatTurn.request_id == request_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        constraint = _postgres_constraint_name(exc)
        if constraint in (_IDEMPOTENCY_CONSTRAINT, _ACTIVE_TURN_CONSTRAINT) or constraint is None:
            raise ConflictError(
                "A turn is already running for this conversation; wait for it to finish."
            ) from exc
        logger.exception("chat_turn_claim_failed", constraint=constraint, error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to claim the turn.") from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("chat_turn_claim_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to claim the turn.") from exc
    return turn, True


async def _fresh_turn(db: AsyncSession, turn_id: UUID) -> ChatTurn | None:
    """Re-read the turn row, bypassing the identity map.

    The request session inserted this row (status 'running') and the session
    keeps objects usable across commits (expire_on_commit=False), so ``db.get``
    would hand back that stale in-memory snapshot instead of the terminal
    status the worker thread just persisted.
    """
    return (
        await db.execute(
            select(ChatTurn).where(ChatTurn.id == turn_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _turn_response(db: AsyncSession, turn: ChatTurn) -> ChatTurnResponse:
    try:
        rows = (
            (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.turn_id == turn.id)
                    .order_by(ChatMessage.seq)
                )
            )
            .scalars()
            .all()
        )
    except SQLAlchemyError as exc:
        logger.exception("chat_turn_messages_query_failed", error=type(exc).__name__)
        raise ServiceUnavailableError("Database unavailable to read the turn.") from exc
    return ChatTurnResponse(
        id=turn.id,
        conversation_id=turn.conversation_id,
        request_id=turn.request_id,
        route=turn.route,
        status=turn.status,
        error_category=turn.error_category,
        usage=turn.usage,
        created_at=turn.created_at,
        completed_at=turn.completed_at,
        messages=[ChatMessageResponse.model_validate(row) for row in rows],
    )


__all__ = ["router"]
