"""Alembic environment for the canonical asynchronous PostgreSQL models."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

load_dotenv()

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db import models as canonical_models  # noqa: F401
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> URL:
    """Build a migration-only URL without loading application-wide settings."""
    override = os.getenv("BARQ_DATABASE_URL") or os.getenv("DATABASE_URL")
    if override:
        url = make_url(override)
    else:
        password = os.getenv("POSTGRES_PASSWORD")
        if password is None:
            raise RuntimeError(
                "Set BARQ_DATABASE_URL (preferred), DATABASE_URL, or POSTGRES_PASSWORD "
                "before running Alembic."
            )
        url = URL.create(
            "postgresql+asyncpg",
            username=os.getenv("POSTGRES_USER", "postgres"),
            password=password,
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "barq_incident_dev"),
        )

    if url.drivername == "postgresql":
        return url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        raise RuntimeError("Alembic requires a postgresql+asyncpg database URL.")
    return url


def run_migrations_offline() -> None:
    """Render SQL without opening a database connection."""
    context.configure(
        url=_database_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations using the project's asyncpg SQLAlchemy driver."""
    connectable = create_async_engine(_database_url(), poolclass=NullPool)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
