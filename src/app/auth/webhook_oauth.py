"""OAuth 2.0 client-credentials authentication for the inbound ServiceNow webhook.

ServiceNow's S1.3 outbound REST message uses an OAuth provider profile. Before each call
it requests a token from ``POST /api/v1/oauth/token`` with the client credentials in the
form body, then sends ``Authorization: Bearer <token>``. This module issues those tokens
and verifies them.

The same endpoint mints a second kind of token for human operators. It carries a
different audience and its own subject, so a credential ServiceNow holds can never open
an operator route (#136, #148), and it carries the roles the operator was granted so the
role check reads a signed claim instead of a header the caller writes (#148).

Tokens are compact JWTs signed with HMAC-SHA256. They are short-lived and carry no
incident data.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import SecretStr

ISSUER = "barq-webhook"
AUDIENCE = "barq-webhook"
#: Audience of tokens minted for the human operator client. Distinct from
#: ``AUDIENCE`` so a ServiceNow token is rejected on every operator route.
OPERATOR_AUDIENCE = "barq-operator"
TOKEN_PATH = "/api/v1/oauth/token"
_HEADER = {"alg": "HS256", "typ": "JWT"}
_BEARER_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="barq-webhook"'}


class WebhookOAuthSettings(Protocol):
    """Settings surface required by the inbound webhook OAuth boundary."""

    webhook_oauth_client_id: str
    webhook_oauth_client_secret: SecretStr
    webhook_oauth_signing_key: SecretStr
    webhook_auth_token: SecretStr
    operator_client_id: str
    operator_roles: list[str]


@dataclass(frozen=True)
class WebhookOAuthConfig:
    client_id: str
    client_secret: str
    signing_key: str
    #: The human operator client: its own id, its own secret, its own audience.
    operator_client_id: str = ""
    operator_client_secret: str = ""
    operator_roles: tuple[str, ...] = ()
    token_ttl_seconds: int = 300
    leeway_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.client_id or not self.client_secret:
            raise ValueError("webhook OAuth client_id and client_secret must be set")
        if len(self.signing_key) < 32:
            raise ValueError("webhook OAuth signing_key must be at least 32 characters")
        if not self.operator_client_id or not self.operator_client_secret:
            raise ValueError(
                "operator client_id and secret must be set: operator routes have no "
                "other credential to check"
            )


def config_from_settings(settings: WebhookOAuthSettings) -> WebhookOAuthConfig:
    """Build OAuth configuration without exposing secret values to repr or logs."""
    return WebhookOAuthConfig(
        client_id=settings.webhook_oauth_client_id,
        client_secret=settings.webhook_oauth_client_secret.get_secret_value(),
        signing_key=settings.webhook_oauth_signing_key.get_secret_value(),
        operator_client_id=settings.operator_client_id,
        operator_client_secret=settings.webhook_auth_token.get_secret_value(),
        operator_roles=tuple(settings.operator_roles),
    )


class InvalidClientError(Exception):
    """The token request did not present the configured client credentials."""


class InvalidTokenError(Exception):
    """The bearer token is malformed, forged, expired or meant for another audience."""


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _sign(signing_input: bytes, key: str) -> str:
    return _b64encode(hmac.new(key.encode(), signing_input, hashlib.sha256).digest())


def issue_access_token(
    config: WebhookOAuthConfig,
    client_id: str,
    client_secret: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Return an RFC 6749 token response, or raise InvalidClientError.

    Two clients can be presented: the ServiceNow webhook client, which receives
    audience ``AUDIENCE``, and the human operator client, which receives
    audience ``OPERATOR_AUDIENCE`` together with the roles it was granted.
    """
    webhook_id = secrets.compare_digest(client_id.encode(), config.client_id.encode())
    webhook_secret = secrets.compare_digest(client_secret.encode(), config.client_secret.encode())
    operator_id = secrets.compare_digest(client_id.encode(), config.operator_client_id.encode())
    operator_secret = secrets.compare_digest(
        client_secret.encode(), config.operator_client_secret.encode()
    )

    roles: tuple[str, ...] | None = None
    if webhook_id and webhook_secret:
        audience, subject = AUDIENCE, config.client_id
    elif operator_id and operator_secret:
        audience, subject = OPERATOR_AUDIENCE, config.operator_client_id
        roles = config.operator_roles
    else:
        raise InvalidClientError("invalid client credentials")

    issued_at = int(time.time() if now is None else now)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": audience,
        "sub": subject,
        "iat": issued_at,
        "exp": issued_at + config.token_ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    if roles is not None:
        claims["roles"] = list(roles)
    header = _b64encode(json.dumps(_HEADER, separators=(",", ":")).encode())
    payload = _b64encode(json.dumps(claims, separators=(",", ":")).encode())
    signature = _sign(f"{header}.{payload}".encode(), config.signing_key)
    return {
        "access_token": f"{header}.{payload}.{signature}",
        "token_type": "Bearer",
        "expires_in": config.token_ttl_seconds,
    }


