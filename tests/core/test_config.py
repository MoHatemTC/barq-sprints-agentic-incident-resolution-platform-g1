"""Tests for retrieval configuration independence (RetrievalSettings)."""

import pytest
from pydantic import ValidationError

from app.core.config import RetrievalSettings, Settings, get_retrieval_settings
from tests.clients.test_servicenow_integration import _load_live_test_settings

_SERVICENOW_LIVE_CONFIG_ENV = (
    "SERVICENOW_INSTANCE_URL",
    "SERVICENOW_CLIENT_ID",
    "SERVICENOW_CLIENT_SECRET",
    "SERVICENOW_USERNAME",
    "SERVICENOW_PASSWORD",
    "SERVICENOW_TEST_INCIDENT_SYS_ID",
)


def test_retrieval_settings_independent_of_servicenow() -> None:
    settings = get_retrieval_settings()
    assert settings.qdrant_url
    assert settings.dense_embedding_model == "BAAI/bge-small-en-v1.5"
    assert settings.sparse_embedding_model == "Qdrant/bm25"
    assert settings.qdrant_collection_name == "incident_knowledge_base"


def test_retrieval_settings_ignores_local_dotenv(tmp_path, monkeypatch):
    """A hostile .env in the repo root must not leak into unit-test settings."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("QDRANT_COLLECTION_NAME=team_kb_prod\n")

    settings = RetrievalSettings()

    assert settings.qdrant_collection_name == "incident_knowledge_base"


def test_live_settings_with_incomplete_credentials_are_not_ready(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SERVICENOW_LIVE_TESTS", "1")
    for name in _SERVICENOW_LIVE_CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)

    settings, reason = _load_live_test_settings()

    assert settings is None
    assert "missing or invalid" in reason
    assert "SERVICENOW_INSTANCE_URL" in reason
    assert "SERVICENOW_TEST_INCIDENT_SYS_ID" in reason


def test_live_settings_load_dotenv_with_environment_precedence(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SERVICENOW_LIVE_TESTS", "1")
    for name in _SERVICENOW_LIVE_CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "SERVICENOW_INSTANCE_URL=https://example.invalid",
                "SERVICENOW_CLIENT_ID=dotenv-client",
                "SERVICENOW_CLIENT_SECRET=dotenv-secret",
                "SERVICENOW_USERNAME=dotenv-user",
                "SERVICENOW_PASSWORD=dotenv-password",
                "SERVICENOW_TEST_INCIDENT_SYS_ID=dotenv-incident",
            )
        ),
        encoding="utf-8",
    )

    settings, reason = _load_live_test_settings()

    assert reason == ""
    assert settings is not None
    assert settings.servicenow_username == "dotenv-user"
    assert settings.servicenow_test_incident_sys_id == "dotenv-incident"

    monkeypatch.setenv("SERVICENOW_USERNAME", "environment-user")
    overridden_settings, reason = _load_live_test_settings()

    assert reason == ""
    assert overridden_settings is not None
    assert overridden_settings.servicenow_username == "environment-user"


SENTINEL_PASSWORD = "SENTINEL-PASSWORD-123"


def test_missing_field_error_hides_sibling_password(monkeypatch):
    """#64: a pydantic validation error must not print the submitted values.

    Settings is built at import in app/main.py and every ServiceNow field is
    required, so one missing or misspelled variable in .env raised a ValidationError
    whose `input_value` was the whole input dict - with SERVICENOW_PASSWORD at the end
    of it - straight into a terminal or a container log. `hide_input_in_errors` on the
    base class is what suppresses that.
    """
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://dev00000.service-now.com")
    monkeypatch.setenv("SERVICENOW_CLIENT_ID", "cid")
    monkeypatch.setenv("SERVICENOW_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SERVICENOW_PASSWORD", SENTINEL_PASSWORD)
    monkeypatch.delenv("SERVICENOW_USERNAME", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # rely only on the env vars set above

    assert SENTINEL_PASSWORD not in str(excinfo.value)
    assert SENTINEL_PASSWORD not in repr(excinfo.value)


def test_kb_publisher_settings_swap_in_the_publisher_credentials() -> None:
    from app.core.config import kb_publisher_settings
    from tests.helpers import mock_settings

    base = mock_settings(servicenow_kb_username="kb_publisher", servicenow_kb_password="kb-pass")
    publisher = kb_publisher_settings(base)
    assert publisher.servicenow_username == "kb_publisher"
    assert publisher.servicenow_password.get_secret_value() == "kb-pass"
    assert base.servicenow_username == "svc_user", "the original settings must not change"


def test_kb_publisher_settings_fall_back_when_unset() -> None:
    from app.core.config import kb_publisher_settings
    from tests.helpers import mock_settings

    base = mock_settings()
    assert kb_publisher_settings(base) is base


def test_kb_publisher_username_without_password_is_an_error() -> None:
    import pytest

    from app.core.config import kb_publisher_settings
    from tests.helpers import mock_settings

    with pytest.raises(ValueError, match="SERVICENOW_KB_PASSWORD"):
        kb_publisher_settings(mock_settings(servicenow_kb_username="kb_publisher"))
    with pytest.raises(ValueError, match="SERVICENOW_KB_PASSWORD"):
        kb_publisher_settings(
            mock_settings(servicenow_kb_username="kb_publisher", servicenow_kb_password="")
        )
