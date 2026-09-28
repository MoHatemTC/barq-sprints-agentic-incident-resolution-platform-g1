"""Real PostgreSQL proof that only one decision proceeds at a time."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.decision_lock import lock_execution_decision


@pytest.mark.asyncio
async def test_decision_lock_serializes_until_commit() -> None:
    raw_url = os.getenv("BARQ_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set BARQ_TEST_DATABASE_URL to an isolated barq_s2_2_test* database")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "barq_s2_2_test"
    ):
        pytest.fail("Decision lock test requires an isolated PostgreSQL test database")

    engine = create_async_engine(url)
    try:
        sessions = async_sessionmaker(engine)
        execution_id = uuid4()
        async with sessions() as first, sessions() as second:
            await lock_execution_decision(first, execution_id)
            waiting = asyncio.create_task(lock_execution_decision(second, execution_id))
            try:
                await asyncio.sleep(0.1)
                assert not waiting.done(), "a second decision passed the first lock"
                await first.commit()
                await asyncio.wait_for(waiting, timeout=2)
                await second.commit()
            finally:
                if not waiting.done():
                    waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)
    finally:
        await engine.dispose()
