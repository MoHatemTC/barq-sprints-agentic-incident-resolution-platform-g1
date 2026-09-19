"""Public API contract for authentication and RBAC dependencies (re-exports from app.auth.auth)."""

from __future__ import annotations

from app.auth.auth import (
    UserRole,
    http_bearer_scheme,
    require_role,
    verify_bearer_token,
)

__all__ = [
    "UserRole",
    "http_bearer_scheme",
    "require_role",
    "verify_bearer_token",
]
