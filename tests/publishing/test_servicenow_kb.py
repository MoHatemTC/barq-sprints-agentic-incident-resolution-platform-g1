"""Tests for the kb_knowledge Table API client and idempotent upsert.

Uses httpx.MockTransport as an in-process fake ServiceNow — no live PDI
required. The fake mirrors the Table API contract the publisher relies on:
encoded-query GET, POST, PATCH, and per-sys_id GET.
"""

from typing import Any

import httpx
import pytest

from app.models.knowledge import Article
from app.publishing.payload import U_SOURCE_ID_FIELD, build_kb_payload
from app.publishing.servicenow_kb import (
    ServiceNowAuthError,
    ServiceNowKBClient,
    ServiceNowKBError,
    ServiceNowKBSchemaError,
    ServiceNowWriteRejectedError,
    publish_article,
)

INSTANCE = "https://fake-pdi.service-now.com"
KB_SYS_ID = "kb-base-1111111111111111"


def test_publish_to_fresh_instance_creates_all(sample_articles: list[Article], fake: Any) -> None:
    client = fake.build_client()
    outcomes = [publish_article(client, a, KB_SYS_ID) for a in sample_articles]

    assert outcomes == ["created"] * len(sample_articles)
    assert len(fake.rows) == len(sample_articles)
    stored_ids = {row[U_SOURCE_ID_FIELD] for row in fake.rows}
    assert stored_ids == {a.article_id for a in sample_articles}


def test_republish_updates_in_place_with_zero_duplicates(
    sample_articles: list[Article], fake: Any
) -> None:
    client = fake.build_client()
    for article in sample_articles:
        publish_article(client, article, KB_SYS_ID)
    post_count = len(fake.rows)

    outcomes = [publish_article(client, a, KB_SYS_ID) for a in sample_articles]

    assert outcomes == ["updated"] * len(sample_articles)
    assert len(fake.rows) == post_count, "re-run must never duplicate rows"


def test_renamed_title_still_finds_row_by_source_id(
    sample_articles: list[Article], fake: Any
) -> None:
    """A human renames the title in the UI — the u_source_id lookup still wins."""
    client = fake.build_client()
    article = sample_articles[0]
    publish_article(client, article, KB_SYS_ID)

    for row in fake.rows:
        row["short_description"] = "A human renamed this title in the UI"

    outcome = publish_article(client, article, KB_SYS_ID)

    assert outcome == "updated"
    assert len(fake.rows) == 1
    assert fake.rows[0]["short_description"] == article.title


def test_readback_mismatch_fails_loud(sample_articles: list[Article], fake: Any) -> None:
    client = fake.build_client()
    # instance returns invalid state to simulate tampering or write rejection
    fake.tamper_next_readback = ("workflow_state", "corrupted_state")

    with pytest.raises(ServiceNowWriteRejectedError, match="workflow_state"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_duplicate_source_id_rows_fail_loud(sample_articles: list[Article], fake: Any) -> None:
    client = fake.build_client()
    payload = build_kb_payload(sample_articles[0], KB_SYS_ID)
    fake.rows.append({"sys_id": "dup1", **payload})
    fake.rows.append({"sys_id": "dup2", **payload})

    with pytest.raises(ServiceNowKBError, match="Duplicate"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_missing_u_source_id_column_shows_remedy(sample_articles: list[Article], fake: Any) -> None:
    """Running against a table without the custom field must explain the fix."""
    fake.query_returns_400 = True
    client = fake.build_client()

    with pytest.raises(ServiceNowKBSchemaError, match="u_source_id"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_bad_credentials_raise_auth_error(sample_articles: list[Article], fake: Any) -> None:
    fake.reject_auth = True
    client = fake.build_client()

    with pytest.raises(ServiceNowAuthError, match="401"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_client_sends_basic_auth_header(fake: Any) -> None:
    seen: dict[str, str] = {}

    def spy(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"result": []})

    client = ServiceNowKBClient(INSTANCE, "admin", "s3cret", transport=httpx.MockTransport(spy))
    client.find_by_source_id("KB0001-v2.0")
    client.close()

    import base64

    expected = base64.b64encode(b"admin:s3cret").decode()
    assert seen["authorization"] == f"Basic {expected}"


def test_ensure_schema_and_properties_run_without_error(fake: Any) -> None:
    client = fake.build_client()
    # Should run cleanly and create missing dictionary fields
    client.ensure_schema()
    client.close()


def test_ensure_categories_resolves_and_creates(fake: Any) -> None:
    client = fake.build_client()
    cats = ["network", "software"]
    mapping = client.ensure_categories(KB_SYS_ID, cats)
    client.close()

    assert "network" in mapping
    assert "software" in mapping
    assert mapping["network"] == "cat_network"
    assert mapping["software"] == "cat_software"


def test_publish_article_with_dynamic_category(sample_articles: list[Article], fake: Any) -> None:
    client = fake.build_client()
    article = sample_articles[0]
    cat_mapping = {article.category: "sys-cat-999"}

    outcome = publish_article(client, article, KB_SYS_ID, category_mapping=cat_mapping)

    assert outcome == "created"
    assert fake.rows[0]["kb_category"] == "sys-cat-999"
    assert "category" not in fake.rows[0]


def test_sync_version_raises_on_auth_error(fake: Any) -> None:
    fake.reject_auth = True
    client = fake.build_client()
    with pytest.raises(ServiceNowAuthError):
        client.sync_version("ver-123", "2.0")
    client.close()
