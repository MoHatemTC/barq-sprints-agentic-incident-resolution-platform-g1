"""Authentication and Role-Based Access Control (RBAC) dependencies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_app_settings
from app.auth.webhook_oauth import (
    InvalidTokenError,
    config_from_settings,
    verify_access_token,
    verify_operator_token,
)
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
) -> dict[str, Any]:
    """Validate the operator credential and return its verified claims.

    Only tokens minted for the operator client (audience ``barq-operator``)
    pass. A ServiceNow webhook token fails here on audience, which is the
    boundary #136 and #148 ask for; the static token fails because it is not
    a JWT at all.
    """
    if not credentials or not credentials.credentials:
        raise AuthenticationError("Missing or invalid Bearer token")

    token = credentials.credentials.strip()
    try:
        return verify_operator_token(config_from_settings(settings), token)
    except InvalidTokenError as exc:
        raise AuthenticationError("Invalid Bearer token") from exc


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

    The role is read from the verified operator token's ``roles`` claim. It
    used to come from the ``X-User-Role`` header, which the caller writes
    themselves (#148). A token that carries no such role is refused with 403,
    not 401: the credential was valid, the authorisation was not.
    """

    def _role_checker(
        _claims: Annotated[dict[str, Any], Depends(verify_bearer_token)],
    ) -> str:
        roles = _claims.get("roles")
        granted = (
            {str(role).strip().lower() for role in roles} if isinstance(roles, list) else set()
        )
        role = required_role.lower()
        if role not in granted:
            raise PermissionDeniedError(f"Role '{required_role}' required to perform this action")
        return role

    return _role_checker


__all__ = [
    "http_bearer_scheme",
    "require_role",
    "verify_bearer_token",
    "verify_webhook_oauth_token",
]
