"""Authentication and Role-Based Access Control (RBAC) dependencies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_app_settings
from app.auth.roles import UserRole
from app.auth.webhook_oauth import (
    InvalidTokenError,
    WebhookOAuthConfig,
    verify_access_token,
)
from app.core.config import Settings
from app.exceptions.app_errors import AuthenticationError, PermissionDeniedError

http_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Bearer token authentication. Enter your token (without 'Bearer ' prefix).",
)


def verify_bearer_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer_scheme)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> str:
    """Validate Bearer authentication header against configured webhook auth token or OAuth JWT."""
    if not credentials or not credentials.credentials:
        raise AuthenticationError("Missing or invalid Bearer token")

    token = credentials.credentials.strip()

    # Check if token is a valid signed OAuth 2.0 access token
    oauth_cfg = WebhookOAuthConfig.from_settings_or_fallback(settings)
    try:
        claims: dict[str, Any] = verify_access_token(oauth_cfg, token)
        request.state.oauth_claims = claims
        if "role" in claims:
            request.state.user_role = claims["role"]
        return token
    except InvalidTokenError:
        pass

    raise AuthenticationError("Invalid Bearer token")


def require_role(required_role: UserRole | str) -> Callable[..., str]:
    """Dependency factory enforcing caller role (RBAC).

    Inspects OAuth token claims or 'X-User-Role' header; raises 403 PermissionDeniedError
    if the caller does not satisfy the required role.
    """
    target = (
        required_role.value
        if isinstance(required_role, UserRole)
        else str(required_role).lower()
    )

    def _role_checker(
        request: Request,
        _token: Annotated[str, Depends(verify_bearer_token)],
        x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
    ) -> str:
        # Check explicit header first, then OAuth claims on request.state
        role_str = (x_user_role or getattr(request.state, "user_role", None) or "").strip().lower()
        if not role_str:
            raise PermissionDeniedError(f"Role '{target}' required to perform this action")

        try:
            caller_role = UserRole(role_str)
            if not caller_role.satisfies(target):
                raise PermissionDeniedError(f"Role '{target}' required to perform this action")
        except ValueError:
            # Fall back to exact string match if role is not in enum
            if role_str != target:
                raise PermissionDeniedError(
                    f"Role '{target}' required to perform this action"
                ) from None

        return role_str

    return _role_checker


__all__ = [
    "UserRole",
    "http_bearer_scheme",
    "require_role",
    "verify_bearer_token",
]
