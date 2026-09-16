"""Authentication and Role-Based Access Control (RBAC) dependencies."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Request

from app.api.dependencies import get_app_settings
from app.core.config import Settings
from app.exceptions.app_errors import AuthenticationError, PermissionDeniedError


def verify_bearer_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> str:
    """Validate Bearer authentication header against configured webhook auth token."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthenticationError("Missing or invalid Bearer token")

    token = auth_header[7:].strip()
    if not secrets.compare_digest(token, settings.webhook_auth_token):
        raise AuthenticationError("Invalid Bearer token")

    return token


def require_role(required_role: str) -> Callable[..., str]:
    """Dependency factory enforcing caller role (RBAC).

    Inspects 'X-User-Role' header; raises 403 PermissionDeniedError if role does not match.
    """

    def _role_checker(
        _token: Annotated[str, Depends(verify_bearer_token)],
        x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
    ) -> str:
        role = (x_user_role or "").strip().lower()
        if role != required_role.lower():
            raise PermissionDeniedError(f"Role '{required_role}' required to perform this action")
        return role

    return _role_checker


__all__ = [
    "require_role",
    "verify_bearer_token",
]
