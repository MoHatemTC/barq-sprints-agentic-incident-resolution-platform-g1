"""Health and readiness probes for platform liveness and dependency checks."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

logger = structlog.getLogger("api.health")

router = APIRouter(tags=["Health"])


@router.get("/health", status_code=status.HTTP_200_OK, summary="Liveness probe")
async def health_check() -> dict[str, str]:
    """Process liveness probe; returns 200 if ASGI process is running."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def readiness_check(request: Request) -> JSONResponse:
    """Readiness probe checking PostgreSQL and Redis connectivity."""
    db_ok = False
    redis_ok = False
    errors: dict[str, str] = {}

    # 1. Check PostgreSQL Engine
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception as exc:
            errors["database"] = str(exc)
    else:
        errors["database"] = "Database engine not initialized"

    # 2. Check Redis Client
    redis_client: Redis | None = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        try:
            await redis_client.ping()
            redis_ok = True
        except Exception as exc:
            errors["redis"] = str(exc)
    else:
        errors["redis"] = "Redis client not initialized"

    if db_ok and redis_ok:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ready",
                "database": "connected",
                "redis": "connected",
            },
        )

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "not_ready",
            "database": "connected" if db_ok else "unavailable",
            "redis": "connected" if redis_ok else "unavailable",
            "details": errors,
        },
    )
