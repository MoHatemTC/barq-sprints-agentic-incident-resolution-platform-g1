"""Synchronous PostgreSQL engine for Celery workers.

The API layer (FastAPI) is async and uses the async engine in
:mod:`app.db.session`. Celery workers are synchronous processes, so they need
a sync engine — the standard Celery+SQLAlchemy pattern. This module mirrors
Ahmed's async factory with the ``psycopg`` driver and is proposed for adoption
into ``app/db/session.py``; until then it lives here, isolated behind the
``WorkerRepo`` protocol, so any future consolidation touches one file.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker


class SyncPostgreSQLSettings(Protocol):
    """Settings surface needed to build the sync PostgreSQL URL.

    Mirrors ``app.db.session.PostgreSQLSettings`` so the same Settings object
    (or mock) can drive both engines.
    """

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr | None


SyncSessionFactory = sessionmaker[Session]


def build_sync_database_url(settings: SyncPostgreSQLSettings) -> URL:
    """Sync (psycopg) URL while keeping credentials escaped and undisclosed."""
    password = (
        settings.postgres_password.get_secret_value()
        if settings.postgres_password is not None
        else None
    )
    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.postgres_user,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def create_sync_engine(
    database_url: str | URL,
    *,
    echo: bool = False,
    connect_timeout_seconds: int = 5,
) -> Engine:
    """Lazy sync engine with a bounded initial PostgreSQL connection attempt."""
    return create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"connect_timeout": connect_timeout_seconds},
    )


def create_sync_session_factory(engine: Engine) -> SyncSessionFactory:
    """Sessions whose objects remain usable after transaction commits."""
    return sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )


@contextmanager
def sync_session_scope(factory: SyncSessionFactory) -> Iterator[Session]:
    """Yield one session and roll back failed work; callers own commits.

    Workers open a FRESH session per logical operation — the session that
    witnessed a task's failure is dead (aborted transaction) and must never be
    reused for the failure record.
    """
    with factory() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


__all__ = [
    "SyncPostgreSQLSettings",
    "SyncSessionFactory",
    "build_sync_database_url",
    "create_sync_engine",
    "create_sync_session_factory",
    "sync_session_scope",
]
