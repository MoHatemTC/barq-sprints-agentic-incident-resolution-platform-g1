"""Unit tests for the Celery worker's synchronous database engine."""

from unittest.mock import patch

from app.workers.sync_engine import create_sync_engine


def test_sync_engine_bounds_initial_connection_attempt() -> None:
    """A dead PostgreSQL endpoint must not hang worker startup indefinitely."""
    database_url = "postgresql+psycopg://user:pass@db.example/barq"

    with patch("app.workers.sync_engine.create_engine") as create_engine:
        engine = create_sync_engine(database_url, connect_timeout_seconds=7)

    assert engine is create_engine.return_value
    create_engine.assert_called_once_with(
        database_url,
        echo=False,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 7},
    )
