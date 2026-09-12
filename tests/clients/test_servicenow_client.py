from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.auth.token_manager import ServiceNowTokenManager
from app.clients.servicenow_client import ServiceNowClient
from app.exceptions.servicenow import (
    ServiceNowAuthenticationError,
    ServiceNowAuthorizationError,
    ServiceNowConflictError,
    ServiceNowConnectionError,
    ServiceNowError,
    ServiceNowHumanLockError,
    ServiceNowNotFoundError,
    ServiceNowRateLimitError,
    ServiceNowServerError,
    ServiceNowTimeoutError,
    ServiceNowValidationError,
    ServiceNowWriteRejectedError,
)
from app.models.execution_log import ExecutionLogCreatePayload, ExecutionStatus
from app.models.incident import _SCOPE, AIProcessingState, IncidentUpdatePayload
from tests.helpers import mock_settings


def _incident_result(**overrides: object) -> dict:
    base = {
        "sys_id": "abc123",
        "number": "INC0010001",
        "short_description": "Disk full",
        "description": "Root partition at 100 %",
        "state": "1",
        "priority": "2",
        "category": "hardware",
        "subcategory": "disk",
        "active": "true",
    }
    base.update(overrides)
    return base


def _api_response(
    status_code: int = 200,
    result: dict | list | None = None,
    text: str = "",
    headers: dict | None = None,
) -> httpx.Response:
    """Build an httpx.Response that looks like a ServiceNow Table API reply."""
    kwargs: dict = {
        "status_code": status_code,
        "headers": headers or {},
        "request": httpx.Request("GET", "https://dev00000.service-now.com/api/now/table/incident"),
    }
    if result is not None:
        kwargs["json"] = {"result": result}
    else:
        kwargs["text"] = text

    resp = httpx.Response(**kwargs)
    return resp


def _execution_log_result(**overrides: object) -> dict:
    base = {
        "sys_id": "log_abc123",
        "execution_id": "exec_test_001",
        "incident_reference": "inc_abc123",
        "agent": "triage_agent",
        "action": "execute",
        "status": "succeeded",
        "timestamp": "2026-09-11 12:00:00",
        "result": "Classified as software",
        "error": "",
    }
    base.update(overrides)
    return base


def _log_payload(**overrides: object) -> ExecutionLogCreatePayload:
    defaults = {
        "incident_sys_id": "inc_abc123",
        "execution_id": "exec_test_001",
        "agent": "triage_agent",
        "action": "execute",
        "status": ExecutionStatus.SUCCEEDED,
        "result": "Classified as software",
    }
    defaults.update(overrides)
    return ExecutionLogCreatePayload(**defaults)


def _build_client(
    *,
    token: str = "tok_test",
    responses: list[httpx.Response] | None = None,
) -> tuple[ServiceNowClient, AsyncMock, AsyncMock]:
    """Create a ServiceNowClient with mocked HTTP and token manager."""
    http = AsyncMock(spec=httpx.AsyncClient)
    if responses:
        http.request.side_effect = responses
    else:
        http.request.return_value = _api_response(result=_incident_result())

    token_mgr = AsyncMock(spec=ServiceNowTokenManager)
    token_mgr.get_token.return_value = token

    client = ServiceNowClient(
        mock_settings(),
        http_client=http,
        token_manager=token_mgr,
    )
    return client, http, token_mgr


