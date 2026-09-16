from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request

from api.dependencies import get_app_settings, get_db_session, get_redis
from api.lifespan import lifespan
from tests.helpers import mock_settings


@pytest.mark.asyncio
async def test_lifespan_initializes_and_cleans_up_resources() -> None:
    app = FastAPI()
    app.state.settings = mock_settings()

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()

    class AsyncContextManagerMock:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *args):
            pass

    mock_engine.connect = MagicMock(return_value=AsyncContextManagerMock())

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with (
        patch("api.lifespan.create_db_engine", return_value=mock_engine),
        patch("api.lifespan.create_redis_client", return_value=mock_redis),
    ):
        async with lifespan(app):
            assert app.state.engine is mock_engine
            assert app.state.session_factory is not None
            assert app.state.redis is mock_redis

        # After lifespan exits, cleanup must be called and state cleared
        mock_engine.dispose.assert_awaited_once()
        mock_redis.aclose.assert_awaited_once()
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

    with pytest.raises(HTTPException) as exc_info:
        async for _ in get_db_session(request):
            pass
    assert exc_info.value.status_code == 503


def test_get_redis_dependency() -> None:
    mock_redis = MagicMock()
    request = MagicMock(spec=Request)
    request.app.state.redis = mock_redis

    assert get_redis(request) is mock_redis


def test_get_redis_raises_503_when_uninitialized() -> None:
    request = MagicMock(spec=Request)
    request.app.state.redis = None

    with pytest.raises(HTTPException) as exc_info:
        get_redis(request)
    assert exc_info.value.status_code == 503


def test_get_app_settings_dependency() -> None:
    request = MagicMock(spec=Request)
    expected_settings = mock_settings()
    request.app.state.settings = expected_settings

    assert get_app_settings(request) is expected_settings


@pytest.mark.asyncio
async def test_middleware_generates_and_returns_correlation_id() -> None:
    from httpx import ASGITransport, AsyncClient

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
    from httpx import ASGITransport, AsyncClient

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
