"""The failure hook's "can this run still be decided?" query, on real PostgreSQL.

INC0010252: ServiceNow showed "awaiting approval" but no pause was ever stored, so
nobody could decide it. The hook releases such an incident only when this query
says no run for it is decidable — the same rule ``POST /approvals/{id}/decide``
applies: execution ``awaiting_approval``, its pause stored, no decision yet.
"""

import asyncio
from types import SimpleNamespace

import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.workers.incident_state import postgres_pause_lookup
from tests.db.test_approvals_postgresql import _seed_execution, approval_database

__all__ = ["approval_database"]


def _settings(url: sa.URL) -> SimpleNamespace:
    return SimpleNamespace(
        postgres_user=url.username,
        postgres_password=SecretStr(url.password) if url.password else None,
        postgres_host=url.host,
        postgres_port=url.port or 5432,
        postgres_db=url.database,
    )


def _run(url: sa.URL, *statements: tuple[str, dict]) -> None:
    async def go() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                for sql, params in statements:
                    await conn.execute(sa.text(sql), params)
        finally:
            await engine.dispose()

    asyncio.run(go())


def _seed(url: sa.URL, tag: str, incident_sys_id: str):
    """One execution for its own incident, so tests never see each other's rows."""

    async def go():
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                execution = await _seed_execution(conn, tag)
                await conn.execute(
                    sa.text(
                        "UPDATE executions SET incident_sys_id = :sys_id WHERE execution_id = :id"
                    ),
                    {"sys_id": incident_sys_id, "id": execution},
                )
                return execution
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _park(url: sa.URL, execution_id, *, store_pause: bool) -> None:
    statements = [
        (
            "UPDATE executions SET status = 'awaiting_approval' WHERE execution_id = :id",
            {"id": execution_id},
        )
    ]
    if store_pause:
        statements.append(
            (
                "INSERT INTO workflow_state (execution_id, sequence_number, node_name, status) "
                "VALUES (:id, 1, 'hitl.interrupt', 'awaiting_approval')",
                {"id": execution_id},
            )
        )
    _run(url, *statements)


def test_nothing_decidable_when_the_pause_was_never_stored(approval_database):
    incident = "1" * 32
    _park(approval_database, _seed(approval_database, "nostored", incident), store_pause=False)
    assert postgres_pause_lookup(_settings(approval_database))(incident) is False


def test_a_stored_undecided_pause_is_decidable(approval_database):
    incident = "2" * 32
    _park(approval_database, _seed(approval_database, "stored", incident), store_pause=True)
    lookup = postgres_pause_lookup(_settings(approval_database))
    assert lookup(incident) is True
    # Scoped to the incident: another incident's pause does not count.
    assert lookup("9" * 32) is False


def test_a_decided_pause_is_no_longer_decidable(approval_database):
    incident = "3" * 32
    execution = _seed(approval_database, "decided", incident)
    _park(approval_database, execution, store_pause=True)
    _run(
        approval_database,
        (
            "INSERT INTO approvals (execution_id, decision, decided_by) "
            "VALUES (:id, 'rejected', 'test')",
            {"id": execution},
        ),
    )
    assert postgres_pause_lookup(_settings(approval_database))(incident) is False
