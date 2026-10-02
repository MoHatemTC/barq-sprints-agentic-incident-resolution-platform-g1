"""Production wiring for the admin chatbot: settings, session auth, service.

Everything model-bound, retrieval-bound and storage-bound hangs off small
seams so the ordinary test suite can substitute fakes without network or
credentials (the same discipline as ``agent.dependencies``).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

import redis as redis_sync
import structlog
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent.llm import get_llm
from app.api.dependencies import get_app_settings, get_db_session
from app.auth.auth import verify_bearer_token
from app.chat.budget import RedisChatBudget
from app.chat.config import ChatSettings
from app.chat.retrieval import build_chat_retriever
from app.chat.service import ChatTurnService
from app.chat.store import SQLAlchemyChatStore
from app.core.config import Settings
from app.db.models import ChatSession
from app.exceptions.app_errors import AuthenticationError, ServiceUnavailableError
from app.workers.sync_engine import (
    build_sync_database_url,
    create_sync_engine,
    create_sync_session_factory,
)

logger = structlog.get_logger(__name__)

CHAT_SESSION_ID_HEADER = "X-Chat-Session-Id"
CHAT_SESSION_SECRET_HEADER = "X-Chat-Session-Secret"


@lru_cache
def _cached_chat_settings() -> ChatSettings:
    return ChatSettings()


def get_chat_settings(request: Request) -> ChatSettings:
    """Chat settings; ``app.state.chat_settings`` wins so tests can inject."""
    override: ChatSettings | None = getattr(request.app.state, "chat_settings", None)
    return override if override is not None else _cached_chat_settings()


def require_chat_enabled(
    settings: Annotated[ChatSettings, Depends(get_chat_settings)],
) -> ChatSettings:
    """The chatbot is feature-off by default; every route answers 503 until enabled."""
    if not settings.chat_enabled:
        raise ServiceUnavailableError("Chat is disabled (CHAT_ENABLED=false).")
    return settings


async def require_chat_session(
    claims: Annotated[dict[str, Any], Depends(verify_bearer_token)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    x_chat_session_id: Annotated[str | None, Header(alias=CHAT_SESSION_ID_HEADER)] = None,
    x_chat_session_secret: Annotated[str | None, Header(alias=CHAT_SESSION_SECRET_HEADER)] = None,
) -> ChatSession:
    """Bind the request to a server-issued chat session.

    After ``POST /chat/sessions`` every call needs the operator token AND the
    session secret; the stored hash is compared in constant time and the
    session's operator subject must match the token subject. Unknown session,
    wrong secret and wrong operator all read as 401 — nothing is revealed.
    """
    if not x_chat_session_id or not x_chat_session_secret:
        raise AuthenticationError("Missing chat session credentials")
    try:
        session_id = UUID(x_chat_session_id)
    except ValueError as exc:
        raise AuthenticationError("Missing chat session credentials") from exc

    session = await db.get(ChatSession, session_id)
    if session is None:
        raise AuthenticationError("Missing chat session credentials")
    supplied_hash = hashlib.sha256(x_chat_session_secret.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_hash, session.secret_hash):
        raise AuthenticationError("Missing chat session credentials")
    if session.operator_subject != str(claims.get("sub") or ""):
        raise AuthenticationError("Missing chat session credentials")

    session.last_seen_at = datetime.now(UTC)
    await db.commit()
    return session


def get_chat_store(settings: Annotated[Settings, Depends(get_app_settings)]) -> SQLAlchemyChatStore:
    """Sync chat store over its own small engine (checkpointer precedent)."""
    return _chat_store_singleton()


def get_chat_budget(
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    chat_settings: Annotated[ChatSettings, Depends(get_chat_settings)],
) -> RedisChatBudget:
    # A thin per-request wrapper; the Redis client underneath is a singleton.
    return RedisChatBudget(
        _sync_redis_singleton(),
        daily_limit_usd=chat_settings.chat_daily_budget_usd,
    )


def get_chat_service(
    request: Request,
    settings: Annotated[ChatSettings, Depends(require_chat_enabled)],
) -> ChatTurnService:
    """Assemble the turn service from process-wide singletons.

    Note: pydantic settings instances are unhashable, so the singletons below
    must never be ``lru_cache``d *through* a settings argument — they build
    from the process-wide cached settings instead.
    """
    return _service_singleton()


# -- singletons -----------------------------------------------------------------


@lru_cache
def _chat_store_singleton() -> SQLAlchemyChatStore:
    from app.core.config import get_settings

    engine = create_sync_engine(build_sync_database_url(get_settings()))
    return SQLAlchemyChatStore(create_sync_session_factory(engine))


@lru_cache
def _sync_redis_singleton() -> redis_sync.Redis:
    from app.core.config import get_settings

    settings = get_settings()
    password = settings.redis_password.get_secret_value() if settings.redis_password else None
    return redis_sync.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        socket_timeout=settings.redis_socket_timeout,
    )


@lru_cache
def _service_singleton() -> ChatTurnService:
    from observability.tracing import get_tracer

    settings = _cached_chat_settings()
    budget = RedisChatBudget(
        _sync_redis_singleton(), daily_limit_usd=settings.chat_daily_budget_usd
    )
    return ChatTurnService(
        llm=get_llm(),
        retriever=build_chat_retriever(settings),
        store=_chat_store_singleton(),
        budget=budget,
        settings=settings,
        tracer=get_tracer(),
    )


__all__ = [
    "CHAT_SESSION_ID_HEADER",
    "CHAT_SESSION_SECRET_HEADER",
    "get_chat_service",
    "get_chat_settings",
    "require_chat_enabled",
    "require_chat_session",
]
