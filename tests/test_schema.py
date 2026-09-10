"""Validation tests for the knowledge domain models and the JSON source."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.knowledge import Article, ArticleChunk, KnowledgePayload
from app.retrieval.sources import LocalJSONSource, summarize_validation_error


def test_valid_article_round_trips(sample_articles: list[Article]) -> None:
    assert [a.unique_key for a in sample_articles] == [
        "KB0001-v2.0",
        "KB0001-v1.0",
        "KB0002-v1.1",
    ]
    assert [a.article_number for a in sample_articles] == [
        "KB0001",
        "KB0001",
        "KB0002",
    ]


def test_article_number_must_match_pattern(article_dicts: list[dict]) -> None:
    data = {**article_dicts[0], "article_number": "INVALID-001"}
    with pytest.raises(ValidationError, match="article_number"):
        Article.model_validate(data)


@pytest.mark.parametrize("bad_version", ["2", "v1.0", "1.0.0", "1", ""])
def test_version_requires_major_minor(article_dicts: list[dict], bad_version: str) -> None:
    """The version is half of the unique key; bare PDF integers must be rejected."""
    data = {**article_dicts[0], "version": bad_version}
    with pytest.raises(ValidationError, match="version"):
        Article.model_validate(data)


def test_category_and_service_accept_new_slugs(article_dicts: list[dict]) -> None:
    data = {**article_dicts[0], "service": "pgbouncer"}
    assert Article.model_validate(data).service == "pgbouncer"


def test_category_and_service_reject_non_slugs(article_dicts: list[dict]) -> None:
    data = {**article_dicts[0], "service": "PostgreSQL"}
    with pytest.raises(ValidationError, match="lowercase slugs"):
        Article.model_validate(data)


def test_workflow_state_is_closed_vocabulary(article_dicts: list[dict]) -> None:
    data = {**article_dicts[0], "workflow_state": "archived"}
    with pytest.raises(ValidationError):
        Article.model_validate(data)


def test_unknown_fields_are_rejected_not_dropped(article_dicts: list[dict]) -> None:
    data = {**article_dicts[0], "visibility": "everyone"}
    with pytest.raises(ValidationError, match="visibility"):
        Article.model_validate(data)


def test_short_description_respects_servicenow_limit(article_dicts: list[dict]) -> None:
    data = {**article_dicts[0], "short_description": "x" * 256}
    with pytest.raises(ValidationError):
        Article.model_validate(data)


def test_chunk_rejects_index_outside_bounds(sample_articles: list[Article]) -> None:
    article = sample_articles[0]
    with pytest.raises(ValueError, match="chunk_index"):
        ArticleChunk(
            article_id=article.article_id,
            chunk_index=2,
            total_chunks=2,
            section="Resolution",
            text="SELECT 1;",
        )


def test_payload_from_chunk_carries_all_mandatory_fields(
    sample_articles: list[Article],
) -> None:
    article = sample_articles[0]
    chunk = ArticleChunk(
        article_id=article.article_id,
        chunk_index=0,
        total_chunks=1,
        section="Resolution",
        text="ALTER ROLE svc_app CONNECTION LIMIT 200;",
    )
    payload = KnowledgePayload.from_chunk(article, chunk).to_qdrant_payload()

    for field in ("category", "service", "workflow_state", "version", "security_level"):
        assert field in payload
    assert payload["article_number"] == "KB0001"
    assert payload["article_id"] == "KB0001-v2.0"
    assert payload["workflow_state"] == "published"
    assert payload["chunk_text"].startswith("ALTER ROLE")


def test_payload_omits_unset_servicenow_fields(sample_articles: list[Article]) -> None:
    article = sample_articles[0]
    chunk = ArticleChunk(
        article_id=article.article_id,
        chunk_index=0,
        total_chunks=1,
        section="Symptom",
        text="FATAL: 53300 too many connections",
    )
    payload = KnowledgePayload.from_chunk(article, chunk).to_qdrant_payload()
    assert "sys_id" not in payload
    assert "article_url" not in payload


def test_payload_round_trips_servicenow_fields_and_provenance(
    sample_articles: list[Article],
) -> None:
    """Once published, sys_id/owner/author/related_records must reach Qdrant."""
    article = sample_articles[0].model_copy(
        update={
            "sys_id": "0a1b2c3d4e5f67890a1b2c3d4e5f6789",
            "owner": "Network Operations",
            "author": "L. Haddad",
            "related_records": ["PRB0040012", "INC0010023"],
        }
    )
    chunk = ArticleChunk(
        article_id=article.article_id,
        chunk_index=0,
        total_chunks=1,
        section="Resolution",
        text="Clear the cached credential for the VPN profile.",
    )
    payload = KnowledgePayload.from_chunk(article, chunk).to_qdrant_payload()
    assert payload["sys_id"] == "0a1b2c3d4e5f67890a1b2c3d4e5f6789"
    assert payload["owner"] == "Network Operations"
    assert payload["author"] == "L. Haddad"
    assert payload["related_records"] == ["PRB0040012", "INC0010023"]
    assert payload["article_id"] == "KB0001-v2.0"


def test_local_json_source_loads_valid_corpus(corpus_file: Path) -> None:
    articles = LocalJSONSource(corpus_file).load_articles()
    assert len(articles) == 3
    assert articles[0].category == "database"


def test_local_json_source_fails_fast_on_invalid_article(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    # Title below min_length must appear in the report alongside missing fields
    path.write_text(json.dumps([{"title": "x"}]), encoding="utf-8")
    with pytest.raises(ValidationError) as exc_info:
        LocalJSONSource(path).load_articles()
    report_line = summarize_validation_error(exc_info.value, path.name)
    assert path.name in report_line
    assert "title" in report_line
    assert "body" in report_line


def test_local_json_source_rejects_non_array(tmp_path: Path) -> None:
    path = tmp_path / "dict.json"
    path.write_text(json.dumps({"articles": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        LocalJSONSource(path).load_articles()
