"""Real database boundaries for durable cache waiters and worker exclusion."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from api.routers.approvals import _close_resumed_execution
from app.workers.db import PostgresRepo
from app.workers.sync_engine import create_sync_session_factory
from tests.db.test_approvals_postgresql import (
    INCIDENT_SYS_ID,
    _seed_execution,
    approval_database,
)

# Share the isolated, migrated database fixture; no production database is used.
__all__ = ["approval_database"]


@pytest.mark.parametrize("approved", [True, False])
def test_api_closure_releases_waiters_without_inheriting_approval(approval_database, approved):
    async def seed():
        engine = create_async_engine(approval_database)
        try:
            async with engine.begin() as conn:
                leader = await _seed_execution(conn, uuid4().hex[:12])
                follower = await _seed_execution(conn, uuid4().hex[:12])
            return leader, follower
        finally:
            await engine.dispose()

    leader, follower = asyncio.run(seed())
    engine = sa.create_engine(approval_database.set(drivername="postgresql+psycopg"))
    repo = PostgresRepo(create_sync_session_factory(engine))
    cluster = uuid4()
    try:
        repo.create_cluster(cluster, INCIDENT_SYS_ID, "INC0010991", leader, leader, 0.76, "test")
        # A later event for the same incident must be allowed; execution uniqueness remains.
        repo.add_cluster_member(cluster, follower, INCIDENT_SYS_ID, "INC0010991", 1.0)
        repo.mark_cluster_waiting(follower, "original-follower-correlation")
        assert repo.ready_cluster_waiters() == []
        repo.update_cluster_status(cluster, "awaiting_approval")
        parked_waiters = repo.ready_cluster_waiters()
        assert len(parked_waiters) == 1
        assert parked_waiters[0]["execution_id"] == str(follower)
        assert parked_waiters[0]["correlation_id"] == "original-follower-correlation"

        async def decide():
            async_engine = create_async_engine(approval_database)
            try:
                async with AsyncSession(async_engine) as db, db.begin():
                    await _close_resumed_execution(
                        db,
                        leader,
                        {
                            "outcome": "escalated_low_confidence",
                            "processing_state": "complete" if approved else "failed",
                            "write_back": "written",
                            "cache_draft": {"steps": ["candidate"]} if approved else None,
                        },
                        datetime.now(UTC),
                    )
            finally:
                await async_engine.dispose()

        asyncio.run(decide())
        assert repo.get_cluster(cluster).status == ("resolved" if approved else "failed")
        first = repo.ready_cluster_waiters()
        assert len(first) == 1
        assert first[0]["correlation_id"] == "original-follower-correlation"
        assert first[0]["execution_id"] == str(follower)
        # An enqueue failure leaves the same immutable event ready for the next sweep.
        assert repo.ready_cluster_waiters() == first
        assert repo.get_status(follower) == "accepted"
        with engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text(
                        "SELECT applied_at FROM semantic_cluster_members WHERE execution_id=:id"
                    ),
                    {"id": follower},
                ).scalar_one()
                is None
            )
        repo.claim_for_running(follower)
        assert repo.ready_cluster_waiters() == []
    finally:
        engine.dispose()


def test_execution_guard_excludes_concurrent_delivery_and_releases(approval_database):
    engine = sa.create_engine(approval_database.set(drivername="postgresql+psycopg"))
    repo = PostgresRepo(create_sync_session_factory(engine))
    execution = uuid4()
    try:
        with repo.execution_guard(execution) as first:
            assert first
            with repo.execution_guard(execution) as second:
                assert not second
        with repo.execution_guard(execution) as recovered:
            assert recovered
    finally:
        engine.dispose()


def test_a_newer_run_supersedes_an_older_paused_run(approval_database):
    """A hand-back or caller answer starts a new run; the older pause can no longer be
    approved (a 'cancelled' decision is stored) and ends abandoned."""

    async def seed():
        engine = create_async_engine(approval_database)
        try:
            async with engine.begin() as conn:
                return (
                    await _seed_execution(conn, uuid4().hex[:12]),
                    await _seed_execution(conn, uuid4().hex[:12]),
                )
        finally:
            await engine.dispose()

    paused, newer = asyncio.run(seed())
    engine = sa.create_engine(approval_database.set(drivername="postgresql+psycopg"))
    repo = PostgresRepo(create_sync_session_factory(engine))
    try:
        repo.mark_awaiting_approval(paused)
        assert repo.supersede_paused_runs(INCIDENT_SYS_ID, newer) == [paused]
        assert repo.get_status(paused) == "abandoned"
        assert repo.get_termination_cause(paused) == "superseded_by_newer_run"
        with engine.connect() as conn:
            decisions = conn.execute(
                sa.text("SELECT decision FROM approvals WHERE execution_id = :e"), {"e": paused}
            ).scalars()
            assert list(decisions) == ["cancelled"]
        # Nothing left to supersede; the newer run itself is never touched.
        assert repo.supersede_paused_runs(INCIDENT_SYS_ID, newer) == []
        assert repo.get_status(newer) != "abandoned"
    finally:
        engine.dispose()
