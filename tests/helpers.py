from __future__ import annotations

import contextlib
import importlib
import logging
from io import StringIO
from typing import Any
from unittest.mock import MagicMock

import structlog
from sqlalchemy.engine import URL

from app.core.config import Settings
from app.core.logging import redact_sensitive_data


def mock_settings(**overrides: object) -> Settings:
    defaults = {
        "servicenow_instance_url": "https://dev00000.service-now.com",
        "servicenow_client_id": "test-cid",
        "servicenow_client_secret": "test-secret",
        "servicenow_username": "svc_user",
        "servicenow_password": "svc_pass",
        "servicenow_timeout_seconds": 5,
        "servicenow_token_expiry_buffer_seconds": 30,
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "test_db",
        "postgres_user": "test_user",
        "postgres_password": "test_password",
        "redis_host": "localhost",
        "redis_port": 6379,
        "redis_password": "test_redis_password",
        "webhook_auth_token": "operator-api-token-for-tests",
        "webhook_oauth_client_id": "barq-servicenow-test",
        "webhook_oauth_client_secret": "client-secret-for-tests",
        "webhook_oauth_signing_key": "test-signing-key-that-is-at-least-32-characters",
        "langfuse_public_key": None,
        "langfuse_secret_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def make_oauth_token(settings: Settings | None = None) -> str:
    """Issue a valid short-lived ServiceNow webhook token (audience barq-webhook)."""
    from app.auth.webhook_oauth import config_from_settings, issue_access_token

    resolved = settings or mock_settings()
    config = config_from_settings(resolved)
    return issue_access_token(config, config.client_id, config.client_secret)["access_token"]


def make_operator_token(settings: Settings | None = None, *, roles: list[str] | None = None) -> str:
    """Issue a valid operator token (audience barq-operator).

    ``roles`` re-mints the token with a different ``roles`` claim, which is how
    the tests prove a missing role comes back 403 rather than trusting a
    request header (#148).
    """
    from dataclasses import replace

    from app.auth.webhook_oauth import config_from_settings, issue_access_token

    resolved = settings or mock_settings()
    config = config_from_settings(resolved)
    if roles is not None:
        config = replace(config, operator_roles=tuple(roles))
    return issue_access_token(config, config.operator_client_id, config.operator_client_secret)[
        "access_token"
    ]


WEBHOOK_TOKEN = make_oauth_token()
OPERATOR_TOKEN = make_operator_token()
#: Headers for operator-facing routes. AUTH_HEADERS carries the operator token;
#: the webhook has its own (``webhook_oauth_headers``).
AUTH_HEADERS = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
#: Kept for the tests that still send the header: since #148 it is ignored, and
#: the role is read from the token instead.
OPERATOR_HEADERS = {**AUTH_HEADERS, "X-User-Role": "operator"}


def webhook_oauth_headers(settings: Settings | None = None) -> dict[str, str]:
    """Issue a valid short-lived ServiceNow webhook token for HTTP-boundary tests."""
    return {"Authorization": f"Bearer {make_oauth_token(settings)}"}


VALID_SYS_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
VALID_NUMBER = "INC0014231"


def make_incident_payload(**overrides: Any) -> dict[str, Any]:
    """Return a valid Outbound Event Contract v1 payload with optional field overrides."""
    payload: dict[str, Any] = {
        "event_id": "0b78b3f6-9a51-4f2f-8f10-3b0f9bc82f1a",
        "sys_id": VALID_SYS_ID,
        "number": VALID_NUMBER,
        "event_type": "incident.created",
        "contract_version": "v1",
    }
    payload.update(overrides)
    return payload


def build_session_factory(session: Any) -> Any:
    """Wrap a fake AsyncSession in the async-context-manager shape get_db_session expects."""

    class _SessionContext:
        async def __aenter__(self) -> Any:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    factory = MagicMock(return_value=_SessionContext())
    return factory


def build_database_url_for_db(settings: Settings, database_name: str) -> Any:
    """Build a postgresql+asyncpg URL for an arbitrary database name from settings."""

    password = (
        settings.postgres_password.get_secret_value()
        if settings.postgres_password is not None
        else None
    )
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.postgres_user,
        password=password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database_name,
    )


# ---------------------------------------------------------------------------
# Structured log capture wired through the real production processor chain
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def capture_json_logs(level: str = "INFO"):
    """Capture rendered structlog output through the production processor chain.

    Reconfigures structlog with the same processors ``configure_logging`` installs for
    the production environment (contextvars merge, log level, timestamp, secret
    redaction, exc info, JSON rendering) but writes into an in-memory buffer.

    Module-level loggers (``api.access``, ``api.errors``, router loggers, ...) may
    already be cached against the stdout factory from earlier use, so each known
    module logger is rebound to a fresh proxy for the duration of the context and
    restored afterwards.
    """

    buffer = StringIO()
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive_data,
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=buffer),
        cache_logger_on_first_use=False,
    )

    rebound_modules = [
        "app.middlewares.correlation",
        "app.exceptions.handlers",
        "api.routers.webhook",
        "api.routers.executions",
        "api.routers.approvals",
        "api.routers.dlq",
        "api.routers.eval",
        "api.routers.config",
    ]
    originals: list[tuple[Any, str, Any]] = []
    for module_name in rebound_modules:
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # pragma: no cover - module always exists in this repo
            continue
        logger_attr = getattr(module, "logger", None)
        if logger_attr is None:
            continue
        fresh = structlog.getLogger(module_name)
        originals.append((module, "logger", logger_attr))
        module.logger = fresh
    try:
        yield buffer
    finally:
        for module, attr, value in originals:
            setattr(module, attr, value)
        structlog.reset_defaults()


def parse_json_log_lines(buffer: StringIO) -> list[dict[str, Any]]:
    """Parse each non-empty line of a captured buffer as a JSON log event."""
    import json

    events: list[dict[str, Any]] = []
    for line in buffer.getvalue().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"_unparseable": line})
    return events
