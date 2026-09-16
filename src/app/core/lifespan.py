from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import sys

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.db.session import create_db_engine, create_session_factory

logger = structlog.getLogger(__name__)


def create_redis_client(settings: Settings) -> aioredis.Redis:
    """Create an async Redis client instance."""
    password = (
        settings.redis_password.get_secret_value()
        if settings.redis_password
        else None
    )
    return aioredis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
    )


def _resolve_engine_creator():
    api_lifespan = sys.modules.get("api.lifespan")
    if api_lifespan and hasattr(api_lifespan, "create_db_engine"):
        return api_lifespan.create_db_engine
    return create_db_engine


def _resolve_redis_creator():
    api_lifespan = sys.modules.get("api.lifespan")
    if api_lifespan and hasattr(api_lifespan, "create_redis_client"):
        return api_lifespan.create_redis_client
    return create_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifespan for SQLAlchemy engine, sessions, and Redis client."""
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    engine_creator = _resolve_engine_creator()
    redis_creator = _resolve_redis_creator()

    # --- Startup ---
    # 1. Initialize SQLAlchemy Engine & Session Factory
    if not hasattr(app.state, "engine") or app.state.engine is None:
        try:
            app.state.engine = engine_creator(settings)
            app.state.session_factory = create_session_factory(app.state.engine)
            # Test connectivity (non-fatal on startup)
            async with app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info(
                "postgres_engine_connected",
                host=settings.postgres_host,
                port=settings.postgres_port,
            )
        except Exception as exc:
            logger.warning("postgres_ping_failed_on_startup", error=str(exc))
            if not hasattr(app.state, "session_factory") or app.state.session_factory is None:
                app.state.session_factory = create_session_factory(app.state.engine)

    # 2. Initialize Redis Client
    if not hasattr(app.state, "redis") or app.state.redis is None:
        try:
            app.state.redis = redis_creator(settings)
            await app.state.redis.ping()
            logger.info(
                "redis_client_connected",
                host=settings.redis_host,
                port=settings.redis_port,
            )
        except Exception as exc:
            logger.warning("redis_ping_failed_on_startup", error=str(exc))

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

    if getattr(app.state, "engine", None) is not None:
        try:
            await app.state.engine.dispose()
            logger.info("postgres_engine_disposed")
        except Exception as exc:
            logger.warning("postgres_engine_dispose_failed", error=str(exc))
        finally:
            app.state.engine = None
            app.state.session_factory = None

    logger.info("application_lifespan_stopped")
