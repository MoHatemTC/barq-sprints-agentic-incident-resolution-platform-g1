"""Tests for retrieval configuration independence (RetrievalSettings)."""

from app.core.config import RetrievalSettings, get_retrieval_settings


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
