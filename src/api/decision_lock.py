"""Serialize human decisions without blocking LangGraph checkpoint writes."""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_execution_decision(db: AsyncSession, execution_id: UUID) -> None:
    """Hold one PostgreSQL transaction lock until the decision audit commits.

    A row lock on ``executions`` would deadlock a paused graph: its checkpointer
    updates that same row through another connection during resume. The advisory
    lock is shared by both decision routes and does not block those writes.
    """
    digest = hashlib.sha256(b"barq-decision:" + execution_id.bytes).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
