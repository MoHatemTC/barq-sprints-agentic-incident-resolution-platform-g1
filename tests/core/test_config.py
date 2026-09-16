"""Tests for retrieval configuration independence (RetrievalSettings)."""

from app.core.config import get_retrieval_settings


def test_retrieval_settings_independent_of_servicenow() -> None:
    settings = get_retrieval_settings()
    assert settings.qdrant_url
    assert settings.dense_embedding_model == "BAAI/bge-small-en-v1.5"
    assert settings.sparse_embedding_model == "Qdrant/bm25"
    assert settings.qdrant_collection_name == "incident_knowledge_base"


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
