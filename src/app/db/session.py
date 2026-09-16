from __future__ import annotations

import structlog
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

logger = structlog.getLogger(__name__)


def build_postgres_url(settings: Settings) -> URL:
    """Construct a safe SQLAlchemy async URL for PostgreSQL."""
    password = (
        settings.postgres_password.get_secret_value()
        if settings.postgres_password
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


def create_db_engine(settings: Settings) -> AsyncEngine:
    """Create a production-configured SQLAlchemy async engine."""
    url = build_postgres_url(settings)
    return create_async_engine(
        url,
        pool_size=10,
        max_overflow=20,
        pool_timeout=5.0,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async sessionmaker bound to the given engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