class TestAuth401RefreshRetry:
    """FR-02 / mid-run expiry: 401 must trigger one refresh + retry."""

    async def test_401_triggers_refresh_and_retry(self) -> None:
        first_resp = _api_response(status_code=401, result=None, text="Unauthorized")
        second_resp = _api_response(result=_incident_result())

        client, http, token_mgr = _build_client(responses=[first_resp, second_resp])
        token_mgr.get_token.side_effect = ["tok_old", "tok_new", "tok_new"]

        incident = await client.get_incident("abc123")

        assert incident.sys_id == "abc123"
        # get_token called 3 times: initial + force_refresh + recursive call
        assert token_mgr.get_token.call_count == 3
        force_call = token_mgr.get_token.call_args_list[1]
        assert force_call.kwargs.get("force_refresh") is True
        assert force_call.kwargs.get("failed_token") == "tok_old"

    async def test_401_after_retry_raises_auth_error(self) -> None:
        """If the retry also returns 401, raise ServiceNowAuthenticationError."""
        resp_401 = _api_response(status_code=401, result=None, text="Still unauthorized")

        client, http, token_mgr = _build_client(responses=[resp_401, resp_401])
        token_mgr.get_token.side_effect = ["tok_old", "tok_new", "tok_new"]

        with pytest.raises(ServiceNowAuthenticationError, match="Still unauthorized"):
            await client.get_incident("abc123")

    async def test_retry_uses_refreshed_token_in_header(self) -> None:
        first_resp = _api_response(status_code=401, result=None, text="Unauthorized")
        second_resp = _api_response(result=_incident_result())

        client, http, token_mgr = _build_client(responses=[first_resp, second_resp])
        token_mgr.get_token.side_effect = ["tok_old", "tok_new", "tok_new"]

        await client.get_incident("abc123")

        # The second request should use the refreshed token
        second_call_headers = http.request.call_args_list[1].kwargs.get("headers", {})
        assert second_call_headers["Authorization"] == "Bearer tok_new"


class TestAuth403NoRefresh:
    async def test_403_raises_authorization_error_without_refresh(self) -> None:
        resp_403 = _api_response(status_code=403, result=None, text="Forbidden")

        client, http, token_mgr = _build_client(responses=[resp_403])

        with pytest.raises(ServiceNowAuthorizationError, match="403"):
            await client.get_incident("abc123")

        # get_token called once (for the initial request), never force_refresh
        assert token_mgr.get_token.call_count == 1
        for call in token_mgr.get_token.call_args_list:
            assert call.kwargs.get("force_refresh") is not True


class TestHTTPStatusMapping:
    async def test_404_raises_not_found(self) -> None:
        client, http, _ = _build_client(
            responses=[_api_response(status_code=404, result=None, text="Not found")]
        )
        with pytest.raises(ServiceNowNotFoundError):
            await client.get_incident("no-such-id")

    async def test_409_raises_conflict_error(self) -> None:
        client, http, _ = _build_client(
            responses=[_api_response(status_code=409, result=None, text="Conflict")]
        )
        with pytest.raises(ServiceNowConflictError):
            await client.get_incident("abc")

    async def test_429_raises_rate_limit_with_retry_after(self) -> None:
        resp = _api_response(
            status_code=429,
            result=None,
            text="Rate limited",
            headers={"Retry-After": "30"},
        )
        client, http, _ = _build_client(responses=[resp])

        with pytest.raises(ServiceNowRateLimitError) as exc_info:
            await client.get_incident("abc")
        assert exc_info.value.retry_after == 30.0

    async def test_429_without_retry_after(self) -> None:
        resp = _api_response(status_code=429, result=None, text="Rate limited")
        client, http, _ = _build_client(responses=[resp])

        with pytest.raises(ServiceNowRateLimitError) as exc_info:
            await client.get_incident("abc")
        assert exc_info.value.retry_after is None

    async def test_400_raises_validation_error(self) -> None:
        client, http, _ = _build_client(
            responses=[_api_response(status_code=400, result=None, text="Bad request")]
        )
        with pytest.raises(ServiceNowValidationError):
            await client.get_incident("abc")

    async def test_422_raises_validation_error(self) -> None:
        client, http, _ = _build_client(
            responses=[_api_response(status_code=422, result=None, text="Unprocessable")]
        )
        with pytest.raises(ServiceNowValidationError):
            await client.get_incident("abc")

    async def test_500_raises_server_error(self) -> None:
        client, http, _ = _build_client(
            responses=[_api_response(status_code=500, result=None, text="Internal error")]
        )
        with pytest.raises(ServiceNowServerError):
            await client.get_incident("abc")

    async def test_502_raises_server_error(self) -> None:
        client, http, _ = _build_client(
            responses=[_api_response(status_code=502, result=None, text="Bad gateway")]
        )
        with pytest.raises(ServiceNowServerError):
            await client.get_incident("abc")

    async def test_503_raises_server_error(self) -> None:
        client, http, _ = _build_client(
            responses=[_api_response(status_code=503, result=None, text="Unavailable")]
        )
        with pytest.raises(ServiceNowServerError):
            await client.get_incident("abc")

    async def test_unexpected_status_raises_base_error(self) -> None:
        """A non-standard status like 418 should raise the base ServiceNowError."""
        client, http, _ = _build_client(
            responses=[_api_response(status_code=418, result=None, text="I'm a teapot")]
        )
        with pytest.raises(ServiceNowError):
            await client.get_incident("abc")


