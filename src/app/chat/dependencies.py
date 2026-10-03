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
from app.chat.cache import RedisAnswerCache, digest
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


def get_chat_service(request: Request) -> ChatTurnService:
    """Assemble the turn service from THIS app's effective settings.

    ``app.state.chat_service`` is built once per app lifespan from
    ``app.state.chat_settings`` (which tests may override); the expensive
    clients underneath stay process-wide singletons. Settings instances are
    unhashable, so nothing may be ``lru_cache``d *through* a settings
    argument.
    """
    service: ChatTurnService | None = getattr(request.app.state, "chat_service", None)
    if service is not None:
        return service
    service = build_chat_service(get_chat_settings(request))
    request.app.state.chat_service = service
    return service


def build_chat_service(settings: ChatSettings) -> ChatTurnService:
    """Wire a turn service for one app from its effective chat settings."""
    from agent.config import get_agent_settings
    from app.chat.prompts import CHAT_ANSWER_SYSTEM, CHAT_ROUTE_SYSTEM
    from app.core.config import get_retrieval_settings
    from observability.tracing import get_tracer

    budget = RedisChatBudget(
        _sync_redis_singleton(), daily_limit_usd=settings.chat_daily_budget_usd
    )
    retriever = build_chat_retriever(settings)
    cache = None
    scope = ""
    if settings.chat_cache_enabled:
        retrieval_settings = get_retrieval_settings()
        agent_settings = get_agent_settings()
        cache = RedisAnswerCache(
            _sync_redis_singleton(),
            embed=retriever.embed_query,
            validate_sources=retriever.validate_sources,
            collection=retrieval_settings.qdrant_collection_name,
            threshold=settings.chat_cache_similarity_threshold,
            ttl_seconds=settings.chat_cache_ttl_seconds,
            max_entries=settings.chat_cache_max_entries,
        )
        scope = digest(
            [
                settings.chat_max_security_level,
                settings.chat_model or agent_settings.agent_llm_model,
                settings.chat_cache_prompt_version,
                CHAT_ANSWER_SYSTEM,
                CHAT_ROUTE_SYSTEM,
                agent_settings.agent_prompt_version,
                retrieval_settings.qdrant_url,
                retrieval_settings.dense_embedding_model,
                retrieval_settings.sparse_embedding_model,
                retrieval_settings.retrieval_mode.value,
                settings.chat_evidence_chunk_limit,
                settings.chat_max_evidence_chars,
            ]
        )
    return ChatTurnService(
        llm=get_llm(),
        retriever=retriever,
        store=_chat_store_singleton(),
        budget=budget,
        settings=settings,
        tracer=get_tracer(),
        cache=cache,
        cache_scope=scope,
    )


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


__all__ = [
    "CHAT_SESSION_ID_HEADER",
    "CHAT_SESSION_SECRET_HEADER",
    "build_chat_service",
    "get_chat_service",
    "get_chat_settings",
    "require_chat_enabled",
    "require_chat_session",
]
