from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from fastapi import status

from app.core.config import Settings
from app.exceptions.servicenow import (
    ServiceNowAuthenticationError,
    ServiceNowConnectionError,
    ServiceNowTimeoutError,
)
from app.models.oauth import OAuthTokenResponse

logger = structlog.get_logger(__name__)


class ServiceNowTokenManager:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: datetime | None = None

    async def get_token(
        self, *, force_refresh: bool = False, failed_token: str | None = None
    ) -> str:
        """
        Retrieves a valid access token, refreshing it if necessary.
        If force_refresh is True or token is expired, it will always refresh the token.
        """
        if not force_refresh and self._is_valid():
            return self._token  # type: ignore[return-value]

        async with self._lock:
            # If another coroutine refreshed the token while we were waiting for the lock, return it
            if failed_token and self._token != failed_token and self._is_valid():
                return self._token  # type: ignore[return-value]

            if not force_refresh and self._is_valid():
                return self._token  # type: ignore[return-value]

            if self._refresh_token:
                await self._refresh_access_token()
            else:
                await self._fetch_access_token()

            return self._token  # type: ignore[return-value]

    async def _fetch_access_token(self) -> None:
        """Fetches a new access token using the configured password grant."""
        form = {
            "grant_type": "password",
            "client_id": self._settings.servicenow_client_id,
            "client_secret": self._settings.servicenow_client_secret.get_secret_value(),
            "username": self._settings.servicenow_username,
            "password": self._settings.servicenow_password.get_secret_value(),
        }
        await self._send_token_request(form)

    async def _refresh_access_token(self) -> None:
        """
        Refreshes the access token using the refresh token.
        """
        form = {
            "grant_type": "refresh_token",
            "client_id": self._settings.servicenow_client_id,
            "client_secret": self._settings.servicenow_client_secret.get_secret_value(),
            "refresh_token": self._refresh_token or "",
        }
        try:
            await self._send_token_request(form)
        except ServiceNowAuthenticationError:
            # If refresh fails, purge token state and surface auth error
            self._token = None
            self._refresh_token = None
            self._expires_at = None
            raise

    async def _send_token_request(self, form: dict) -> None:
        url = f"{self._settings.servicenow_instance_url}/oauth_token.do"
        try:
            response = await self._http.post(
                url,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self._settings.servicenow_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ServiceNowTimeoutError(
                f"Timed out requesting OAuth token from {url} after "
                f"{self._settings.servicenow_timeout_seconds} seconds"
            ) from exc
        except httpx.ConnectError as exc:
            raise ServiceNowConnectionError(
                "Could not connect to ServiceNow OAuth endpoint"
            ) from exc

        if response.status_code != status.HTTP_200_OK:
            logger.error(
                "ServiceNow OAuth token request failed",
                status_code=response.status_code,
            )
            # Secrets and response text excluded from exception to prevent log leaks
            raise ServiceNowAuthenticationError(
                f"ServiceNow rejected OAuth token request with status {response.status_code}",
                status_code=response.status_code,
            )
        token = OAuthTokenResponse.model_validate(response.json())
        self._token = token.access_token

        if token.refresh_token:
            self._refresh_token = token.refresh_token
        self._expires_at = token.expires_at

        logger.info(
            "Successfully obtained new ServiceNow OAuth token",
            expires_at=self._expires_at.isoformat(),
        )

    def _is_valid(self) -> bool:
        if self._token is None or self._expires_at is None:
            return False
        buffer = timedelta(seconds=self._settings.servicenow_token_expiry_buffer_seconds)
        return datetime.now(UTC) < (self._expires_at - buffer)

    def invalidate(self) -> None:
        """Invalidates current access token state on receiving 401."""
        self._token = None
        self._expires_at = None
