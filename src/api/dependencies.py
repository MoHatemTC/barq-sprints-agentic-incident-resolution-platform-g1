"""Public API contract for FastAPI dependencies (re-exports from app.api.dependencies)."""

from app.api.dependencies import (
    get_app_settings,
    get_db_session,
    get_redis,
)

__all__ = [
    "get_app_settings",
    "get_db_session",
    "get_redis",
]
