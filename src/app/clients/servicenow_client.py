from typing import Any

import httpx
import structlog
from fastapi import status

from app.auth.token_manager import ServiceNowTokenManager
from app.core.config import Settings
from app.exceptions.servicenow import (
    ServiceNowAuthenticationError,
    ServiceNowConnectionError,
    ServiceNowError,
    ServiceNowNotFoundError,
    ServiceNowRateLimitError,
    ServiceNowServerError,
    ServiceNowTimeoutError,
    ServiceNowValidationError,
)

logger = structlog.getLogger(__name__)


class ServiceNowClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        token_manager: ServiceNowTokenManager | None = None,
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(timeout=settings.servicenow_timeout_seconds)
        self._owns_http_client = http_client is None
        self._tokens = token_manager or ServiceNowTokenManager(settings, self._http)

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def __aenter__(self) -> "ServiceNowClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        _retry_on_auth_failure: bool = True,
    ) -> Any:
        url = f"{self._settings.servicenow_instance_url}{path}"
        token = await self._tokens.get_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if json is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = await self._http.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=self._settings.servicenow_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ServiceNowTimeoutError(
                f"Timed out requesting {method} {url} after "
                f"{self._settings.servicenow_timeout_seconds} seconds"
            ) from exc
        except httpx.ConnectError as exc:
            raise ServiceNowConnectionError(f"Failed to connect to {url}: {exc}") from exc

        if response.status_code == status.HTTP_401_UNAUTHORIZED and _retry_on_auth_failure:
            # Token might be expired or invalid, try refreshing once
            logger.info("Received 401 Unauthorized, attempting to refresh token and retry")
            self._tokens.invalidate()
            token = await self._tokens.get_token()
            headers["Authorization"] = f"Bearer {token}"
            response = await self._http.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=self._settings.servicenow_timeout_seconds,
            )

            if response.status_code == status.HTTP_401_UNAUTHORIZED:
                logger.warning(
                    "Got 401 on %s %s - refreshing token and retrying once",
                    method,
                    path,
                )
                self._tokens.invalidate()
                await self._tokens.get_token(force_refresh=True)
                return await self._request(
                    method, path, params=params, json=json, _retry_on_auth_failure=False
                )

            return self._parse_response(response, method=method, path=path)

    @staticmethod
    def _parse_response(response: httpx.Response, *, method: str, path: str) -> Any:
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            raise ServiceNowAuthenticationError(
                f"Still unauthorized after token refresh for {method} {path}",
                status_code=status.HTTP_401_UNAUTHORIZED,
                details={"body": response.text[:500]},
            )
        if response.status_code == status.HTTP_404_NOT_FOUND:
            raise ServiceNowNotFoundError(
                f"Resource not found for {method} {path}",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"body": response.text[:500]},
            )
        if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            retry_after = response.headers.get("Retry-After")
            raise ServiceNowRateLimitError(
                f"Rate limited on {method} {path}",
                retry_after=float(retry_after) if retry_after else None,
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                details={"body": response.text[:500]},
            )
        if response.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ):
            raise ServiceNowValidationError(
                f"ServiceNow rejected the payload for {method} {path}",
                status_code=response.status_code,
                details={"body": response.text[:500]},
            )
        if (
            status.HTTP_500_INTERNAL_SERVER_ERROR
            <= response.status_code
            < status.HTTP_600_INTERNAL_SERVER_ERROR
        ):
            raise ServiceNowServerError(
                f"ServiceNow server error on {method} {path}",
                status_code=response.status_code,
                details={"body": response.text[:500]},
            )
        if not (status.HTTP_200_OK <= response.status_code < status.HTTP_300_MULTIPLE_CHOICES):
            raise ServiceNowError(
                f"Unexpected status on {method} {path}",
                status_code=response.status_code,
                details={"body": response.text[:500]},
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ServiceNowError(f"Non-JSON response from {method} {path}") from exc
        return data.get("result", data)
