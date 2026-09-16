from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI

from app.core.config import Settings, get_settings

logger = structlog.getLogger(__name__)


async def create_postgres_pool(settings: Settings) -> asyncpg.Pool | None:
    """Create and return an asyncpg connection pool."""
    password = (
        settings.postgres_password.get_secret_value()
        if settings.postgres_password
        else None
    )
    try:
        pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=password,
            database=settings.postgres_db,
            min_size=1,
            max_size=10,
            timeout=5.0,
        )
        logger.info(
            "postgres_pool_initialized",
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
        )
        return pool
    except Exception as exc:
        logger.warning(
            "postgres_pool_initialization_failed",
            error=str(exc),
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
        )
        return None


async def create_redis_client(settings: Settings) -> aioredis.Redis | None:
    """Create and return an async Redis client."""
    password = (
        settings.redis_password.get_secret_value()
        if settings.redis_password
        else None
    )
    try:
        redis_client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=password,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
        await redis_client.ping()
        logger.info(
            "redis_client_initialized",
            host=settings.redis_host,
            port=settings.redis_port,
        )
        return redis_client
    except Exception as exc:
        logger.warning(
            "redis_client_initialization_failed",
            error=str(exc),
            host=settings.redis_host,
            port=settings.redis_port,
        )
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan for PostgreSQL and Redis connection pools."""
    settings: Settings = getattr(app.state, "settings", None) or get_settings()

    # --- Startup ---
    if not hasattr(app.state, "db_pool") or app.state.db_pool is None:
        app.state.db_pool = await create_postgres_pool(settings)

    if not hasattr(app.state, "redis") or app.state.redis is None:
        app.state.redis = await create_redis_client(settings)

    logger.info("application_lifespan_started")

    yield

    # --- Shutdown ---
    logger.info("application_lifespan_stopping")

    if getattr(app.state, "redis", None) is not None:
        try:
            await app.state.redis.aclose()
            logger.info("redis_client_closed")
        except Exception as exc:
            logger.warning("redis_client_close_failed", error=str(exc))
        finally:
            app.state.redis = None

    if getattr(app.state, "db_pool", None) is not None:
        try:
            await app.state.db_pool.close()
            logger.info("postgres_pool_closed")
        except Exception as exc:
            logger.warning("postgres_pool_close_failed", error=str(exc))
        finally:
            app.state.db_pool = None

    logger.info("application_lifespan_stopped")
