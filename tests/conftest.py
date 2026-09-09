"""Shared pytest configuration.

`app.core.config.Settings` declares the ServiceNow connection fields as
required on purpose: the service must fail fast at startup rather than boot
with a half-configured integration. That means importing anything under
`app` needs those variables present.

Tests must not depend on a developer's local `.env`, and CI runners have none,
so the required variables are injected here with values that are obviously not
real credentials. `get_settings` is cached, so this runs before the first
import that would build a Settings instance.
"""

import os

import pytest

_REQUIRED_TEST_ENV = {
    "SERVICENOW_INSTANCE_URL": "https://dev00000.service-now.com",
    "SERVICENOW_CLIENT_ID": "test-client-id",
    "SERVICENOW_CLIENT_SECRET": "test-client-secret",
    "SERVICENOW_USERNAME": "test_service_account",
    "SERVICENOW_PASSWORD": "test-password",
}

for _key, _value in _REQUIRED_TEST_ENV.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture
def settings():
    """Settings built from the test environment above."""
    from app.core.config import get_settings

    return get_settings()
