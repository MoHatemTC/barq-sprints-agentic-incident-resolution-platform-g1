from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.api.lifespan import create_postgres_pool, create_redis_client, lifespan
from tests.helpers import mock_settings


@pytest.mark.asyncio
async def test_lifespan_initializes_and_cleans_up_pools() -> None:
    app = FastAPI()
    app.state.settings = mock_settings()

    mock_db_pool = MagicMock()
    mock_db_pool.close = AsyncMock()

    mock_redis = MagicMock()
    mock_redis.aclose = AsyncMock()

    with (
        patch("app.api.lifespan.create_postgres_pool", AsyncMock(return_value=mock_db_pool)),
        patch("app.api.lifespan.create_redis_client", AsyncMock(return_value=mock_redis)),
    ):
        async with lifespan(app):
            assert app.state.db_pool is mock_db_pool
            assert app.state.redis is mock_redis

        # After lifespan exits, cleanup must be called and state cleared
        mock_db_pool.close.assert_awaited_once()
        mock_redis.aclose.assert_awaited_once()
        assert app.state.db_pool is None
        assert app.state.redis is None


@pytest.mark.asyncio
async def test_lifespan_preserves_pre_injected_pools() -> None:
    """If pools were already set on app.state (e.g. in tests), lifespan should not re-initialize."""
    app = FastAPI()
    app.state.settings = mock_settings()

    existing_db = MagicMock()
    existing_db.close = AsyncMock()
    existing_redis = MagicMock()
    existing_redis.aclose = AsyncMock()

    app.state.db_pool = existing_db
    app.state.redis = existing_redis

    with (
        patch("app.api.lifespan.create_postgres_pool") as mock_create_db,
        patch("app.api.lifespan.create_redis_client") as mock_create_redis,
    ):
        async with lifespan(app):
            assert app.state.db_pool is existing_db
            assert app.state.redis is existing_redis
            mock_create_db.assert_not_called()
            mock_create_redis.assert_not_called()

        existing_db.close.assert_awaited_once()
        existing_redis.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_postgres_pool_failure_returns_none() -> None:
    with patch("asyncpg.create_pool", AsyncMock(side_effect=Exception("DB connection refused"))):
        pool = await create_postgres_pool(mock_settings())
        assert pool is None


@pytest.mark.asyncio
async def test_create_redis_client_failure_returns_none() -> None:
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(side_effect=Exception("Redis connection refused"))

    with patch("redis.asyncio.Redis", return_value=mock_redis):
        client = await create_redis_client(mock_settings())
        assert client is None
