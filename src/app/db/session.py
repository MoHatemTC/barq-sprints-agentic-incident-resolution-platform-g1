"""Async SQLAlchemy engine and session construction without import-time I/O."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from pydantic import SecretStr
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class PostgreSQLSettings(Protocol):
    """Settings surface needed to construct the PostgreSQL URL."""

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr | None


SessionFactory = async_sessionmaker[AsyncSession]


def build_database_url(settings: PostgreSQLSettings) -> URL:
    """Build an asyncpg URL while leaving credentials escaped and undisclosed."""
    password = (
        settings.postgres_password.get_secret_value()
        if settings.postgres_password is not None
        else None
    )
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.postgres_user,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


# Compatibility alias for build_database_url
build_postgres_url = build_database_url


def create_database_engine(database_url: str | URL, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine configured for high concurrent throughput."""
    return create_async_engine(
        database_url,
        echo=echo,
        pool_size=50,
        max_overflow=25,
        pool_pre_ping=False,
    )


def create_db_engine(settings: PostgreSQLSettings) -> AsyncEngine:
    """Create a production-configured SQLAlchemy async engine from settings."""
    url = build_database_url(settings)
    return create_database_engine(url)


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create sessions whose objects remain usable after transaction commits."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def session_scope(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Yield one session and roll back failed work; callers own commit boundaries."""
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
