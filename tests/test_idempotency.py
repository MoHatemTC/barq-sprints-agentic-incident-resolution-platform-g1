"""Sequential idempotency behavior against isolated real PostgreSQL."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import fields
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Event, Execution, IdempotencyKey
from app.db.session import SessionFactory, create_session_factory
from app.repositories.idempotency import (
    EventAcceptanceStatus,
    InboundEvent,
    _postgres_constraint_name,
    accept_inbound_event,
)
from db import idempotency as compatibility_idempotency
from tests.db.test_migrations_postgresql import _reset_public_schema, _test_database_url


@pytest.fixture(scope="module")
def idempotency_database_url() -> Iterator[URL]:
    """Apply the baseline to the same guarded database convention as Phase 3."""
    url = _test_database_url()
    asyncio.run(_reset_public_schema(url))

    repository_root = Path(__file__).resolve().parents[1]
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
async def session_factory(idempotency_database_url: URL) -> AsyncIterator[SessionFactory]:
    """Give each test fresh data and connections bound to its event loop."""
    engine = create_async_engine(idempotency_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "TRUNCATE TABLE "
                    "retry_state, failures, approvals, workflow_state, executions, "
                    "idempotency_keys, events CASCADE"
                )
            )
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def _counts(factory: SessionFactory) -> tuple[int, int, int]:
    async with factory() as session:
        keys = await session.scalar(sa.select(sa.func.count()).select_from(IdempotencyKey))
        events = await session.scalar(sa.select(sa.func.count()).select_from(Event))
        executions = await session.scalar(sa.select(sa.func.count()).select_from(Execution))
    return int(keys or 0), int(events or 0), int(executions or 0)


def _event(event_id: str = "event-ABC") -> InboundEvent:
    return InboundEvent(
        event_id=event_id,
        sys_id="0123456789abcdef0123456789abcdef",
        number="INC0010001",
        event_type="incident.created",
    )


def test_input_contract_and_ticket_reexport_are_exact() -> None:
    assert tuple(field.name for field in fields(InboundEvent)) == (
        "event_id",
        "sys_id",
        "number",
        "event_type",
    )
    assert compatibility_idempotency.InboundEvent is InboundEvent
    assert compatibility_idempotency.accept_inbound_event is accept_inbound_event


async def test_first_event_is_accepted_and_fully_persisted(
    session_factory: SessionFactory,
) -> None:
    result = await accept_inbound_event(session_factory, _event())

    assert result.status is EventAcceptanceStatus.ACCEPTED
    assert result.event_record_id is not None
    assert result.execution_id is not None
    assert await _counts(session_factory) == (1, 1, 1)

    async with session_factory() as session:
        event_record = await session.scalar(sa.select(Event))
        execution = await session.scalar(sa.select(Execution))

    assert event_record is not None
    assert event_record.event_id == "event-ABC"
    assert event_record.incident_sys_id == "0123456789abcdef0123456789abcdef"
    assert event_record.incident_number == "INC0010001"
    assert event_record.event_type == "incident.created"
    assert event_record.contract_version == "v1"
    assert execution is not None
    assert execution.event_record_id == event_record.id
    assert execution.incident_sys_id == event_record.incident_sys_id
    assert execution.status == "accepted"
    assert execution.node_reached is None
    assert execution.model_name is None
    assert execution.agent_version is None


async def test_sequential_duplicate_returns_duplicate_without_new_rows(
    session_factory: SessionFactory,
) -> None:
    first = await accept_inbound_event(session_factory, _event())
    duplicate = await accept_inbound_event(session_factory, _event())

    assert first.status is EventAcceptanceStatus.ACCEPTED
    assert duplicate.status is EventAcceptanceStatus.DUPLICATE
    assert duplicate.event_record_id is None
    assert duplicate.execution_id is None
    assert await _counts(session_factory) == (1, 1, 1)


async def test_duplicate_payload_variation_does_not_overwrite_original(
    session_factory: SessionFactory,
) -> None:
    original = _event()
    variation = InboundEvent(
        event_id=original.event_id,
        sys_id=original.sys_id,
        number="INC0099999",
        event_type="incident.updated",
    )

    assert (
        await accept_inbound_event(session_factory, original)
    ).status is EventAcceptanceStatus.ACCEPTED
    assert (
        await accept_inbound_event(session_factory, variation)
    ).status is EventAcceptanceStatus.DUPLICATE

    async with session_factory() as session:
        persisted = await session.scalar(sa.select(Event))

    assert persisted is not None
    assert persisted.incident_number == original.number
    assert persisted.event_type == original.event_type
    assert await _counts(session_factory) == (1, 1, 1)


async def test_distinct_event_ids_for_same_incident_are_both_accepted(
    session_factory: SessionFactory,
) -> None:
    first = _event("event-one")
    second = InboundEvent(
        event_id="event-two",
        sys_id=first.sys_id,
        number=first.number,
        event_type="incident.updated",
    )

    assert (
        await accept_inbound_event(session_factory, first)
    ).status is EventAcceptanceStatus.ACCEPTED
    assert (
        await accept_inbound_event(session_factory, second)
    ).status is EventAcceptanceStatus.ACCEPTED
    assert await _counts(session_factory) == (2, 2, 2)


async def test_downstream_persistence_failure_rolls_back_key_and_allows_retry(
    session_factory: SessionFactory,
) -> None:
    invalid = InboundEvent(
        event_id="event-rollback",
        sys_id="0123456789abcdef0123456789abcdef",
        number="INC0010002",
        event_type="not-a-valid-event-type",
    )

    with pytest.raises(IntegrityError) as caught:
        await accept_inbound_event(session_factory, invalid)

    assert _postgres_constraint_name(caught.value) == "ck_events_event_type"
    assert await _counts(session_factory) == (0, 0, 0)

    valid_retry = InboundEvent(
        event_id=invalid.event_id,
        sys_id=invalid.sys_id,
        number=invalid.number,
        event_type="incident.created",
    )
    retry_result = await accept_inbound_event(session_factory, valid_retry)
    assert retry_result.status is EventAcceptanceStatus.ACCEPTED
    assert await _counts(session_factory) == (1, 1, 1)


async def test_non_idempotency_integrity_error_is_not_converted_to_duplicate(
    session_factory: SessionFactory,
) -> None:
    invalid = InboundEvent(
        event_id="event-unexpected-integrity-error",
        sys_id="0123456789abcdef0123456789abcdef",
        number="INC0010003",
        event_type="invalid",
    )

    with pytest.raises(IntegrityError) as caught:
        await accept_inbound_event(session_factory, invalid)

    assert _postgres_constraint_name(caught.value) == "ck_events_event_type"
    assert await _counts(session_factory) == (0, 0, 0)


async def test_direct_duplicate_key_insert_is_rejected_by_named_database_constraint(
    session_factory: SessionFactory,
) -> None:
    with pytest.raises(IntegrityError) as caught:
        async with session_factory() as session, session.begin():
            session.add(
                Event(
                    event_id="direct-duplicate",
                    incident_sys_id="0123456789abcdef0123456789abcdef",
                    incident_number="INC0010004",
                    event_type="incident.created",
                    contract_version="v1",
                )
            )
            await session.flush()
            session.add(IdempotencyKey(event_id="direct-duplicate"))
            await session.flush()
            session.add(IdempotencyKey(event_id="direct-duplicate"))
            await session.flush()

    assert _postgres_constraint_name(caught.value) == "uq_idempotency_keys_event_id"
    assert await _counts(session_factory) == (0, 0, 0)
