"""Tests for Article → kb_knowledge payload construction."""

import pytest

from app.models.knowledge import Article
from app.publishing.payload import U_SOURCE_ID_FIELD, build_kb_payload


def test_payload_maps_all_core_fields(sample_articles: list[Article]) -> None:
    payload = build_kb_payload(sample_articles[0], "kb-base-sys-id")

    assert payload["short_description"] == sample_articles[0].title
    assert payload["workflow_state"] == sample_articles[0].workflow_state.value
    assert payload["kb_knowledge_base"] == "kb-base-sys-id"
    assert payload[U_SOURCE_ID_FIELD] == sample_articles[0].article_id


def test_payload_text_starts_with_source_marker(sample_articles: list[Article]) -> None:
    """Fields the OOB table lacks (service/version/security) travel in the body header."""
    article = sample_articles[0]
    payload = build_kb_payload(article, "kb-base-sys-id")

    text = payload["text"]
    assert text.startswith(f"<p><strong>Source:</strong> {article.article_id}")
    assert f"service={article.service}" in text
    assert f"version={article.version}" in text
    assert f"security={article.security_level.value}" in text
    # the marker doubles as the stable read-back check target
    assert article.article_id in text


def test_payload_body_is_html_not_markdown(sample_articles: list[Article]) -> None:
    payload = build_kb_payload(sample_articles[0], "kb-base-sys-id")

    assert "## " not in payload["text"]
    assert "<h2>" in payload["text"]


def test_empty_kb_sys_id_fails_loud(sample_articles: list[Article]) -> None:
    with pytest.raises(ValueError, match="SERVICENOW_KB_ID"):
        build_kb_payload(sample_articles[0], "")

    with pytest.raises(ValueError, match="SERVICENOW_KB_ID"):
        build_kb_payload(sample_articles[0], "   ")


def test_u_source_id_distinguishes_versions(sample_articles: list[Article]) -> None:
    """KB0001-v2.0 and KB0001-v1.0 are separate rows — the key must differ."""
    payload_v2 = build_kb_payload(sample_articles[0], "kb-base-sys-id")
    payload_v1 = build_kb_payload(sample_articles[1], "kb-base-sys-id")

    assert payload_v2[U_SOURCE_ID_FIELD] != payload_v1[U_SOURCE_ID_FIELD]
    assert payload_v2[U_SOURCE_ID_FIELD].endswith("-v2.0")
    assert payload_v1[U_SOURCE_ID_FIELD].endswith("-v1.0")
