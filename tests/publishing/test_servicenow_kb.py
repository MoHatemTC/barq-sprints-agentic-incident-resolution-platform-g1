"""Tests for the kb_knowledge Table API client and idempotent upsert.

Uses httpx.MockTransport as an in-process fake ServiceNow — no live PDI
required. The fake mirrors the Table API contract the publisher relies on:
OAuth token exchange, encoded-query GET, POST, PATCH, and per-sys_id GET.
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
    ServiceNowRequestError,
    ServiceNowWriteRejectedError,
    publish_article,
)
from tests.helpers import mock_settings

INSTANCE = "https://fake-pdi.service-now.com"
KB_SYS_ID = "kb-base-1111111111111111"


@pytest.mark.asyncio
async def test_publish_to_fresh_instance_creates_all(
    sample_articles: list[Article], fake: Any
) -> None:
    client = fake.build_client()
    try:
        outcomes = [
            await publish_article(client, a, KB_SYS_ID) for a in sample_articles
        ]

        assert outcomes == ["created"] * len(sample_articles)
        assert len(fake.rows) == len(sample_articles)
        stored_ids = {row[U_SOURCE_ID_FIELD] for row in fake.rows}
        assert stored_ids == {a.article_id for a in sample_articles}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_republish_updates_in_place_with_zero_duplicates(
    sample_articles: list[Article], fake: Any
) -> None:
    client = fake.build_client()
    try:
        for article in sample_articles:
            await publish_article(client, article, KB_SYS_ID)
        post_count = len(fake.rows)

        outcomes = [
            await publish_article(client, a, KB_SYS_ID) for a in sample_articles
        ]

        assert outcomes == ["updated"] * len(sample_articles)
        assert len(fake.rows) == post_count, "re-run must never duplicate rows"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_renamed_title_still_finds_row_by_source_id(
    sample_articles: list[Article], fake: Any
) -> None:
    """A human renames the title in the UI — the u_source_id lookup still wins."""
    client = fake.build_client()
    try:
        article = sample_articles[0]
        await publish_article(client, article, KB_SYS_ID)

        for row in fake.rows:
            row["short_description"] = "A human renamed this title in the UI"

        outcome = await publish_article(client, article, KB_SYS_ID)

        assert outcome == "updated"
        assert len(fake.rows) == 1
        assert fake.rows[0]["short_description"] == article.title
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_readback_mismatch_fails_loud(
    sample_articles: list[Article], fake: Any
) -> None:
    client = fake.build_client()
    try:
        # instance returns invalid state to simulate tampering or write rejection
        fake.tamper_next_readback = ("workflow_state", "corrupted_state")

        with pytest.raises(ServiceNowWriteRejectedError, match="workflow_state"):
            await publish_article(client, sample_articles[0], KB_SYS_ID)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_verify_stored_fails_when_article_stuck_in_draft(
    sample_articles: list[Article], fake: Any
) -> None:
    """Fail-closed: if an article was published but ends up stuck in draft, fail loud."""
    client = fake.build_client()
    try:
        article = sample_articles[0]
        assert article.workflow_state.value == "published"
        fake.tamper_next_readback = ("workflow_state", "draft")

        with pytest.raises(ServiceNowWriteRejectedError, match="target workflow state"):
            await publish_article(client, article, KB_SYS_ID)
    finally:
        await client.aclose()



@pytest.mark.asyncio
async def test_duplicate_source_id_rows_fail_loud(
    sample_articles: list[Article], fake: Any
) -> None:
    client = fake.build_client()
    try:
        payload = build_kb_payload(sample_articles[0], KB_SYS_ID)
        fake.rows.append({"sys_id": "dup1", **payload})
        fake.rows.append({"sys_id": "dup2", **payload})

        with pytest.raises(ServiceNowKBError, match="Duplicate"):
            await publish_article(client, sample_articles[0], KB_SYS_ID)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_missing_u_source_id_column_shows_remedy(
    sample_articles: list[Article], fake: Any
) -> None:
    fake.query_returns_400 = True
    client = fake.build_client()
    try:
        with pytest.raises(ServiceNowKBSchemaError, match="schema columns"):
            await publish_article(client, sample_articles[0], KB_SYS_ID)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_bad_credentials_raise_auth_error(
    sample_articles: list[Article], fake: Any
) -> None:
    fake.reject_auth = True
    client = fake.build_client()
    try:
        with pytest.raises(ServiceNowAuthError, match="401"):
            await publish_article(client, sample_articles[0], KB_SYS_ID)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_client_sends_bearer_auth_header() -> None:
    seen: dict[str, str] = {}

    def spy(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth_token.do":
            return httpx.Response(
                200,
                json={
                    "access_token": "bearer_token_xyz",
                    "token_type": "Bearer",
                    "expires_in": 1800,
                },
            )
        seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"result": []})

    settings = mock_settings(
        servicenow_instance_url=INSTANCE,
        servicenow_kb_id=KB_SYS_ID,
        servicenow_client_id="test_cid",
        servicenow_client_secret="test_secret",
        servicenow_username="svc_user",
        servicenow_password="svc_password",
    )
    http_client = httpx.AsyncClient(
        base_url=INSTANCE, transport=httpx.MockTransport(spy)
    )
    client = ServiceNowKBClient(settings, http_client=http_client)
    try:
        await client.find_by_source_id("KB0001-v2.0")
        assert seen["authorization"] == "Bearer bearer_token_xyz"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ensure_schema_verifies_cleanly(fake: Any) -> None:
    client = fake.build_client()
    try:
        await client.ensure_schema()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ensure_categories_resolves_and_creates(fake: Any) -> None:
    client = fake.build_client()
    try:
        cats = ["network", "software"]
        mapping = await client.ensure_categories(KB_SYS_ID, cats)

        assert "network" in mapping
        assert "software" in mapping
        assert mapping["network"] == "cat_network"
        assert mapping["software"] == "cat_software"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_publish_article_with_dynamic_category(
    sample_articles: list[Article], fake: Any
) -> None:
    client = fake.build_client()
    try:
        article = sample_articles[0]
        cat_mapping = {article.category: "sys-cat-999"}

        outcome = await publish_article(
            client, article, KB_SYS_ID, category_mapping=cat_mapping
        )

        assert outcome == "created"
        assert fake.rows[0]["kb_category"] == "sys-cat-999"
        assert "category" not in fake.rows[0]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sync_version_raises_on_auth_error(fake: Any) -> None:
    fake.reject_auth = True
    client = fake.build_client()
    try:
        with pytest.raises(ServiceNowAuthError):
            await client.sync_version("ver-123", "2.0")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sync_version_raises_on_server_error(fake: Any) -> None:
    fake.kb_version_returns_error = True
    client = fake.build_client()
    try:
        with pytest.raises(ServiceNowRequestError):
            await client.sync_version("ver-123", "2.0")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_publish_article_fails_loud_when_version_sync_fails(
    sample_articles: list[Article], fake: Any
) -> None:
    fake.kb_version_returns_error = True
    client = fake.build_client()
    try:
        with pytest.raises(ServiceNowRequestError):
            await publish_article(client, sample_articles[0], KB_SYS_ID)
    finally:
        await client.aclose()
