"""Public API contract for application factory (re-exports from app.main)."""

from app.main import app, create_app

__all__ = [
    "app",
    "create_app",
]