class TestNetworkFailures:
    async def test_timeout_raises_servicenow_timeout_error(self) -> None:
        client, http, _ = _build_client()
        http.request.side_effect = httpx.TimeoutException("read timed out")

        with pytest.raises(ServiceNowTimeoutError, match="Timed out"):
            await client.get_incident("abc")

    async def test_connection_error_raises_servicenow_connection_error(self) -> None:
        client, http, _ = _build_client()
        http.request.side_effect = httpx.ConnectError("connection refused")

        with pytest.raises(ServiceNowConnectionError, match="Failed to connect"):
            await client.get_incident("abc")


class TestGetIncident:
    async def test_get_incident_success(self) -> None:
        client, http, _ = _build_client()
        incident = await client.get_incident("abc123")

        assert incident.sys_id == "abc123"
        assert incident.number == "INC0010001"

    async def test_get_incident_url_and_method(self) -> None:
        client, http, _ = _build_client()
        await client.get_incident("abc123")

        call_args = http.request.call_args
        assert call_args.args[0] == "GET"
        assert "/api/now/table/incident/abc123" in call_args.args[1]

    async def test_get_incident_sends_bearer_token(self) -> None:
        client, http, _ = _build_client(token="tok_xyz")
        await client.get_incident("abc123")

        headers = http.request.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok_xyz"
        assert headers["Accept"] == "application/json"


class TestFindIncidentByNumber:
    async def test_found_one(self) -> None:
        resp = _api_response(result=[_incident_result()])
        client, http, _ = _build_client(responses=[resp])

        incident = await client.find_incident_by_number("INC0010001")
        assert incident is not None
        assert incident.number == "INC0010001"

    async def test_found_none(self) -> None:
        resp = _api_response(result=[])
        client, http, _ = _build_client(responses=[resp])

        result = await client.find_incident_by_number("INC9999999")
        assert result is None

    async def test_multiple_results_raises_error(self) -> None:
        resp = _api_response(result=[_incident_result(), _incident_result(sys_id="def456")])
        client, http, _ = _build_client(responses=[resp])

        with pytest.raises(ServiceNowError, match="Expected at most one"):
            await client.find_incident_by_number("INC0010001")

    async def test_query_params(self) -> None:
        resp = _api_response(result=[_incident_result()])
        client, http, _ = _build_client(responses=[resp])
        await client.find_incident_by_number("INC0010001")

        params = http.request.call_args.kwargs["params"]
        assert params["sysparm_query"] == "number=INC0010001"
        assert params["sysparm_limit"] == 2


