"""Authentication and Role-Based Access Control (RBAC) dependencies."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_app_settings
from app.auth.webhook_oauth import InvalidTokenError, config_from_settings, verify_access_token
from app.core.config import Settings
from app.exceptions.app_errors import AuthenticationError, PermissionDeniedError

http_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Bearer token authentication. Enter your token (without 'Bearer ' prefix).",
)


def verify_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer_scheme)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> str:
    """Validate the separate bearer credential for operator-facing API routes."""
    if not credentials or not credentials.credentials:
        raise AuthenticationError("Missing or invalid Bearer token")

    token = credentials.credentials.strip()
    expected = settings.webhook_auth_token.get_secret_value()
    if secrets.compare_digest(token, expected):
        return token
    raise AuthenticationError("Invalid Bearer token")

def verify_webhook_oauth_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer_scheme)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> str:
    """Accept only an OAuth JWT issued for the ServiceNow webhook client."""
    if not credentials or not credentials.credentials:
        raise AuthenticationError("Missing or invalid Bearer token")

    token = credentials.credentials.strip()
    try:
        verify_access_token(config_from_settings(settings), token)
    except InvalidTokenError as exc:
        raise AuthenticationError("Invalid Bearer token") from exc
    return token


def require_role(required_role: str) -> Callable[..., str]:
    """Dependency factory enforcing caller role (RBAC).

    Inspects 'X-User-Role' after the separate operator credential is verified.
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
    "http_bearer_scheme",
    "require_role",
    "verify_bearer_token",
    "verify_webhook_oauth_token",
]
