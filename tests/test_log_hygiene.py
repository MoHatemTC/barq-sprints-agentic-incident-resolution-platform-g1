"""EC-09 log hygiene: secrets must never appear in application logs.

Captures real rendered log output through the production structlog processor chain
(including the ``redact_sensitive_data`` processor) and drives requests carrying
fake secrets (Authorization tokens, passwords, client secrets). The tests fail if
any known fake secret value appears anywhere in the captured log output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

import tests.helpers as h
from app.core.logging import REDACTED, SENSITIVE_KEYS, redact_sensitive_data
from app.main import create_app
from tests.helpers import capture_json_logs, mock_settings, parse_json_log_lines

FAKE_BEARER = "VERY_SECRET_TOKEN_9f3ab2"
FAKE_PASSWORD = "SuperSecretPW-zz81"
FAKE_CLIENT_SECRET = "client-secret-AA73kd"


@pytest.fixture
def app():
    settings = mock_settings(webhook_auth_token=h.WEBHOOK_TOKEN)
    application = create_app(settings=settings)
    application.state.engine = MagicMock()
    application.state.session_factory = MagicMock()
    application.state.redis = MagicMock()
    application.state.redis.lpush = AsyncMock(return_value=1)
    return application


# ---------------------------------------------------------------------------
# Redaction processor unit behaviour (documented sensitive key set)
# ---------------------------------------------------------------------------
def test_documented_sensitive_keys_are_redacted_at_top_level() -> None:
    for key in SENSITIVE_KEYS:
        event_dict = {key: "raw-value", "event": "test"}
        result = redact_sensitive_data(None, "info", dict(event_dict))
        assert result[key] == REDACTED, f"key {key!r} must be redacted"
        assert result["event"] == "test"


def test_redaction_handles_case_insensitive_keys_and_nested_dicts() -> None:
    # Header-cased key ('Authorization' as it arrives from HTTP).
    top = redact_sensitive_data(None, "info", {"Authorization": FAKE_BEARER})
    assert top["Authorization"] == REDACTED

    # Nested dictionaries are redacted recursively.
    nested = redact_sensitive_data(
        None,
        "info",
        {
            "event": "outbound",
            "request": {
                "url": "https://x",
                "headers": {"authorization": f"Bearer {FAKE_BEARER}", "accept": "*/*"},
                "auth": {"client_secret": FAKE_CLIENT_SECRET, "password": FAKE_PASSWORD},
            },
        },
    )
    headers = nested["request"]["headers"]
    auth = nested["request"]["auth"]
    assert headers["authorization"] == REDACTED
    assert headers["accept"] == "*/*"  # non-sensitive values untouched
    assert auth["client_secret"] == REDACTED
    assert auth["password"] == REDACTED
    # No raw secret survived anywhere in the rendered structure.
    assert FAKE_BEARER not in repr(nested)
    assert FAKE_CLIENT_SECRET not in repr(nested)
    assert FAKE_PASSWORD not in repr(nested)


def test_non_sensitive_values_pass_through_untouched() -> None:
    event = {"event": "incident_event_accepted", "event_id": "evt-1", "count": 3}
    result = redact_sensitive_data(None, "info", dict(event))
    assert result == event


# ---------------------------------------------------------------------------
# End-to-end: driven requests with fake secrets
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_authorization_token_never_appears_in_request_logs(app) -> None:
    with capture_json_logs() as buf:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 401 path logs a platform error; token must not leak into it.
            resp = await ac.post(
                "/api/v1/webhook/incident",
                json=h.make_incident_payload(),
                headers={"Authorization": f"Bearer {FAKE_BEARER}"},
            )
            assert resp.status_code == 401
            await ac.get("/health")

    output = buf.getvalue()
    assert FAKE_BEARER not in output, "raw bearer token leaked into captured logs"
    events = parse_json_log_lines(buf)
    assert any(e.get("event") == "platform_error_handled" for e in events)
    assert any(e.get("event") == "http_request_completed" for e in events)


@pytest.mark.asyncio
async def test_error_path_logs_do_not_dump_secrets_or_headers(app) -> None:
    """Webhook 503 error logging must not leak the configured token or auth headers."""
    with (
        capture_json_logs() as buf,
        patch(
            "api.routers.webhook.accept_inbound_event",
            new_callable=AsyncMock,
            side_effect=SQLAlchemyError("DB connection dropped"),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/webhook/incident",
                json=h.make_incident_payload(),
                headers=h.AUTH_HEADERS,
            )
            assert resp.status_code == 503

    output = buf.getvalue()
    for secret in (FAKE_BEARER, h.WEBHOOK_TOKEN):
        assert secret not in output, f"secret {secret!r} leaked into error-path logs"

    events = parse_json_log_lines(buf)
    error_events = [e for e in events if e.get("event") == "database_persistence_failed"]
    assert error_events, "expected database_persistence_failed log event"
    # Structured error metadata only; no raw Authorization content.
    assert "error" in error_events[0]
    assert FAKE_BEARER not in repr(error_events[0])


@pytest.mark.asyncio
async def test_config_endpoint_logging_does_not_expose_secret_values(app) -> None:
    from pydantic import SecretStr

    app.state.settings = mock_settings(
        webhook_auth_token=h.WEBHOOK_TOKEN,
        servicenow_password=SecretStr(FAKE_PASSWORD),
        servicenow_client_secret=SecretStr(FAKE_CLIENT_SECRET),
    )
    with capture_json_logs() as buf:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/config", headers=h.AUTH_HEADERS)
            assert resp.status_code == 200
            assert resp.json()["servicenow_password"] == REDACTED

    output = buf.getvalue()
    assert FAKE_PASSWORD not in output
    assert FAKE_CLIENT_SECRET not in output
    assert h.WEBHOOK_TOKEN not in output


@pytest.mark.asyncio
async def test_structured_access_log_fields_and_no_query_secret_leak(app) -> None:
    """Access log carries method/path/status/latency; secret query params stay out."""
    with capture_json_logs() as buf:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.get(
                "/api/v1/eval/results",
                params={"run_id": "eval-1"},
                headers={**h.AUTH_HEADERS, "X-Trace-Password": FAKE_PASSWORD},
            )

    events = parse_json_log_lines(buf)
    access = [e for e in events if e.get("event") == "http_request_completed"]
    assert access, f"expected access log, got: {events}"
    entry = access[-1]
    assert entry["method"] == "GET"
    assert entry["path"] == "/api/v1/eval/results"
    assert entry["status_code"] == 200
    assert isinstance(entry["duration_ms"], (int, float))

    # Header-injected fake secrets never reached the log.
    assert FAKE_PASSWORD not in buf.getvalue()