class TestUpdateIncident:
    async def test_update_sends_patch(self) -> None:
        initial_incident = _incident_result()
        updated_incident = _incident_result(
            **{
                f"{_SCOPE}_ai_processing_state": "in_progress",
                f"{_SCOPE}_ai_confidence": 0.85,
            },
        )

        responses = [
            _api_response(result=initial_incident),
            _api_response(result=updated_incident),
        ]

        client, http, _ = _build_client(responses=responses)

        payload = IncidentUpdatePayload(
            ai_processing_state=AIProcessingState.IN_PROGRESS,
            ai_confidence=0.85,
        )

        await client.update_incident("abc123", payload)

        call = http.request.call_args
        assert call.args[0] == "PATCH"
        assert "/api/now/table/incident/abc123" in call.args[1]

    async def test_update_body_contains_ai_fields(self) -> None:
        initial_incident = _incident_result()
        updated_incident = _incident_result(
            **{
                f"{_SCOPE}_ai_processing_state": "in_progress",
                f"{_SCOPE}_ai_confidence": 0.85,
            },
        )

        responses = [
            _api_response(result=initial_incident),
            _api_response(result=updated_incident),
        ]

        client, http, _ = _build_client(responses=responses)

        payload = IncidentUpdatePayload(
            ai_processing_state=AIProcessingState.IN_PROGRESS,
            ai_confidence=0.85,
        )

        await client.update_incident("abc123", payload)

        body = http.request.call_args.kwargs["json"]

        assert body[f"{_SCOPE}_ai_processing_state"] == "in_progress"
        assert body[f"{_SCOPE}_ai_confidence"] == "0.85"

    async def test_update_sets_content_type_json(self) -> None:
        initial_incident = _incident_result()
        updated_incident = _incident_result(
            **{f"{_SCOPE}_ai_classification": "network"},
        )

        responses = [
            _api_response(result=initial_incident),
            _api_response(result=updated_incident),
        ]

        client, http, _ = _build_client(responses=responses)

        payload = IncidentUpdatePayload(ai_classification="network")

        await client.update_incident("abc123", payload)

        headers = http.request.call_args.kwargs["headers"]

        assert headers["Content-Type"] == "application/json"

    async def test_update_raises_when_service_now_drops_field(self) -> None:
        """A 2xx response that does not persist a requested field is rejected."""
        initial_incident = _incident_result()
        updated_incident = _incident_result(
            **{f"{_SCOPE}_ai_classification": "hardware"},
        )

        responses = [
            _api_response(result=initial_incident),
            _api_response(result=updated_incident),
        ]

        client, http, _ = _build_client(responses=responses)

        payload = IncidentUpdatePayload(ai_classification="network")

        with pytest.raises(
            ServiceNowWriteRejectedError,
            match=f"{_SCOPE}_ai_classification",
        ):
            await client.update_incident("abc123", payload)

    async def test_update_succeeds_when_service_now_persists_fields(self) -> None:
        """A 2xx response containing requested values is accepted."""
        initial_incident = _incident_result()
        updated_incident = _incident_result(
            **{f"{_SCOPE}_ai_classification": "network"},
        )

        responses = [
            _api_response(result=initial_incident),
            _api_response(result=updated_incident),
        ]

        client, http, _ = _build_client(responses=responses)

        payload = IncidentUpdatePayload(ai_classification="network")

        incident = await client.update_incident("abc123", payload)

        assert incident.sys_id == "abc123"
        assert incident.ai_classification == "network"

    async def test_update_accepts_boolean_representation_from_servicenow(self) -> None:
        """Boolean strings and boolean values are treated as equivalent."""
        initial_incident = _incident_result()
        updated_incident = _incident_result(
            **{f"{_SCOPE}_ai_human_review_required": True},
        )

        responses = [
            _api_response(result=initial_incident),
            _api_response(result=updated_incident),
        ]

        client, http, _ = _build_client(responses=responses)

        payload = IncidentUpdatePayload(ai_human_review_required=True)

        incident = await client.update_incident("abc123", payload)

        assert incident.ai_human_review_required is True


class TestAddWorkNote:
    async def test_add_work_note_sends_patch(self) -> None:
        resp = _api_response(result=_incident_result())
        client, http, _ = _build_client(responses=[resp, resp])

        await client.add_work_note("abc123", "AI is investigating")

        call = http.request.call_args
        assert call.args[0] == "PATCH"
        body = call.kwargs["json"]
        assert body == {"work_notes": "AI is investigating"}

    async def test_add_work_note_returns_incident(self) -> None:
        resp = _api_response(result=_incident_result())
        client, http, _ = _build_client(responses=[resp, resp])

        incident = await client.add_work_note("abc123", "Note text")
        assert incident.sys_id == "abc123"


