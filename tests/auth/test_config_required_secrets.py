"""Every API and webhook auth secret is required, with no fallback value (#51, #136).

`tests/auth/test_config_required_secrets.py` is the file `audit-and-identity.md`
points at for the claim that tracked configuration carries no hardcoded
credential: drop one of these variables and Settings refuses to build, in
production and in tests alike.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED_SECRETS = (
    "WEBHOOK_AUTH_TOKEN",
    "WEBHOOK_OAUTH_CLIENT_ID",
    "WEBHOOK_OAUTH_CLIENT_SECRET",
    "WEBHOOK_OAUTH_SIGNING_KEY",
)


def _bare_settings(**overrides: object) -> Settings:
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        servicenow_instance_url="https://dev00000.service-now.com",
        servicenow_client_id="test-client",
        servicenow_client_secret="test-secret",
        servicenow_username="svc",
        servicenow_password="test-password",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="test_db",
        postgres_user="test_user",
        postgres_password="test_password",
        redis_host="localhost",
        redis_port=6379,
        redis_password="test_redis_password",
        **overrides,
    )


def test_settings_require_all_api_and_webhook_auth_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in REQUIRED_SECRETS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as raised:
        _bare_settings()

    missing = {str(error["loc"][0]) for error in raised.value.errors()}
    assert set(REQUIRED_SECRETS) == {
        name.upper() for name in missing if name.startswith("webhook_")
    }


@pytest.mark.parametrize("secret", REQUIRED_SECRETS)
def test_each_auth_secret_is_required_on_its_own(
    secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One missing variable is enough to refuse to start, even with the rest set."""
    for name in REQUIRED_SECRETS:
        monkeypatch.delenv(name, raising=False)

    present = {name.lower(): "x" * 40 for name in REQUIRED_SECRETS if name != secret}
    with pytest.raises(ValidationError) as raised:
        _bare_settings(**present)

    missing = {str(error["loc"][0]) for error in raised.value.errors()}
    assert secret.lower() in missing