def verify_access_token(
    config: WebhookOAuthConfig, token: str, *, now: float | None = None
) -> dict[str, Any]:
    """Return the claims of a ServiceNow webhook token, or raise InvalidTokenError."""
    return _verify_claims(config, token, audience=AUDIENCE, sub=config.client_id, now=now)


def verify_operator_token(
    config: WebhookOAuthConfig, token: str, *, now: float | None = None
) -> dict[str, Any]:
    """Return the claims of an operator token, or raise InvalidTokenError.

    A webhook token fails here on audience before anything else is looked at,
    and a token minted for another subject fails on ``sub``. Roles are *not*
    checked here: a token whose roles are missing or empty must reach
    ``require_role`` and come back as 403, not 401 (#148).
    """
    return _verify_claims(
        config, token, audience=OPERATOR_AUDIENCE, sub=config.operator_client_id, now=now
    )


def _verify_claims(
    config: WebhookOAuthConfig,
    token: str,
    *,
    audience: str,
    sub: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Return the token's claims, or raise InvalidTokenError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidTokenError("malformed token")
    header_b64, payload_b64, signature = parts

    expected = _sign(f"{header_b64}.{payload_b64}".encode(), config.signing_key)
    if not secrets.compare_digest(signature.encode(), expected.encode()):
        raise InvalidTokenError("bad signature")

    try:
        header = json.loads(_b64decode(header_b64))
        claims = json.loads(_b64decode(payload_b64))
    except (ValueError, binascii.Error) as exc:
        raise InvalidTokenError("malformed token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise InvalidTokenError("malformed token")
    if header.get("alg") != "HS256":
        raise InvalidTokenError("unexpected algorithm")

    current = time.time() if now is None else now
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, int) or not isinstance(iat, int):
        raise InvalidTokenError("missing exp or iat")
    if current > exp + config.leeway_seconds:
        raise InvalidTokenError("token expired")
    if iat > current + config.leeway_seconds:
        raise InvalidTokenError("token issued in the future")
    if claims.get("iss") != ISSUER or claims.get("aud") != audience:
        raise InvalidTokenError("wrong issuer or audience")
    if claims.get("sub") != sub:
        raise InvalidTokenError("unknown client")
    return claims


def _token_error(error: str, http_status: int) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if http_status == status.HTTP_401_UNAUTHORIZED:
        headers["WWW-Authenticate"] = 'Basic realm="barq-webhook"'
    return JSONResponse({"error": error}, status_code=http_status, headers=headers)


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode()
    except (ValueError, binascii.Error):
        return None
    client_id, sep, client_secret = decoded.partition(":")
    return (client_id, client_secret) if sep else None


def create_token_router(get_config: Callable[[], WebhookOAuthConfig]) -> APIRouter:
    """Router exposing the client-credentials token endpoint at TOKEN_PATH."""
    router = APIRouter(tags=["Webhook OAuth"])

    @router.post(TOKEN_PATH, summary="Issue a webhook access token (client credentials)")
    async def token(request: Request) -> JSONResponse:
        form = parse_qs((await request.body()).decode(errors="replace"))

        def field(name: str) -> str:
            return form.get(name, [""])[0]

        if field("grant_type") != "client_credentials":
            return _token_error("unsupported_grant_type", status.HTTP_400_BAD_REQUEST)

        client_id, client_secret = _basic_credentials(request) or (
            field("client_id"),
            field("client_secret"),
        )
        try:
            body = issue_access_token(get_config(), client_id, client_secret)
        except InvalidClientError:
            return _token_error("invalid_client", status.HTTP_401_UNAUTHORIZED)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    return router


def bearer_dependency(
    get_config: Callable[[], WebhookOAuthConfig],
) -> Callable[[Request], dict[str, Any]]:
    """FastAPI dependency that returns the verified claims or raises 401."""

    def verify(request: Request) -> dict[str, Any]:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Missing bearer token", headers=_BEARER_CHALLENGE
            )
        try:
            return verify_access_token(get_config(), header[7:].strip())
        except InvalidTokenError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Invalid bearer token", headers=_BEARER_CHALLENGE
            ) from exc

    return verify
