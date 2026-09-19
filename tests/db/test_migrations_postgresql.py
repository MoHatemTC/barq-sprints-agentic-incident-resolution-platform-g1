"""Alembic round-trip proof against an explicitly isolated PostgreSQL database."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db import models as canonical_models  # noqa: F401
from app.db.base import Base

APPLICATION_TABLES = {
    "events",
    "idempotency_keys",
    "executions",
    "workflow_state",
    "approvals",
    "failures",
    "retry_state",
}

TIMESTAMP_COLUMNS = {
    ("events", "received_at"),
    ("idempotency_keys", "created_at"),
    ("executions", "started_at"),
    ("executions", "ended_at"),
    ("executions", "updated_at"),
    ("workflow_state", "started_at"),
    ("workflow_state", "ended_at"),
    ("approvals", "decided_at"),
    ("failures", "occurred_at"),
    ("retry_state", "next_retry_at"),
    ("retry_state", "last_attempt_at"),
    ("retry_state", "created_at"),
    ("retry_state", "updated_at"),
}

JSONB_COLUMNS = {
    ("workflow_state", "evidence"),
    ("workflow_state", "decision"),
    ("workflow_state", "state_snapshot"),
    ("approvals", "evidence"),
    ("failures", "details"),
}

TASK_FUNCTIONS = {"barq_reject_approval_mutation", "barq_set_updated_at"}
TASK_TRIGGERS = {
    "trg_approvals_immutable",
    "trg_executions_set_updated_at",
    "trg_retry_state_set_updated_at",
}


def _test_database_url() -> URL:
    raw_url = os.getenv("BARQ_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip(
            "Set BARQ_TEST_DATABASE_URL to an isolated PostgreSQL database named "
            "barq_s2_2_test* to run migration integration tests."
        )

    url = make_url(raw_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        pytest.fail("BARQ_TEST_DATABASE_URL must use PostgreSQL with asyncpg.")
    if not url.database or not url.database.startswith("barq_s2_2_test"):
        pytest.fail("Refusing to reset a database not named barq_s2_2_test*.")
    return url


async def _reset_public_schema(url: URL) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def migrated_database() -> Iterator[tuple[Config, URL]]:
    url = _test_database_url()
    asyncio.run(_reset_public_schema(url))

    repository_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(repository_root / "alembic.ini"))
    previous_url = os.environ.get("BARQ_DATABASE_URL")
    os.environ["BARQ_DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        yield alembic_config, url
    finally:
        asyncio.run(_reset_public_schema(url))
        if previous_url is None:
            os.environ.pop("BARQ_DATABASE_URL", None)
        else:
            os.environ["BARQ_DATABASE_URL"] = previous_url


def _orm_schema_signature() -> dict[str, dict[str, set[str] | tuple[str, ...]]]:
    signature: dict[str, dict[str, set[str] | tuple[str, ...]]] = {}
    for table in Base.metadata.sorted_tables:
        signature[table.name] = {
            "columns": tuple(table.columns.keys()),
            "constraints": {
                str(constraint.name) for constraint in table.constraints if constraint.name
            },
            "indexes": {str(index.name) for index in table.indexes if index.name},
        }
    return signature


def _database_schema_signature(
    synchronous_connection: sa.Connection,
) -> dict[str, dict[str, set[str] | tuple[str, ...]]]:
    inspector = sa.inspect(synchronous_connection)
    signature: dict[str, dict[str, set[str] | tuple[str, ...]]] = {}
    for table_name in sorted(APPLICATION_TABLES):
        constraint_names = {
            inspector.get_pk_constraint(table_name)["name"],
            *(constraint["name"] for constraint in inspector.get_unique_constraints(table_name)),
            *(constraint["name"] for constraint in inspector.get_foreign_keys(table_name)),
            *(constraint["name"] for constraint in inspector.get_check_constraints(table_name)),
        }
        indexes = inspector.get_indexes(table_name)
        signature[table_name] = {
            "columns": tuple(column["name"] for column in inspector.get_columns(table_name)),
            "constraints": {name for name in constraint_names if name},
            "indexes": {
                index["name"] for index in indexes if not index.get("duplicates_constraint")
            },
        }
    return signature


async def _schema_evidence(url: URL) -> dict[str, Any]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            tables = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            database_signature = await connection.run_sync(_database_schema_signature)

            timestamp_rows = await connection.execute(
                sa.text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND data_type = 'timestamp with time zone'
                    """
                )
            )
            jsonb_rows = await connection.execute(
                sa.text(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND data_type = 'jsonb'
                    """
                )
            )
            default_rows = await connection.execute(
                sa.text(
                    """
                    SELECT table_name, column_name, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN (
                          'events', 'idempotency_keys', 'executions', 'workflow_state',
                          'approvals', 'failures', 'retry_state'
                      )
                    """
                )
            )
            idempotency_fk = (
                await connection.execute(
                    sa.text(
                        """
                        SELECT condeferrable, condeferred
                        FROM pg_constraint
                        WHERE conname = 'fk_idempotency_keys_event_id_events'
                        """
                    )
                )
            ).one()
            retry_index_predicate = (
                await connection.execute(
                    sa.text(
                        """
                        SELECT pg_get_expr(index_definition.indpred, index_definition.indrelid)
                        FROM pg_index AS index_definition
                        JOIN pg_class AS index_class
                          ON index_class.oid = index_definition.indexrelid
                        WHERE index_class.relname = 'ix_retry_state_due'
                        """
                    )
                )
            ).scalar_one()
            function_rows = await connection.execute(
                sa.text(
                    """
                    SELECT routine_name
                    FROM information_schema.routines
                    WHERE routine_schema = 'public'
                      AND routine_name IN (
                          'barq_reject_approval_mutation', 'barq_set_updated_at'
                      )
                    """
                )
            )
            trigger_rows = await connection.execute(
                sa.text(
                    """
                    SELECT trigger_name
                    FROM information_schema.triggers
                    WHERE trigger_schema = 'public'
                      AND trigger_name IN (
                          'trg_approvals_immutable',
                          'trg_executions_set_updated_at',
                          'trg_retry_state_set_updated_at'
                      )
                    """
                )
            )

            return {
                "tables": tables,
                "database_signature": database_signature,
                "timestamps": {(row.table_name, row.column_name) for row in timestamp_rows},
                "jsonb": {(row.table_name, row.column_name) for row in jsonb_rows},
                "defaults": {
                    (row.table_name, row.column_name): row.column_default for row in default_rows
                },
                "idempotency_fk": tuple(idempotency_fk),
                "retry_index_predicate": retry_index_predicate,
                "functions": {row.routine_name for row in function_rows},
                "triggers": {row.trigger_name for row in trigger_rows},
            }
    finally:
        await engine.dispose()


async def _exercise_database_triggers(url: URL) -> None:
    engine: AsyncEngine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            event_id = (
                await connection.execute(
                    sa.text(
                        """
                        INSERT INTO events (
                            event_id, incident_sys_id, incident_number, event_type
                        ) VALUES (
                            'migration-event-1',
                            '0123456789abcdef0123456789abcdef',
                            'INC-MIGRATION-1',
                            'incident.created'
                        )
                        RETURNING id
                        """
                    )
                )
            ).scalar_one()
            execution_id, execution_updated_at = (
                await connection.execute(
                    sa.text(
                        """
                        INSERT INTO executions (event_record_id, incident_sys_id)
                        VALUES (:event_id, '0123456789abcdef0123456789abcdef')
                        RETURNING execution_id, updated_at
                        """
                    ),
                    {"event_id": event_id},
                )
            ).one()
            retry_updated_at = (
                await connection.execute(
                    sa.text(
                        """
                        INSERT INTO retry_state (execution_id, max_attempts)
                        VALUES (:execution_id, 3)
                        RETURNING updated_at
                        """
                    ),
                    {"execution_id": execution_id},
                )
            ).scalar_one()
            approval_id = (
                await connection.execute(
                    sa.text(
                        """
                        INSERT INTO approvals (execution_id, decision, decided_by)
                        VALUES (:execution_id, 'approved', 'migration-test')
                        RETURNING id
                        """
                    ),
                    {"execution_id": execution_id},
                )
            ).scalar_one()

        async with engine.begin() as connection:
            new_execution_updated_at = (
                await connection.execute(
                    sa.text(
                        """
                        UPDATE executions SET status = 'queued'
                        WHERE execution_id = :execution_id
                        RETURNING updated_at
                        """
                    ),
                    {"execution_id": execution_id},
                )
            ).scalar_one()
            new_retry_updated_at = (
                await connection.execute(
                    sa.text(
                        """
                        UPDATE retry_state SET backoff_seconds = 1
                        WHERE execution_id = :execution_id
                        RETURNING updated_at
                        """
                    ),
                    {"execution_id": execution_id},
                )
            ).scalar_one()

        assert new_execution_updated_at > execution_updated_at
        assert new_retry_updated_at > retry_updated_at

        async with engine.connect() as connection:
            transaction = await connection.begin()
            with pytest.raises(DBAPIError, match="approvals are immutable"):
                await connection.execute(
                    sa.text("UPDATE approvals SET reason = 'changed' WHERE id = :approval_id"),
                    {"approval_id": approval_id},
                )
            await transaction.rollback()

        async with engine.connect() as connection:
            transaction = await connection.begin()
            with pytest.raises(DBAPIError, match="approvals are immutable"):
                await connection.execute(
                    sa.text("DELETE FROM approvals WHERE id = :approval_id"),
                    {"approval_id": approval_id},
                )
            await transaction.rollback()

        async with engine.connect() as connection:
            approval_count = (
                await connection.execute(
                    sa.text("SELECT count(*) FROM approvals WHERE id = :approval_id"),
                    {"approval_id": approval_id},
                )
            ).scalar_one()
            assert approval_count == 1
    finally:
        await engine.dispose()


async def _downgrade_evidence(url: URL) -> dict[str, Any]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            tables = set(await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()))
            function_rows = await connection.execute(
                sa.text(
                    """
                    SELECT routine_name
                    FROM information_schema.routines
                    WHERE routine_schema = 'public'
                      AND routine_name IN (
                          'barq_reject_approval_mutation', 'barq_set_updated_at'
                      )
                    """
                )
            )
            version_count = (
                await connection.execute(sa.text("SELECT count(*) FROM alembic_version"))
            ).scalar_one()
            return {
                "tables": tables,
                "functions": {row.routine_name for row in function_rows},
                "version_count": version_count,
            }
    finally:
        await engine.dispose()


def test_postgresql_migration_round_trip(
    migrated_database: tuple[Config, URL],
) -> None:
    alembic_config, url = migrated_database

    command.upgrade(alembic_config, "head")
    evidence = asyncio.run(_schema_evidence(url))

    assert evidence["tables"] - {"alembic_version"} == APPLICATION_TABLES
    assert evidence["database_signature"] == _orm_schema_signature()
    assert evidence["timestamps"] == TIMESTAMP_COLUMNS
    assert evidence["jsonb"] == JSONB_COLUMNS
    assert evidence["idempotency_fk"] == (True, True)
    retry_predicate = evidence["retry_index_predicate"]
    normalized_retry_predicate = (
        retry_predicate.replace("::text", "").replace("(", "").replace(")", "")
    )
    assert normalized_retry_predicate == "state = 'scheduled'"
    assert evidence["functions"] == TASK_FUNCTIONS
    assert evidence["triggers"] == TASK_TRIGGERS

    defaults = evidence["defaults"]
    uuid_primary_keys = {
        ("events", "id"),
        ("idempotency_keys", "id"),
        ("executions", "execution_id"),
        ("workflow_state", "id"),
        ("approvals", "id"),
        ("failures", "failure_id"),
        ("retry_state", "retry_state_id"),
    }
    assert all(defaults[column] == "gen_random_uuid()" for column in uuid_primary_keys)
    assert defaults[("retry_state", "max_attempts")] is None

    asyncio.run(_exercise_database_triggers(url))

    command.downgrade(alembic_config, "base")
    downgrade = asyncio.run(_downgrade_evidence(url))
    assert downgrade["tables"] == {"alembic_version"}
    assert downgrade["functions"] == set()
    assert downgrade["version_count"] == 0

    command.upgrade(alembic_config, "head")
    reupgrade = asyncio.run(_schema_evidence(url))
    assert reupgrade["tables"] - {"alembic_version"} == APPLICATION_TABLES
