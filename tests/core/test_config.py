"""Tests for retrieval configuration independence (RetrievalSettings)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_retrieval_settings


def test_retrieval_settings_independent_of_servicenow() -> None:
    settings = get_retrieval_settings()
    assert settings.qdrant_url
    assert settings.dense_embedding_model == "BAAI/bge-small-en-v1.5"
    assert settings.sparse_embedding_model == "Qdrant/bm25"
    assert settings.qdrant_collection_name == "incident_knowledge_base"


SENTINEL_PASSWORD = "SENTINEL-PASSWORD-123"


def test_missing_field_error_hides_sibling_password(monkeypatch):
    monkeypatch.setenv("SERVICENOW_INSTANCE_URL", "https://dev00000.service-now.com")
    monkeypatch.setenv("SERVICENOW_CLIENT_ID", "cid")
    monkeypatch.setenv("SERVICENOW_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SERVICENOW_PASSWORD", SENTINEL_PASSWORD)
    monkeypatch.delenv("SERVICENOW_USERNAME", raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # rely only on the env vars set above

    assert SENTINEL_PASSWORD not in str(excinfo.value)
    assert SENTINEL_PASSWORD not in repr(excinfo.value)
