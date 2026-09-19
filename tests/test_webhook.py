"""S2.1 ingestion webhook tests — ``POST /api/v1/webhook/incident``.

20 consolidated tests covering the FR-08 pipeline: 401 auth, Contract v1 strict
validation (422 / extra="forbid" / unknown version), persistence-before-202 (503 on
DB failure), Redis enqueue to ``barq:incident:events`` (503 on enqueue failure),
idempotency (sequential + 10-way concurrent), zero downstream execution, probes,
and two ``integration``-marked tests against real PostgreSQL + Redis.

Related negative scenarios are looped inside a single test to keep the suite small;
each loop iteration carries a descriptive assertion message.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

import tests.helpers as h
from app.db.redis.keys import INCIDENT_EVENTS_QUEUE
from app.main import create_app
from app.repositories.idempotency import EventAcceptanceResult, EventAcceptanceStatus
from tests.helpers import mock_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_PAYLOAD = h.make_incident_payload()
AUTH = h.AUTH_HEADERS


def _accepted() -> EventAcceptanceResult:
    return EventAcceptanceResult(
        status=EventAcceptanceStatus.ACCEPTED,
        event_id=VALID_PAYLOAD["event_id"],
        event_record_id=uuid.uuid4(),
        execution_id=uuid.uuid4(),
    )


def _duplicate() -> EventAcceptanceResult:
    return EventAcceptanceResult(
        status=EventAcceptanceStatus.DUPLICATE, event_id=VALID_PAYLOAD["event_id"]
    )


@pytest.fixture
def app_with_mocks():
    settings = mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN)
    app = create_app(settings=settings)
    app.state.engine = MagicMock()
    session_factory = MagicMock()
    app.state.session_factory = session_factory
    mock_redis = MagicMock()
    mock_redis.lpush = AsyncMock(return_value=1)
    mock_redis.ping = AsyncMock(return_value=True)
    app.state.redis = mock_redis
    return app, session_factory, mock_redis


@pytest.fixture
async def client(app_with_mocks):
    app, _, _ = app_with_mocks
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _post_valid(client: AsyncClient):
    """POST the valid payload with the persistence primitive mocked to accept."""
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            return_value=_accepted(),
        ),
        patch("api.routers.webhook.send_incident_event"),
    ):
        return await client.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_authorization_returns_401_envelope_without_token_leak(client) -> None:
    resp = await client.post("/api/v1/webhook/incident", json=VALID_PAYLOAD)

    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "AUTHENTICATION_FAILED"
    for field in ("code", "message", "details", "correlation_id", "timestamp"):
        assert field in body["error"], f"error envelope missing '{field}'"
    assert h.WEBHOOK_TOKEN not in resp.text


@pytest.mark.asyncio
async def test_invalid_bearer_tokens_return_401(client) -> None:
    invalid_headers = [
        {"Authorization": "Bearer wrong-token-value"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
    ]
    for headers in invalid_headers:
        resp = await client.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=headers)
        assert resp.status_code == 401, f"{headers} must be rejected with 401"
        assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"


# ---------------------------------------------------------------------------
# Contract v1 validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_required_fields_return_422(client) -> None:
    for missing in VALID_PAYLOAD:
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != missing}
        resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
        assert resp.status_code == 422, f"missing {missing!r} must return 422"
        assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_missing_contract_version_must_return_422(client) -> None:
    payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "contract_version"}
    resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_malformed_field_values_return_422(client) -> None:
    bad_values: list[tuple[str, object]] = [
        ("sys_id", "a1b2c3d4e5f60718293a4b5c6d7e8f9"),  # 31 hex chars
        ("sys_id", "g1b2c3d4e5f60718293a4b5c6d7e8f90"),  # non-hex character
        ("number", "INC123"),  # too few digits
        ("number", "inc0010001"),  # lowercase prefix
        ("number", "RITM0010001"),  # wrong ticket prefix
        ("event_type", "incident.deleted"),  # unsupported type
        ("event_type", 123),  # wrong JSON type
        ("sys_id", None),  # null value
    ]
    for field, value in bad_values:
        payload = {**VALID_PAYLOAD, field: value}
        resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
        assert resp.status_code == 422, f"{field}={value!r} must return 422"


@pytest.mark.asyncio
async def test_invalid_json_bodies_return_422(client) -> None:
    responses = {
        "raw": await client.post(
            "/api/v1/webhook/incident",
            content=b"{not valid json!!",
            headers={**AUTH, "Content-Type": "application/json"},
        ),
        "empty": await client.post(
            "/api/v1/webhook/incident",
            content=b"",
            headers={**AUTH, "Content-Type": "application/json"},
        ),
        "array": await client.post("/api/v1/webhook/incident", json=[VALID_PAYLOAD], headers=AUTH),
    }
    for label, resp in responses.items():
        assert resp.status_code == 422, f"{label} body must return 422"
        assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_extra_fields_are_rejected_not_silently_ignored(client) -> None:
    extra_shapes = [
        {"unexpected_field": "malicious-or-invalid"},
        {"priority": "1", "urgency": "high"},  # multiple extras
        {"incident": {"priority": 1}},  # nested object extra
        {"parent": {"child": {"deep": "value"}}},  # deeply nested extra
    ]
    for extra in extra_shapes:
        payload = {**VALID_PAYLOAD, **extra}
        resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
        assert resp.status_code == 422, f"extra fields {extra} must return 422"

    # The rejection must name the undeclared field (proof of forbid, not ignore).
    resp = await client.post(
        "/api/v1/webhook/incident",
        json={**VALID_PAYLOAD, "unexpected_field": "x"},
        headers=AUTH,
    )
    field_names = " ".join(
        fe["field"] for fe in resp.json()["error"]["details"].get("field_errors", [])
    )
    assert "unexpected_field" in field_names


@pytest.mark.asyncio
async def test_unsupported_contract_versions_rejected_with_distinct_code(client) -> None:
    for version in ["v2", "v2-draft", "v1.1", "unknown", "", "V1"]:
        payload = {**VALID_PAYLOAD, "contract_version": version}
        resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
        assert resp.status_code == 422, f"contract_version={version!r} must return 422"
        body = resp.json()
        # Distinct code: version mismatch must not look like a missing-field failure.
        assert body["error"]["code"] == "UNKNOWN_CONTRACT_VERSION", body
        assert "v1" in body["error"]["message"]


@pytest.mark.asyncio
async def test_non_string_event_id_must_return_422(client) -> None:
    payload = {**VALID_PAYLOAD, "event_id": ["not", "a", "string"]}
    resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONTRACT_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_arbitrary_string_event_id_is_accepted(client) -> None:
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            return_value=_accepted(),
        ),
        patch("api.routers.webhook.send_incident_event"),
    ):
        payload = {**VALID_PAYLOAD, "event_id": "custom-string-evt-999"}
        resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
        assert resp.status_code == 202
        assert resp.json()["event_id"] == "custom-string-evt-999"


# ---------------------------------------------------------------------------
# Successful ingestion: 202 + persistence + enqueue + zero downstream execution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_valid_payload_returns_202_with_documented_ack_schema(client) -> None:
    resp = await _post_valid(client)

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["event_id"] == VALID_PAYLOAD["event_id"]
    assert body["idempotent_replay"] is False
    assert body["correlation_id"]


@pytest.mark.asyncio
async def test_event_persisted_and_enqueued_to_barq_incident_events(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    with (
        patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as mock_accept,
        patch("api.routers.webhook.send_incident_event") as mock_producer,
    ):
        mock_accept.return_value = _accepted()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 202

    # The exact four-field inbound event reached the persistence primitive.
    inbound = mock_accept.await_args.args[1]
    assert (inbound.event_id, inbound.sys_id, inbound.number, inbound.event_type) == (
        VALID_PAYLOAD["event_id"],
        VALID_PAYLOAD["sys_id"],
        VALID_PAYLOAD["number"],
        VALID_PAYLOAD["event_type"],
    )

    # Exactly one Celery task enqueue on the documented queue with the matching payload.
    mock_producer.assert_called_once()
    payload_arg, _ = mock_producer.call_args[0]
    assert payload_arg == {**VALID_PAYLOAD}


@pytest.mark.asyncio
async def test_zero_downstream_execution_on_request_thread(app_with_mocks) -> None:
    """FR-08: request thread must never invoke retrieval, ServiceNow, or agents."""
    app, _, _ = app_with_mocks
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            return_value=_accepted(),
        ),
        patch("api.routers.webhook.send_incident_event"),
        patch("app.clients.qdrant.get_qdrant_client") as mock_qdrant,
        patch("app.retrieval.hybrid_search.hybrid_search") as mock_retrieve,
        patch("app.clients.servicenow_client.ServiceNowClient") as mock_servicenow,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 202
    mock_qdrant.assert_not_called()
    mock_retrieve.assert_not_called()
    mock_servicenow.assert_not_called()

    import sys

    for forbidden in ("langgraph", "langchain", "langfuse", "openai", "anthropic"):
        assert forbidden not in sys.modules, f"{forbidden} must not load during ingestion"


# ---------------------------------------------------------------------------
# Persistence-before-202 and Redis failure semantics
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_db_failure_returns_503_and_skips_enqueue(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            side_effect=SQLAlchemyError("DB connection dropped"),
        ),
        patch("api.routers.webhook.send_incident_event") as mock_producer,
        patch("api.routers.webhook.logger"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 503, "must fail closed, never a false 202"
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    mock_producer.assert_not_called()


@pytest.mark.asyncio
async def test_endpoint_waits_for_delayed_persistence_before_responding(app_with_mocks) -> None:
    """The 202 may only be returned after the (bounded) persistence completes."""
    app, _, _ = app_with_mocks
    order: list[str] = []

    async def slow_persist(session_factory, inbound):
        order.append("persist:start")
        await asyncio.sleep(0.05)  # simulated bounded DB latency
        order.append("persist:end")
        return _accepted()

    with (
        patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as mock_accept,
        patch("api.routers.webhook.send_incident_event") as mock_producer,
    ):
        mock_accept.side_effect = slow_persist
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 202
    assert order == ["persist:start", "persist:end"]
    mock_producer.assert_called_once()


@pytest.mark.asyncio
async def test_redis_enqueue_failure_returns_503_never_202(app_with_mocks) -> None:
    app, _, _ = app_with_mocks
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            return_value=_accepted(),
        ),
        patch(
            "api.routers.webhook.send_incident_event",
            side_effect=RuntimeError("Redis connection broken"),
        ),
        patch("api.routers.webhook.logger"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_redis_enqueue_failure_compensates_database_claim(app_with_mocks) -> None:
    """Ensure database claim is compensated if Redis enqueue fails."""
    app, mock_session_factory, _ = app_with_mocks
    accepted_result = _accepted()

    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            return_value=accepted_result,
        ),
        patch(
            "api.routers.webhook.send_incident_event",
            side_effect=RuntimeError("Redis connection broken"),
        ),
        patch("api.routers.webhook.logger"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 503
    mock_session_factory.assert_called()


@pytest.mark.asyncio
async def test_webhook_database_failure_returns_503(app_with_mocks) -> None:
    """Concrete SQLAlchemy or connection failure during persistence returns 503."""
    from sqlalchemy.exc import OperationalError

    app, _, _ = app_with_mocks
    with patch(
        "api.routers.webhook.accept_inbound_event",
        new_callable=AsyncMock,
        side_effect=OperationalError("connection lost", None, Exception("socket error")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_webhook_programming_errors_not_masked(app_with_mocks) -> None:
    """Programming defects (MissingGreenlet, AttributeError) must bubble up as 500, not 503."""
    from sqlalchemy.exc import MissingGreenlet

    app, _, _ = app_with_mocks

    # MissingGreenlet should bubble up as 500, not 503
    with patch(
        "api.routers.webhook.accept_inbound_event",
        new_callable=AsyncMock,
        side_effect=MissingGreenlet("async greenlet violation"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with pytest.raises(MissingGreenlet):
                await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    # AttributeError should bubble up as 500, not 503
    with patch(
        "api.routers.webhook.accept_inbound_event",
        new_callable=AsyncMock,
        side_effect=AttributeError("'NoneType' object has no attribute 'val'"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            with pytest.raises(AttributeError):
                await ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_delivery_returns_202_replay_without_reenqueue(client) -> None:
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            side_effect=[_accepted(), _duplicate()],
        ),
        patch("api.routers.webhook.send_incident_event") as mock_producer,
    ):
        first = await client.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)
        second = await client.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)

    assert first.status_code == 202 and first.json()["idempotent_replay"] is False
    assert second.status_code == 202 and second.json()["idempotent_replay"] is True
    # Exactly one Celery enqueue for two deliveries of the same event_id.
    mock_producer.assert_called_once()


@pytest.mark.asyncio
async def test_ten_concurrent_duplicates_produce_single_enqueue(app_with_mocks) -> None:
    """EC-01: simultaneous same-event_id requests must collapse to one enqueue."""
    app, _, _ = app_with_mocks
    lock = asyncio.Lock()
    accepted_seen = 0

    async def arbitrating_accept(session_factory, inbound):
        # Mirrors the DB unique constraint: first caller wins, rest are duplicates.
        nonlocal accepted_seen
        async with lock:
            if accepted_seen == 0:
                accepted_seen += 1
                return _accepted()
            return _duplicate()

    with (
        patch("api.routers.webhook.accept_inbound_event", new_callable=AsyncMock) as mock_accept,
        patch("api.routers.webhook.send_incident_event") as mock_producer,
    ):
        mock_accept.side_effect = arbitrating_accept
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            responses = await asyncio.gather(
                *[
                    ac.post("/api/v1/webhook/incident", json=VALID_PAYLOAD, headers=AUTH)
                    for _ in range(10)
                ]
            )

    assert all(r.status_code == 202 for r in responses), [r.status_code for r in responses]
    replays = [r.json()["idempotent_replay"] for r in responses]
    assert replays.count(True) == 9 and replays.count(False) == 1, replays
    assert mock_producer.call_count == 1


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_returns_200_liveness(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_200_when_up_and_503_with_degraded_components(app_with_mocks) -> None:
    app, _, mock_redis = app_with_mocks

    def _engine(healthy: bool) -> MagicMock:
        engine = MagicMock()
        conn = MagicMock()

        async def _execute(*args, **kwargs):
            if not healthy:
                raise RuntimeError("connection refused")
            return "1"

        conn.execute = AsyncMock(side_effect=_execute)

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *args):
                return None

        engine.connect = MagicMock(return_value=_Ctx())
        return engine

    redis_down = MagicMock()
    redis_down.ping = AsyncMock(side_effect=RuntimeError("redis down"))

    scenarios: dict[str, tuple[MagicMock | None, MagicMock | None, str, str]] = {
        "pg_down": (_engine(False), mock_redis, "unavailable", "connected"),
        "redis_down": (_engine(True), redis_down, "connected", "unavailable"),
        "both_down": (_engine(False), redis_down, "unavailable", "unavailable"),
        "uninitialized": (None, None, "unavailable", "unavailable"),
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.get("/ready")
        assert ok.status_code == 200
        assert ok.json() == {"status": "ready", "database": "connected", "redis": "connected"}

        for label, (engine, redis, db_state, redis_state) in scenarios.items():
            app.state.engine = engine
            app.state.redis = redis
            resp = await ac.get("/ready")
            assert resp.status_code == 503, f"scenario {label} must return 503"
            body = resp.json()
            assert body["status"] == "not_ready"
            assert body["database"] == db_state, f"{label}: {body}"
            assert body["redis"] == redis_state, f"{label}: {body}"


# ===========================================================================
# Integration: real PostgreSQL + Redis (marked `integration`, skips cleanly)
# ===========================================================================
TEST_DB_NAME = "barq_s2_1_test"
QUEUE = INCIDENT_EVENTS_QUEUE


def _integration_settings():
    try:
        from app.core.config import Settings

        real = Settings()
        return mock_settings(
            webhook_auth_token=h.WEBHOOK_TOKEN,
            postgres_host=real.postgres_host,
            postgres_port=real.postgres_port,
            postgres_user=real.postgres_user,
            postgres_password=real.postgres_password.get_secret_value()
            if real.postgres_password
            else "",
            postgres_db=TEST_DB_NAME,
            redis_host=real.redis_host,
            redis_port=real.redis_port,
            redis_password=real.redis_password.get_secret_value() if real.redis_password else None,
        )
    except Exception:
        return mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN, postgres_db=TEST_DB_NAME)


def _run_async(coro):
    return asyncio.run(coro)


async def _server_reachable(settings) -> bool:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        h.build_database_url_for_db(settings, "postgres"), poolclass=NullPool
    )
    try:
        async with engine.connect():
            return True
    except Exception:
        return False
    finally:
        await engine.dispose()


async def _ensure_database(settings) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        h.build_database_url_for_db(settings, "postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        await engine.dispose()


def _run_migrations(settings) -> None:
    import os

    from alembic import command
    from alembic.config import Config

    url = h.build_database_url_for_db(settings, TEST_DB_NAME).render_as_string(hide_password=False)
    previous = os.environ.get("BARQ_DATABASE_URL")
    os.environ["BARQ_DATABASE_URL"] = url
    try:
        command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    finally:
        if previous is None:
            os.environ.pop("BARQ_DATABASE_URL", None)
        else:
            os.environ["BARQ_DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def integration_database_url() -> str:
    """Create/upgrade the dedicated barq_s2_1_test database; skip if PG unreachable."""
    settings = _integration_settings()

    if not _run_async(_server_reachable(settings)):
        pytest.skip(
            "Integration infrastructure unavailable: PostgreSQL not reachable at "
            f"{settings.postgres_host}:{settings.postgres_port} "
            "(run `docker compose up -d postgres redis` to enable integration tests)."
        )

    _run_async(_ensure_database(settings))
    _run_migrations(settings)

    from app.db.session import build_database_url

    return build_database_url(settings).render_as_string(hide_password=False)


@pytest.fixture
async def integration_app(integration_database_url: str):
    """App wired to real PostgreSQL and Redis, with clean tables and queue bookkeeping."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.lifespan import create_redis_client
    from app.db.session import create_session_factory

    settings = _integration_settings()
    app = create_app(settings=settings)

    engine = create_async_engine(integration_database_url, poolclass=NullPool)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    redis = create_redis_client(settings)
    try:
        await redis.ping()
    except Exception as exc:  # pragma: no cover - depends on local docker
        await engine.dispose()
        pytest.skip(f"Integration infrastructure unavailable: Redis ping failed ({exc})")
    app.state.redis = redis

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE retry_state, failures, approvals, workflow_state, "
                "executions, idempotency_keys, events CASCADE"
            )
        )

    await redis.delete(QUEUE)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, engine, redis

    await redis.delete(QUEUE)
    await engine.dispose()
    await redis.aclose()


