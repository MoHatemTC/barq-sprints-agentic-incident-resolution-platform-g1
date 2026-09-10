from typing import Any

import httpx
import structlog
from fastapi import status

from app.auth.token_manager import ServiceNowTokenManager
from app.core.config import Settings
from app.exceptions.servicenow import (
    ServiceNowAuthenticationError,
    ServiceNowAuthorizationError,
    ServiceNowConnectionError,
    ServiceNowError,
    ServiceNowNotFoundError,
    ServiceNowRateLimitError,
    ServiceNowServerError,
    ServiceNowTimeoutError,
    ServiceNowValidationError,
)
from app.models.incident import Incident, IncidentUpdatePayload

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

    async def get_incident(self, sys_id: str) -> Incident:
        result = await self._request("GET", f"/api/now/table/incident/{sys_id}")
        return Incident.model_validate(result)

    async def find_incident_by_number(self, number: str) -> Incident | None:
        result = await self._request(
            "GET",
            "/api/now/table/incident",
            params={"sysparm_query": f"number={number}", "sysparm_limit": 2},
        )
        incidents = result if isinstance(result, list) else []
        if not incidents:
            return None
        if len(incidents) > 1:
            raise ServiceNowError(
                f"Expected at most one incident for number={number},"
                + "ServiceNow returned {len(incidents)}",
                details={"number": number, "count": len(incidents)},
            )
        return Incident.model_validate(incidents[0])

    async def update_incident(self, sys_id: str, payload: IncidentUpdatePayload) -> Incident:
        body = payload.to_table_api_body()
        result = await self._request("PATCH", f"/api/now/table/incident/{sys_id}", json=body)
        return Incident.model_validate(result)

    ###################################################

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
            logger.warning(
                "servicenow_401_received",
                method=method,
                path=path,
                action="refresh_and_retry_once",
            )
            await self._tokens.get_token(force_refresh=True, failed_token=token)
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
        if response.status_code == status.HTTP_403_FORBIDDEN:
            raise ServiceNowAuthorizationError(
                f"ServiceNow denied access (403) for {method} {path} - "
                "the integration identity likely lacks the required role/ACL",
                status_code=status.HTTP_403_FORBIDDEN,
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
            status.HTTP_422_UNPROCESSABLE_CONTENT,
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