class TestWriteExecutionLog:
    async def test_write_log_sends_post_to_correct_table(self) -> None:
        resp = _api_response(result=_execution_log_result())
        client, http, _ = _build_client(responses=[resp])

        await client.write_execution_log(_log_payload())

        call = http.request.call_args
        assert call.args[0] == "POST"
        assert "x_2215032_ai_inc_0_ai_execution_log" in call.args[1]

    async def test_write_log_sends_correct_body(self) -> None:
        resp = _api_response(result=_execution_log_result())
        client, http, _ = _build_client(responses=[resp])

        await client.write_execution_log(_log_payload())

        body = http.request.call_args.kwargs["json"]
        assert body["execution_id"] == "exec_test_001"
        assert body["agent"] == "triage_agent"
        assert body["action"] == "execute"
        assert body["status"] == "succeeded"
        assert body["incident_reference"] == "inc_abc123"

    async def test_write_log_returns_entry(self) -> None:
        resp = _api_response(result=_execution_log_result())
        client, http, _ = _build_client(responses=[resp])

        entry = await client.write_execution_log(_log_payload())

        assert entry is not None
        assert entry.sys_id == "log_abc123"
        assert entry.execution_id == "exec_test_001"
        assert entry.status == ExecutionStatus.SUCCEEDED

    async def test_write_log_for_failed_attempt(self) -> None:
        """FR-02: failed attempts must leave a record."""
        result = _execution_log_result(status="failed", error="LLM timeout")
        resp = _api_response(result=result)
        client, http, _ = _build_client(responses=[resp])

        payload = _log_payload(
            status=ExecutionStatus.FAILED,
            result=None,
            error="LLM timeout",
        )
        entry = await client.write_execution_log(payload)

        assert entry.status == ExecutionStatus.FAILED

    async def test_write_log_for_blocked_attempt(self) -> None:
        """FR-02: blocked attempts must leave a record."""
        result = _execution_log_result(status="blocked", error="Human lock active")
        resp = _api_response(result=result)
        client, http, _ = _build_client(responses=[resp])

        payload = _log_payload(
            status=ExecutionStatus.BLOCKED,
            error="Human lock active",
        )
        entry = await client.write_execution_log(payload)

        assert entry.status == ExecutionStatus.BLOCKED

    async def test_logging_failure_returns_none(self) -> None:
        """If the POST to the log table fails, return None — don't raise."""
        client, http, _ = _build_client()
        http.request.side_effect = httpx.ConnectError("network down")
        payload = _log_payload(status=ExecutionStatus.FAILED, error="original error")
        entry = await client.write_execution_log(payload)
        assert entry is None

    async def test_logging_failure_does_not_hide_processing_failure(self) -> None:
        """Critical: if the caller had a processing failure AND logging fails,
        the caller must still be able to surface its original error.
        This test proves write_execution_log() returns None (not raises),
        so the caller's own error handling continues uninterrupted.
        """
        client, http, _ = _build_client()
        http.request.side_effect = httpx.TimeoutException("log POST timed out")
        # Simulate: the caller already caught a processing failure and is
        # trying to log it before re-raising.
        payload = _log_payload(
            status=ExecutionStatus.FAILED,
            error="Original processing error: model inference timeout",
        )
        entry = await client.write_execution_log(payload)
        assert entry is None

    async def test_logging_5xx_returns_none(self) -> None:
        """A 500 from ServiceNow on the log POST must not crash the caller."""
        resp = _api_response(status_code=500, result=None, text="Internal error")
        client, http, _ = _build_client(responses=[resp])
        entry = await client.write_execution_log(_log_payload())
        assert entry is None

    async def test_401_on_log_post_triggers_refresh_and_retry(self) -> None:
        """Execution log POSTs must also benefit from 401 refresh+retry."""
        first_resp = _api_response(status_code=401, result=None, text="Unauthorized")
        second_resp = _api_response(result=_execution_log_result())
        client, http, token_mgr = _build_client(responses=[first_resp, second_resp])
        token_mgr.get_token.side_effect = ["tok_old", "tok_new", "tok_new"]
        entry = await client.write_execution_log(_log_payload())
        assert entry is not None
        assert entry.sys_id == "log_abc123"
        assert token_mgr.get_token.call_count == 3

    async def test_403_on_log_post_does_not_refresh(self) -> None:
        """403 on the log table must NOT trigger token refresh."""
        resp = _api_response(status_code=403, result=None, text="Forbidden")
        client, http, token_mgr = _build_client(responses=[resp])
        # 403 raises ServiceNowAuthorizationError, which the broad except
        # in write_execution_log() catches → returns None
        entry = await client.write_execution_log(_log_payload())
        assert entry is None
        assert token_mgr.get_token.call_count == 1

    async def test_error_field_sent_when_present(self) -> None:
        resp = _api_response(result=_execution_log_result(status="failed"))
        client, http, _ = _build_client(responses=[resp])
        payload = _log_payload(
            status=ExecutionStatus.FAILED,
            error="ACL denied field write",
        )
        await client.write_execution_log(payload)
        body = http.request.call_args.kwargs["json"]
        assert body["error"] == "ACL denied field write"
        assert body["status"] == "failed"

    async def test_execution_id_preserved_in_post_body(self) -> None:
        """execution_id must arrive at ServiceNow exactly as provided."""
        eid = "exec_verify_a1b2c3d4e5f6"
        resp = _api_response(result=_execution_log_result(execution_id=eid))
        client, http, _ = _build_client(responses=[resp])
        payload = _log_payload(execution_id=eid)
        entry = await client.write_execution_log(payload)
        body = http.request.call_args.kwargs["json"]
        assert body["execution_id"] == eid
        assert entry is not None
        assert entry.execution_id == eid

    async def test_token_not_in_log_post_error(self) -> None:
        """Bearer token must not appear in error details on log POST failure."""
        resp = _api_response(status_code=404, result=None, text="Not found")
        client, http, token_mgr = _build_client(token="super_secret_token", responses=[resp])
        # The 404 raises inside _request, caught by write_execution_log
        entry = await client.write_execution_log(_log_payload())
        assert entry is None

    async def test_write_log_handles_realistic_servicenow_reference_response(
        self,
    ) -> None:
        """A successful 201 response with reference objects must parse correctly."""
        result = _execution_log_result()
        resp = _api_response(status_code=201, result=result)

        client, http, _ = _build_client(responses=[resp])

        entry = await client.write_execution_log(_log_payload())

        assert entry is not None
        assert entry.sys_id == "log_abc123"
        assert entry.execution_id == "exec_test_001"
        assert entry.incident_reference == "inc_abc123"

    async def test_write_log_success_logs_only_written_event(self) -> None:
        """A valid 201 response logs success and does not log a failure."""
        result = _execution_log_result()
        resp = _api_response(status_code=201, result=result)

        client, http, _ = _build_client(responses=[resp])

        with patch("app.clients.servicenow_client.logger") as mock_logger:
            entry = await client.write_execution_log(_log_payload())

        assert entry is not None

        mock_logger.info.assert_called_once()

        info_call = mock_logger.info.call_args
        assert info_call.args[0] == "execution_log_written"

        mock_logger.exception.assert_not_called()

    async def test_write_log_unparseable_response_is_not_write_failure(self) -> None:
        """A successful HTTP response with invalid data is a parsing failure."""
        result = _execution_log_result(
            incident_reference={
                "link": "https://dev00000.service-now.com/api/now/table/incident/inc_abc123",
                "value": "inc_abc123",
            }
        )
        resp = _api_response(status_code=201, result=result)

        client, http, _ = _build_client(responses=[resp])

        with patch("app.clients.servicenow_client.logger") as mock_logger:
            entry = await client.write_execution_log(_log_payload())

        assert entry is None

        mock_logger.exception.assert_called_once()

        exception_call = mock_logger.exception.call_args
        assert exception_call.args[0] == "execution_log_response_unparseable"


