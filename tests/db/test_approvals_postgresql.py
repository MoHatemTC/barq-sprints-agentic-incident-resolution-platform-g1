"""Approval immutability proven against real PostgreSQL (#147).

The route refuses a second decision itself, but the guarantee that has to hold
even when that check is bypassed or raced is the one in the database:
``uq_approvals_execution_workflow_state`` makes a second row for one execution
(or one node) impossible to store.

These tests need a real PostgreSQL database, so they run only when
``BARQ_TEST_DATABASE_URL`` points at an isolated ``barq_s2_2_test*`` database,
and they reset its schema before and after the module.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

INCIDENT_SYS_ID = "0123456789abcdef0123456789abcdef"


def _test_database_url() -> URL:
    raw_url = os.getenv("BARQ_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip(
            "Set BARQ_TEST_DATABASE_URL to an isolated PostgreSQL database named "
            "barq_s2_2_test* to run the approval immutability tests."
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
    reset_engine = create_async_engine(url)
    try:
        async with reset_engine.begin() as connection:
            await connection.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        await reset_engine.dispose()


@pytest.fixture(scope="module")
def approval_database() -> Iterator[URL]:
    """A schema migrated to head, restored to empty when the module finishes."""
    url = _test_database_url()
    asyncio.run(_reset_public_schema(url))

    repository_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(repository_root / "alembic.ini"))
    previous_url = os.environ.get("BARQ_DATABASE_URL")
    os.environ["BARQ_DATABASE_URL"] = url.render_as_string(hide_password=False)
    try:
        command.upgrade(alembic_config, "head")
        yield url
    finally:
        asyncio.run(_reset_public_schema(url))
        if previous_url is None:
            os.environ.pop("BARQ_DATABASE_URL", None)
        else:
            os.environ["BARQ_DATABASE_URL"] = previous_url


@pytest.fixture
async def engine(approval_database: URL) -> AsyncIterator[AsyncEngine]:
    async_engine = create_async_engine(approval_database)
    try:
        yield async_engine
    finally:
        await async_engine.dispose()


async def _seed_execution(connection: AsyncConnection, tag: str) -> UUID:
    event_id = (
        await connection.execute(
            sa.text(
                """
                INSERT INTO events (event_id, incident_sys_id, incident_number, event_type)
                VALUES (:event_id, :incident_sys_id, :incident_number, 'incident.created')
                RETURNING id
                """
            ),
            {
                "event_id": f"evt-approval-{tag}",
                "incident_sys_id": INCIDENT_SYS_ID,
                "incident_number": f"INC-APPROVAL-{tag}",
            },
        )
    ).scalar_one()
    return (
        await connection.execute(
            sa.text(
                """
                INSERT INTO executions (event_record_id, incident_sys_id)
                VALUES (:event_id, :incident_sys_id)
                RETURNING execution_id
                """
            ),
            {"event_id": event_id, "incident_sys_id": INCIDENT_SYS_ID},
        )
    ).scalar_one()


async def _seed_node(
    connection: AsyncConnection,
    execution_id: UUID,
    node_name: str,
    sequence_number: int = 1,
) -> UUID:
    return (
        await connection.execute(
            sa.text(
                """
                INSERT INTO workflow_state (execution_id, sequence_number, node_name)
                VALUES (:execution_id, :sequence_number, :node_name)
                RETURNING id
                """
            ),
            {
                "execution_id": execution_id,
                "sequence_number": sequence_number,
                "node_name": node_name,
            },
        )
    ).scalar_one()


async def _insert_decision(
    connection: AsyncConnection,
    execution_id: UUID,
    decision: str,
    workflow_state_id: UUID | None = None,
) -> None:
    await connection.execute(
        sa.text(
            """
            INSERT INTO approvals (execution_id, workflow_state_id, decision, decided_by)
            VALUES (:execution_id, :workflow_state_id, :decision, 'test-operator')
            """
        ),
        {
            "execution_id": execution_id,
            "workflow_state_id": workflow_state_id,
            "decision": decision,
        },
    )


async def test_second_decision_for_one_execution_cannot_be_stored(
    engine: AsyncEngine,
) -> None:
    """The index refuses a second row for one execution, contradictory or not."""
    async with engine.begin() as connection:
        execution_id = await _seed_execution(connection, "dup")
        await _insert_decision(connection, execution_id, "approved")

    async with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            await _insert_decision(connection, execution_id, "rejected")

    async with engine.connect() as connection:
        stored = (
            (
                await connection.execute(
                    sa.text("SELECT decision FROM approvals WHERE execution_id = :execution_id"),
                    {"execution_id": execution_id},
                )
            )
            .scalars()
            .all()
        )
    assert stored == ["approved"]


async def test_decisions_for_different_executions_do_not_collide(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as connection:
        first = await _seed_execution(connection, "one")
        second = await _seed_execution(connection, "two")
        await _insert_decision(connection, first, "approved")
        await _insert_decision(connection, second, "rejected")

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.text(
                    """
                    SELECT execution_id, decision FROM approvals
                    WHERE execution_id IN (:first, :second)
                    """
                ),
                {"first": first, "second": second},
            )
        ).all()
    # The module's other tests leave their own rows behind, so only this pair is
    # asserted: one decision each, and they do not collide.
    assert {row[0]: row[1] for row in rows} == {first: "approved", second: "rejected"}


async def test_scoping_by_node_keeps_one_decision_per_node(engine: AsyncEngine) -> None:
    """A node's decision is unique, and another node of the same run is free."""
    async with engine.begin() as connection:
        execution_id = await _seed_execution(connection, "nodes")
        act_node = await _seed_node(connection, execution_id, "act")
        write_node = await _seed_node(connection, execution_id, "servicenow.write", 2)
        await _insert_decision(connection, execution_id, "approved", act_node)

    async with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            await _insert_decision(connection, execution_id, "rejected", act_node)

    async with engine.begin() as connection:
        await _insert_decision(connection, execution_id, "rejected", write_node)

    async with engine.connect() as connection:
        decisions = (
            await connection.execute(
                sa.text(
                    """
                    SELECT workflow_state_id, decision FROM approvals
                    WHERE execution_id = :execution_id
                    ORDER BY decision
                    """
                ),
                {"execution_id": execution_id},
            )
        ).all()
    assert {row[0]: row[1] for row in decisions} == {
        act_node: "approved",
        write_node: "rejected",
    }


async def test_a_null_node_decision_is_distinct_from_a_node_decision(
    engine: AsyncEngine,
) -> None:
    """A decision written without a node does not collide with a node's own."""
    async with engine.begin() as connection:
        execution_id = await _seed_execution(connection, "null-node")
        act_node = await _seed_node(connection, execution_id, "act")
        await _insert_decision(connection, execution_id, "approved", act_node)
        await _insert_decision(connection, execution_id, "cancelled", None)
