"""S2.1 application wiring tests.

Covers the ``create_app`` factory, router mounting, lifespan PostgreSQL/Redis pool
lifecycle (including failure modes), correlation-ID propagation, structured access
logging, and the Langfuse fail-open guarantee (tracing must never break ingestion).
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

import tests.helpers as h
from api.dependencies import get_app_settings, get_db_session, get_redis
from api.lifespan import lifespan
from app.exceptions.app_errors import ServiceUnavailableError
from app.main import create_app
from tests.helpers import mock_settings

# ---------------------------------------------------------------------------
# Expected HTTP surface: every router create_app must mount
# ---------------------------------------------------------------------------
EXPECTED_ROUTES: dict[str, set[str]] = {
    "/health": {"GET"},
    "/ready": {"GET"},
    "/api/v1/webhook/incident": {"POST"},
    "/api/v1/executions/{execution_id}": {"GET"},
    "/api/v1/executions/{execution_id}/trace": {"GET"},
    "/api/v1/incidents/{sys_id}/executions": {"GET"},
    "/api/v1/approvals": {"GET"},
    "/api/v1/approvals/{id}": {"GET"},
    "/api/v1/approvals/{id}/decide": {"POST"},
    "/api/v1/dlq": {"GET"},
    "/api/v1/dlq/{event_id}/replay": {"POST"},
    "/api/v1/eval/results": {"GET"},
    "/api/v1/eval/run": {"POST"},
    "/api/v1/config": {"GET"},
}


def _build_app() -> FastAPI:
    app = create_app(settings=mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN))
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.redis = MagicMock()
    app.state.redis.lpush = AsyncMock(return_value=1)
    app.state.redis.ping = AsyncMock(return_value=True)
    return app


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def test_create_app_returns_fastapi_with_settings() -> None:
    settings = mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN)
    app = create_app(settings=settings)

    assert isinstance(app, FastAPI)
    assert app.state.settings is settings


def test_create_app_mounts_all_documented_routers() -> None:
    app = create_app(settings=mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN))

    # Use the OpenAPI schema: it is the version-stable view of the mounted surface.
    openapi_paths = app.openapi()["paths"]
    found: dict[str, set[str]] = {
        path: {m.upper() for m in operations} for path, operations in openapi_paths.items()
    }

    for path, methods in EXPECTED_ROUTES.items():
        assert path in found, f"route {path} is not mounted"
        assert methods <= found[path], (
            f"route {path} missing methods {methods - found[path]} (has {found[path]})"
        )


def test_create_app_exposes_docs_and_openapi() -> None:
    app = _build_app()
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"
    assert app.openapi()["info"]["title"] == "BARQ Agentic Incident Resolution Platform"


@pytest.mark.asyncio
async def test_application_startup_and_shutdown_succeed() -> None:
    """Lifespan startup and shutdown run cleanly through the real ASGI lifespan hook."""
    app = create_app(settings=mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN))

    engine = MagicMock()
    engine.dispose = AsyncMock()
    conn = MagicMock()
    conn.execute = AsyncMock()

    class _ConnCtx:
        async def __aenter__(self) -> MagicMock:
            return conn

        async def __aexit__(self, *args: object) -> None:
            return None

    engine.connect = MagicMock(return_value=_ConnCtx())

    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    redis.aclose = AsyncMock()

    settings = mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN)
    app.state.settings = settings
    with (
        patch("api.lifespan.create_db_engine", return_value=engine),
        patch("api.lifespan.create_redis_client", return_value=redis),
    ):
        async with app.router.lifespan_context(app):
            assert app.state.engine is engine
            assert app.state.session_factory is not None
            assert app.state.redis is redis

        engine.dispose.assert_awaited_once()
        redis.aclose.assert_awaited_once()
        assert app.state.engine is None
        assert app.state.session_factory is None
        assert app.state.redis is None


# ---------------------------------------------------------------------------
# Lifespan: PostgreSQL / Redis pool lifecycle
# ---------------------------------------------------------------------------
def _mock_engine(ping_ok: bool = True) -> MagicMock:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    conn = MagicMock()
    conn.execute = (
        AsyncMock(return_value="1")
        if ping_ok
        else AsyncMock(side_effect=RuntimeError("SELECT 1 failed: connection refused"))
    )

    class _ConnCtx:
        async def __aenter__(self) -> MagicMock:
            return conn

        async def __aexit__(self, *args: object) -> None:
            return None

    engine.connect = MagicMock(return_value=_ConnCtx())
    return engine


@pytest.mark.asyncio
async def test_lifespan_initializes_and_cleans_up_resources() -> None:
    app = FastAPI()
    app.state.settings = mock_settings()

    engine = _mock_engine()
    redis = MagicMock()
    redis.ping = AsyncMock()
    redis.aclose = AsyncMock()

    with (
        patch("api.lifespan.create_db_engine", return_value=engine),
        patch("api.lifespan.create_redis_client", return_value=redis),
    ):
        async with lifespan(app):
            assert app.state.engine is engine
            assert app.state.session_factory is not None
            assert app.state.redis is redis

        # After lifespan exits, cleanup must be called and state cleared
        engine.dispose.assert_awaited_once()
        redis.aclose.assert_awaited_once()
        assert app.state.engine is None
        assert app.state.session_factory is None
        assert app.state.redis is None


@pytest.mark.asyncio
async def test_lifespan_preserves_pre_injected_resources() -> None:
    """If resources were already set on app.state (e.g. in tests), lifespan preserves them."""
    app = FastAPI()
    app.state.settings = mock_settings()

    existing_engine = MagicMock()
    existing_engine.dispose = AsyncMock()
    existing_redis = MagicMock()
    existing_redis.aclose = AsyncMock()

    app.state.engine = existing_engine
    app.state.session_factory = MagicMock()
    app.state.redis = existing_redis

    with (
        patch("api.lifespan.create_db_engine") as mock_create_engine,
        patch("api.lifespan.create_redis_client") as mock_create_redis,
    ):
        async with lifespan(app):
            assert app.state.engine is existing_engine
            assert app.state.redis is existing_redis
            mock_create_engine.assert_not_called()
            mock_create_redis.assert_not_called()

        existing_engine.dispose.assert_awaited_once()
        existing_redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_survives_postgres_ping_failure() -> None:
    """A failed SELECT 1 at startup logs a warning but must not crash application startup."""
    app = FastAPI()
    app.state.settings = mock_settings()

    engine = _mock_engine(ping_ok=False)
    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    redis.aclose = AsyncMock()

    with (
        patch("api.lifespan.create_db_engine", return_value=engine),
        patch("api.lifespan.create_redis_client", return_value=redis),
    ):
        async with lifespan(app):
            # Engine and session factory are still installed; only the ping failed.
            assert app.state.engine is engine
            assert app.state.session_factory is not None
            assert app.state.redis is redis

        engine.dispose.assert_awaited_once()
        redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_survives_redis_ping_failure() -> None:
    """A failed Redis PING at startup is non-fatal; the client is still installed."""
    app = FastAPI()
    app.state.settings = mock_settings()

    engine = _mock_engine()
    redis = MagicMock()
    redis.ping = AsyncMock(side_effect=RuntimeError("Redis connection refused"))
    redis.aclose = AsyncMock()

    with (
        patch("api.lifespan.create_db_engine", return_value=engine),
        patch("api.lifespan.create_redis_client", return_value=redis),
    ):
        async with lifespan(app):
            assert app.state.redis is redis

        redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifespan_fails_closed_when_engine_factory_raises() -> None:
    """If the PostgreSQL engine cannot even be constructed, startup fails predictably."""
    app = FastAPI()
    app.state.settings = mock_settings()

    with patch(
        "api.lifespan.create_db_engine",
        side_effect=RuntimeError("invalid database url"),
    ):
        with pytest.raises((RuntimeError, AttributeError)):
            async with lifespan(app):
                pass


@pytest.mark.asyncio
async def test_lifespan_closes_redis_even_when_redis_close_raises() -> None:
    """Shutdown must not skip PostgreSQL disposal when Redis teardown raises."""
    app = FastAPI()
    app.state.settings = mock_settings()

    engine = _mock_engine()
    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    redis.aclose = AsyncMock(side_effect=RuntimeError("already closed"))

    with (
        patch("api.lifespan.create_db_engine", return_value=engine),
        patch("api.lifespan.create_redis_client", return_value=redis),
    ):
        async with lifespan(app):
            pass

    redis.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()
    assert app.state.engine is None


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_db_session_yields_session_and_rolls_back_on_error() -> None:
    mock_session = MagicMock()
    mock_session.rollback = AsyncMock()

    class MockAsyncSessionContext:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_factory = MagicMock(return_value=MockAsyncSessionContext())

    request = MagicMock(spec=Request)
    request.app.state.session_factory = mock_factory

    # Normal yield
    gen = get_db_session(request)
    session = await anext(gen)
    assert session is mock_session
    try:
        await anext(gen)
    except StopAsyncIteration:
        pass

    # Error path triggers rollback
    gen_err = get_db_session(request)
    await anext(gen_err)
    with pytest.raises(RuntimeError):
        await gen_err.athrow(RuntimeError("Database write error"))
    mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_db_session_raises_503_when_uninitialized() -> None:
    request = MagicMock(spec=Request)
    request.app.state.session_factory = None

    with pytest.raises(ServiceUnavailableError) as exc_info:
        async for _ in get_db_session(request):
            pass
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "SERVICE_UNAVAILABLE"


def test_get_redis_dependency() -> None:
    mock_redis = MagicMock()
    request = MagicMock(spec=Request)
    request.app.state.redis = mock_redis

    assert get_redis(request) is mock_redis


def test_get_redis_raises_503_when_uninitialized() -> None:
    request = MagicMock(spec=Request)
    request.app.state.redis = None

    with pytest.raises(ServiceUnavailableError) as exc_info:
        get_redis(request)
    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "SERVICE_UNAVAILABLE"


def test_get_app_settings_dependency() -> None:
    request = MagicMock(spec=Request)
    expected_settings = mock_settings()
    request.app.state.settings = expected_settings

    assert get_app_settings(request) is expected_settings


# ---------------------------------------------------------------------------
# Correlation ID
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_middleware_generates_and_returns_correlation_id() -> None:
    from api.middleware import CORRELATION_ID_HEADER, register_middlewares

    app = FastAPI()
    register_middlewares(app)

    @app.get("/test")
    async def sample_endpoint(request: Request) -> dict[str, str]:
        return {"correlation_id": getattr(request.state, "correlation_id", "")}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/test")
        assert resp.status_code == 200
        cid = resp.headers.get(CORRELATION_ID_HEADER)
        assert cid is not None
        assert len(cid) == 36
        assert resp.json()["correlation_id"] == cid


@pytest.mark.asyncio
async def test_middleware_preserves_incoming_correlation_id() -> None:
    from api.middleware import CORRELATION_ID_HEADER, register_middlewares

    app = FastAPI()
    register_middlewares(app)

    @app.get("/test")
    async def sample_endpoint(request: Request) -> dict[str, str]:
        return {"correlation_id": getattr(request.state, "correlation_id", "")}

    custom_cid = "custom-trace-uuid-12345"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/test", headers={CORRELATION_ID_HEADER: custom_cid})
        assert resp.status_code == 200
        assert resp.headers.get(CORRELATION_ID_HEADER) == custom_cid
        assert resp.json()["correlation_id"] == custom_cid


@pytest.mark.asyncio
async def test_full_app_echoes_and_generates_correlation_id() -> None:
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        generated = await client.get("/health")
        assert len(generated.headers.get("X-Correlation-ID", "")) == 36

        echoed = await client.get("/health", headers={"X-Correlation-ID": "test-correlation-123"})
        assert echoed.headers["X-Correlation-ID"] == "test-correlation-123"


@pytest.mark.asyncio
async def test_access_log_contains_structured_request_fields() -> None:
    """Access logs are structured JSON events carrying method, path, status, latency."""
    from tests.helpers import capture_json_logs, parse_json_log_lines

    app = _build_app()
    with capture_json_logs() as buf:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health", headers={"X-Correlation-ID": "log-cid-42"})
            assert resp.status_code == 200

    events = parse_json_log_lines(buf)
    access = [e for e in events if e.get("event") == "http_request_completed"]
    assert access, f"expected an access log event, captured: {events}"

    entry = access[-1]
    assert entry["method"] == "GET"
    assert entry["path"] == "/health"
    assert entry["status_code"] == 200
    assert isinstance(entry["duration_ms"], (int, float))
    assert entry["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_access_log_contains_correlation_id() -> None:
    """FR: the correlation ID must appear in the structured request log."""
    from tests.helpers import capture_json_logs, parse_json_log_lines

    app = _build_app()
    with capture_json_logs() as buf:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health", headers={"X-Correlation-ID": "log-cid-99"})
            assert resp.status_code == 200

    events = parse_json_log_lines(buf)
    access = [e for e in events if e.get("event") == "http_request_completed"]
    assert access, f"expected an access log event, captured: {events}"
    assert access[-1].get("correlation_id") == "log-cid-99", (
        "http_request_completed log entry is missing the correlation_id field"
    )


# ---------------------------------------------------------------------------
# Langfuse fail-open behavior
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingestion_succeeds_without_any_tracing_backend() -> None:
    """The ingestion path must work with no tracing backend configured at all."""
    sys.modules.pop("langfuse", None)
    app = _build_app()

    accepted = h.make_incident_payload()
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            return_value=MagicMock(status=MagicMock(value="accepted"), execution_id="e"),
        ) as mock_accept,
        patch("api.routers.webhook.send_incident_event"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhook/incident",
                json=accepted,
                headers=h.webhook_oauth_headers(app.state.settings),
            )

    assert resp.status_code == 202, resp.text
    mock_accept.assert_awaited_once()
    # No tracing backend was imported or required by the request path.
    assert "langfuse" not in sys.modules


@pytest.mark.asyncio
async def test_langfuse_outage_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a Langfuse client were present and failing, requests must still succeed.

    The S2.1 implementation ships no tracing integration; this test pins the
    fail-open contract at the import boundary: a broken/unavailable ``langfuse``
    package must not turn any request into an HTTP 5xx.
    """

    class _BrokenLangfuse:
        def __getattr__(self, name: str) -> object:
            raise ConnectionError("Langfuse host unreachable")

    monkeypatch.setitem(sys.modules, "langfuse", _BrokenLangfuse())
    monkeypatch.setitem(sys.modules, "langfuse.client", _BrokenLangfuse())

    app = _build_app()
    with (
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            return_value=MagicMock(status=MagicMock(value="accepted"), execution_id="e"),
        ),
        patch("api.routers.webhook.send_incident_event"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhook/incident",
                json=h.make_incident_payload(),
                headers=h.webhook_oauth_headers(app.state.settings),
            )
            health = await client.get("/health")

    assert resp.status_code == 202, resp.text
    assert health.status_code == 200


@pytest.mark.asyncio
async def test_uninitialized_dependencies_return_503_service_unavailable() -> None:
    """Uninitialized dependencies must return SERVICE_UNAVAILABLE, not raw HTTP_503."""
    app = _build_app()
    app.state.session_factory = None
    app.state.redis = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/webhook/incident",
            json=h.make_incident_payload(),
            headers=h.webhook_oauth_headers(app.state.settings),
        )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "Database session factory is not initialized" in resp.json()["error"]["message"]