def _integration_payload() -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "sys_id": h.VALID_SYS_ID,
        "number": h.VALID_NUMBER,
        "event_type": "incident.created",
        "contract_version": "v1",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_event_persisted_and_enqueued_end_to_end(integration_app) -> None:
    client, engine, redis = integration_app
    payload = _integration_payload()

    resp = await client.post("/api/v1/webhook/incident", json=payload, headers=AUTH)
    assert resp.status_code == 202, resp.text
    assert resp.json()["idempotent_replay"] is False

    import sqlalchemy as sa

    async with engine.connect() as conn:
        event_row = (
            await conn.execute(
                sa.text(
                    "SELECT event_id, incident_sys_id, incident_number, event_type, "
                    "contract_version FROM events"
                )
            )
        ).fetchall()
        execution_statuses = (
            (await conn.execute(sa.text("SELECT status FROM executions"))).scalars().all()
        )
        key_count = await conn.scalar(sa.text("SELECT COUNT(*) FROM idempotency_keys"))

    assert event_row == [
        (payload["event_id"], payload["sys_id"], payload["number"], payload["event_type"], "v1")
    ]
    assert execution_statuses == ["accepted"]
    assert key_count == 1

    queue_items = await redis.lrange(QUEUE, 0, -1)
    assert len(queue_items) == 1
    assert json.loads(queue_items[0])["event_id"] == payload["event_id"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_concurrent_duplicates_single_row_single_enqueue(integration_app) -> None:
    """EC-01 proof against real PostgreSQL: 10 concurrent same-event_id requests."""
    import sqlalchemy as sa

    client, engine, redis = integration_app
    payload = _integration_payload()

    responses = await asyncio.gather(
        *[client.post("/api/v1/webhook/incident", json=payload, headers=AUTH) for _ in range(10)]
    )

    assert all(r.status_code == 202 for r in responses), [r.status_code for r in responses]
    replays = [r.json()["idempotent_replay"] for r in responses]
    assert replays.count(False) == 1 and replays.count(True) == 9, replays

    async with engine.connect() as conn:
        event_count = await conn.scalar(sa.text("SELECT COUNT(*) FROM events"))
        execution_count = await conn.scalar(sa.text("SELECT COUNT(*) FROM executions"))
        key_count = await conn.scalar(sa.text("SELECT COUNT(*) FROM idempotency_keys"))
    assert (event_count, execution_count, key_count) == (1, 1, 1)
    assert await redis.llen(QUEUE) == 1