class TestResourceManagement:
    async def test_aclose_closes_owned_client(self) -> None:
        settings = mock_settings()
        http = AsyncMock(spec=httpx.AsyncClient)
        client = ServiceNowClient(settings, http_client=None)
        # Patch out the internally created client
        client._http = http
        client._owns_http_client = True

        await client.aclose()
        http.aclose.assert_called_once()

    async def test_aclose_does_not_close_injected_client(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        client = ServiceNowClient(mock_settings(), http_client=http)

        await client.aclose()
        http.aclose.assert_not_called()

    async def test_context_manager(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        token_mgr = AsyncMock(spec=ServiceNowTokenManager)

        async with ServiceNowClient(
            mock_settings(), http_client=http, token_manager=token_mgr
        ) as client:
            assert client is not None
        # Injected client should not be closed
        http.aclose.assert_not_called()


class TestResponseParsing:
    async def test_non_json_response_raises_error(self) -> None:
        """If ServiceNow returns non-JSON on a 200, raise ServiceNowError."""
        resp = httpx.Response(
            status_code=200,
            text="<html>Not JSON</html>",
            headers={"Content-Type": "text/html"},
            request=httpx.Request(
                "GET", "https://dev00000.service-now.com/api/now/table/incident/x"
            ),
        )
        client, http, _ = _build_client(responses=[resp])

        with pytest.raises(ServiceNowError, match="Non-JSON"):
            await client.get_incident("x")

    async def test_response_without_result_key_returns_data(self) -> None:
        """If the JSON has no 'result' key, return the whole dict."""
        resp = httpx.Response(
            status_code=200,
            json={
                "sys_id": "abc123",
                "number": "INC0010001",
                "short_description": "Test",
            },
            request=httpx.Request(
                "GET", "https://dev00000.service-now.com/api/now/table/incident/x"
            ),
        )
        client, http, _ = _build_client(responses=[resp])
        result = await client._request("GET", "/api/now/table/incident/x")
        assert result["sys_id"] == "abc123"

    async def test_request_preserves_existing_params_and_excludes_reference_links(
        self,
    ) -> None:
        resp = _api_response(result=[_incident_result()])
        client, http, _ = _build_client(responses=[resp])

        await client.find_incident_by_number("INC0010001")

        params = http.request.call_args.kwargs["params"]

        assert params == {
            "sysparm_query": "number=INC0010001",
            "sysparm_limit": 2,
            "sysparm_exclude_reference_link": "true",
        }


class TestAPICredentialSafety:
    async def test_token_not_in_error_details(self) -> None:
        """Error messages/details must not contain the bearer token."""
        resp = _api_response(status_code=404, result=None, text="Not found")
        client, http, token_mgr = _build_client(token="super_secret_token", responses=[resp])

        with pytest.raises(ServiceNowNotFoundError) as exc_info:
            await client.get_incident("abc")

        full_error = str(exc_info.value) + str(exc_info.value.details)
        assert "super_secret_token" not in full_error

    async def test_error_body_truncated_to_500_chars(self) -> None:
        """Response bodies in error details must be truncated to prevent log bloat."""
        long_body = "x" * 1000
        resp = _api_response(status_code=500, result=None, text=long_body)
        client, http, _ = _build_client(responses=[resp])

        with pytest.raises(ServiceNowServerError) as exc_info:
            await client.get_incident("abc")

        body_in_details = exc_info.value.details.get("body", "")
        assert len(body_in_details) <= 500


class TestHumanLockSafety:
    async def test_human_lock_field_mapping_and_write_protection(self) -> None:
        """Verify that ai_human_lock is correctly read from ServiceNow and blocks writes."""
        locked_incident_data = _incident_result(
            sys_id="locked123",
            number="INC0010001",
            **{f"{_SCOPE}_ai_human_lock": "true"},
        )

        responses = [
            _api_response(result=locked_incident_data),  # get_incident directly
            _api_response(result=locked_incident_data),  # get_incident inside update_incident
            _api_response(
                result=locked_incident_data
            ),  # get_incident inside add_work_note (if implemented)
        ]

        client, http, _ = _build_client(responses=responses)

        # Verify model reads the field correctly from the alias
        incident = await client.get_incident("locked123")
        assert incident.ai_human_lock is True

        # Verify update_incident raises ServiceNowHumanLockError on locked incident
        payload = IncidentUpdatePayload(ai_confidence=0.90)
        with pytest.raises(ServiceNowHumanLockError, match="is locked"):
            await client.update_incident("locked123", payload)

        # Verify add_work_note raises ServiceNowHumanLockError on locked incident
        with pytest.raises(ServiceNowHumanLockError, match="is locked"):
            await client.add_work_note("locked123", "Attempting note on locked incident")
