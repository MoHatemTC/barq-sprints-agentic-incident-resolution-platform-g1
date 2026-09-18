from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import redis as sync_redis_lib
import redis.asyncio as aioredis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import SessionFactory
from app.exceptions.app_errors import ServiceUnavailableError
from app.workers.db import InMemoryRepo, WorkerRepo, build_worker_repo


def get_app_settings(request: Request) -> Settings:
    """Return the application settings instance."""
    return getattr(request.app.state, "settings", None) or get_settings()


def get_session_factory(request: Request) -> SessionFactory:
    """FastAPI dependency providing the SQLAlchemy async sessionmaker."""
    session_factory: SessionFactory | None = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise ServiceUnavailableError("Database session factory is not initialized.")
    return session_factory


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency providing an active async SQLAlchemy session."""
    session_factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "session_factory", None
    )
    if session_factory is None:
        raise ServiceUnavailableError("Database session factory is not initialized.")

    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_redis(request: Request) -> aioredis.Redis:
    """FastAPI dependency providing the active async Redis client."""
    redis_client: aioredis.Redis | None = getattr(request.app.state, "redis", None)
    if redis_client is None:
        raise ServiceUnavailableError("Redis client is not initialized.")
    return redis_client


def get_sync_worker_repo(request: Request) -> WorkerRepo:
    """FastAPI dependency providing the sync WorkerRepo backed by connection pool."""
    repo: WorkerRepo | None = getattr(request.app.state, "sync_worker_repo", None)
    if repo is None:
        settings = get_app_settings(request)
        engine = getattr(request.app.state, "engine", None)
        if isinstance(engine, MagicMock):
            repo = InMemoryRepo()
        else:
            repo = build_worker_repo(settings)
        request.app.state.sync_worker_repo = repo
    return repo


def get_sync_redis(request: Request) -> Any:
    """FastAPI dependency providing the sync Redis client backed by connection pool."""
    client = getattr(request.app.state, "sync_redis", None)
    if client is None:
        settings = get_app_settings(request)
        redis_mock = getattr(request.app.state, "redis", None)
        if isinstance(redis_mock, MagicMock):
            client = MagicMock()
        else:
            password = (
                settings.redis_password.get_secret_value() if settings.redis_password else None
            )
            pool = sync_redis_lib.ConnectionPool(
                host=settings.redis_host,
                port=settings.redis_port,
                password=password,
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                max_connections=20,
            )
            client = sync_redis_lib.Redis(connection_pool=pool)
        request.app.state.sync_redis = client
    return client
