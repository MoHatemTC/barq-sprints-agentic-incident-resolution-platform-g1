"""Public API contract for authentication and RBAC dependencies (re-exports from app.auth.auth)."""

from __future__ import annotations

from app.auth.auth import (
    http_bearer_scheme,
    require_role,
    verify_bearer_token,
)

__all__ = [
    "http_bearer_scheme",
    "require_role",
    "verify_bearer_token",
]

