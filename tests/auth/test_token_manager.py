from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.auth.token_manager import ServiceNowTokenManager
from app.exceptions.servicenow import (
    ServiceNowAuthenticationError,
    ServiceNowConnectionError,
    ServiceNowTimeoutError,
)
from tests.helpers import mock_settings


def _token_json(
    access_token: str = "tok_aaa",
    expires_in: int = 1800,
    refresh_token: str | None = "rt_aaa",
) -> dict:
    d: dict = {"access_token": access_token, "expires_in": expires_in}
    if refresh_token is not None:
        d["refresh_token"] = refresh_token
    return d


def _mock_response(status_code: int = 200, json_body: dict | None = None) -> httpx.Response:
    resp = httpx.Response(
        status_code=status_code,
        json=json_body or _token_json(),
        request=httpx.Request("POST", "https://dev00000.service-now.com/oauth_token.do"),
    )
    return resp


class TestTokenAcquisition:
    """Password-grant flow (first fetch)."""

    async def test_fetches_token_on_first_call(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(json_body=_token_json())
        settings = mock_settings()
        mgr = ServiceNowTokenManager(settings, http)

        token = await mgr.get_token()

        assert token == "tok_aaa"
        http.post.assert_called_once()
        call_kwargs = http.post.call_args
        assert "password" in call_kwargs.kwargs.get(
            "data", call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
        ).get("grant_type", "")  # noqa: E501

    async def test_returns_cached_token_while_valid(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response()
        mgr = ServiceNowTokenManager(mock_settings(), http)

        t1 = await mgr.get_token()
        t2 = await mgr.get_token()

        assert t1 == t2
        assert http.post.call_count == 1  # only one HTTP call

    async def test_sends_correct_form_data(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response()
        settings = mock_settings()
        mgr = ServiceNowTokenManager(settings, http)

        await mgr.get_token()

        _, kwargs = http.post.call_args
        form = kwargs["data"]
        assert form["grant_type"] == "password"
        assert form["client_id"] == "test-cid"
        assert form["client_secret"] == "test-secret"
        assert form["username"] == "svc_user"
        assert form["password"] == "svc_pass"

    async def test_credentials_not_in_exception_message(self) -> None:
        """FR-06: secrets must not leak through error messages."""
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(status_code=401, json_body={})
        mgr = ServiceNowTokenManager(mock_settings(), http)

        with pytest.raises(ServiceNowAuthenticationError) as exc_info:
            await mgr.get_token()

        msg = str(exc_info.value)
        assert "test-secret" not in msg
        assert "svc_pass" not in msg
        assert "test-cid" not in msg


class TestTokenRefresh:
    async def test_uses_refresh_token_when_available(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        # First call: password grant → stores refresh_token
        http.post.return_value = _mock_response(json_body=_token_json(refresh_token="rt_first"))
        mgr = ServiceNowTokenManager(mock_settings(), http)
        await mgr.get_token()

        # Simulate expiry
        mgr._expires_at = datetime.now(UTC) - timedelta(seconds=60)

        # Second call: should use refresh_token grant
        http.post.return_value = _mock_response(
            json_body=_token_json(access_token="tok_refreshed", refresh_token="rt_second")
        )
        token = await mgr.get_token()

        assert token == "tok_refreshed"
        second_call_data = http.post.call_args.kwargs["data"]
        assert second_call_data["grant_type"] == "refresh_token"
        assert second_call_data["refresh_token"] == "rt_first"

    async def test_force_refresh_triggers_new_fetch(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(json_body=_token_json(refresh_token="rt_x"))
        mgr = ServiceNowTokenManager(mock_settings(), http)
        await mgr.get_token()

        http.post.return_value = _mock_response(json_body=_token_json(access_token="tok_forced"))
        token = await mgr.get_token(force_refresh=True)

        assert token == "tok_forced"
        assert http.post.call_count == 2

    async def test_refresh_failure_clears_state_and_raises(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(json_body=_token_json(refresh_token="rt_x"))
        mgr = ServiceNowTokenManager(mock_settings(), http)
        await mgr.get_token()

        # Expire the token
        mgr._expires_at = datetime.now(UTC) - timedelta(seconds=60)

        # Refresh fails with 401
        http.post.return_value = _mock_response(status_code=401, json_body={})

        with pytest.raises(ServiceNowAuthenticationError):
            await mgr.get_token()

        assert mgr._token is None
        assert mgr._refresh_token is None
        assert mgr._expires_at is None

    async def test_failed_token_prevents_duplicate_refresh(self) -> None:
        """When multiple coroutines race, only one refresh should happen."""
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(
            json_body=_token_json(access_token="tok_initial", refresh_token="rt_init")
        )
        mgr = ServiceNowTokenManager(mock_settings(), http)
        old_token = await mgr.get_token()

        # Simulate: another coroutine already refreshed
        mgr._token = "tok_already_refreshed"
        mgr._expires_at = datetime.now(UTC) + timedelta(hours=1)

        # This call with failed_token=old should see the new token and skip refresh
        result = await mgr.get_token(force_refresh=True, failed_token=old_token)
        assert result == "tok_already_refreshed"
        # Only the initial fetch, no extra refresh call
        assert http.post.call_count == 1


class TestTokenExpiryBuffer:
    async def test_token_within_buffer_triggers_refresh(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(json_body=_token_json(refresh_token="rt_buf"))
        settings = mock_settings(servicenow_token_expiry_buffer_seconds=60)
        mgr = ServiceNowTokenManager(settings, http)
        await mgr.get_token()

        # Set expires_at to 30 seconds from now (within 60s buffer)
        mgr._expires_at = datetime.now(UTC) + timedelta(seconds=30)

        http.post.return_value = _mock_response(json_body=_token_json(access_token="tok_rebuf"))
        token = await mgr.get_token()
        assert token == "tok_rebuf"
        assert http.post.call_count == 2

    async def test_token_outside_buffer_is_still_valid(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(json_body=_token_json())
        settings = mock_settings(servicenow_token_expiry_buffer_seconds=30)
        mgr = ServiceNowTokenManager(settings, http)
        await mgr.get_token()

        # Set expires_at to 120 seconds from now (well outside 30s buffer)
        mgr._expires_at = datetime.now(UTC) + timedelta(seconds=120)

        await mgr.get_token()
        assert http.post.call_count == 1  # no refresh needed


class TestInvalidate:
    async def test_invalidate_clears_token_and_expiry(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(json_body=_token_json(refresh_token="rt_inv"))
        mgr = ServiceNowTokenManager(mock_settings(), http)
        await mgr.get_token()

        mgr.invalidate()

        assert mgr._token is None
        assert mgr._expires_at is None
        # refresh_token is preserved so the next get_token can use it
        assert mgr._refresh_token == "rt_inv"


class TestTokenNetworkErrors:
    async def test_timeout_raises_servicenow_timeout_error(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.side_effect = httpx.TimeoutException("timed out")
        mgr = ServiceNowTokenManager(mock_settings(), http)

        with pytest.raises(ServiceNowTimeoutError, match="Timed out"):
            await mgr.get_token()

    async def test_connection_error_raises_servicenow_connection_error(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.side_effect = httpx.ConnectError("DNS failure")
        mgr = ServiceNowTokenManager(mock_settings(), http)

        with pytest.raises(ServiceNowConnectionError, match="connect"):
            await mgr.get_token()

    async def test_non_200_raises_auth_error_with_status(self) -> None:
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response(status_code=400, json_body={})
        mgr = ServiceNowTokenManager(mock_settings(), http)

        with pytest.raises(ServiceNowAuthenticationError) as exc_info:
            await mgr.get_token()
        assert exc_info.value.status_code == 400


class TestCredentialSafety:
    async def test_credentials_never_in_log_output(self) -> None:
        """Verify that log calls don't contain secrets."""
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post.return_value = _mock_response()
        mgr = ServiceNowTokenManager(mock_settings(), http)

        with patch("app.auth.token_manager.logger") as mock_logger:
            await mgr.get_token()
            for call in mock_logger.method_calls:
                call_str = str(call)
                assert "test-secret" not in call_str
                assert "svc_pass" not in call_str

    async def test_error_log_excludes_response_body(self) -> None:
        """On auth failure, the log entry must not contain the response body."""
        http = AsyncMock(spec=httpx.AsyncClient)
        resp = httpx.Response(
            status_code=401,
            text="invalid_grant: bad credentials for svc_pass",
            request=httpx.Request("POST", "https://dev00000.service-now.com/oauth_token.do"),
        )
        http.post.return_value = resp
        mgr = ServiceNowTokenManager(mock_settings(), http)

        with patch("app.auth.token_manager.logger") as mock_logger:
            with pytest.raises(ServiceNowAuthenticationError):
                await mgr.get_token()

            for call in mock_logger.method_calls:
                call_str = str(call)
                assert "svc_pass" not in call_str
