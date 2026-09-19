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
        outcomes = [await publish_article(client, a, KB_SYS_ID) for a in sample_articles]

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

        outcomes = [await publish_article(client, a, KB_SYS_ID) for a in sample_articles]

        # An identical re-publish has nothing to write.
        assert outcomes == ["unchanged"] * len(sample_articles)
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
async def test_readback_mismatch_fails_loud(sample_articles: list[Article], fake: Any) -> None:
    client = fake.build_client()
    try:
        # instance returns invalid state to simulate tampering or write rejection
        fake.tamper_readback = ("workflow_state", "corrupted_state")

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
        fake.tamper_readback = ("workflow_state", "draft")

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
async def test_find_by_source_id_scoped_to_kb(sample_articles: list[Article], fake: Any) -> None:
    """Articles in another KB must not be matched or overwritten."""
    client = fake.build_client()
    try:
        article = sample_articles[0]
        payload = build_kb_payload(article, "other-kb-999999999")
        fake.rows.append({"sys_id": "row_in_other_kb", **payload})

        with pytest.raises(ServiceNowKBError, match="already exists in a different Knowledge Base"):
            await client.find_by_source_id(article.article_id, kb_sys_id=KB_SYS_ID)
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
async def test_bad_credentials_raise_auth_error(sample_articles: list[Article], fake: Any) -> None:
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
    http_client = httpx.AsyncClient(base_url=INSTANCE, transport=httpx.MockTransport(spy))
    client = ServiceNowKBClient(settings, http_client=http_client)
    try:
        await client.find_by_source_id("KB0001-v2.0", kb_sys_id=KB_SYS_ID)
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

        outcome = await publish_article(client, article, KB_SYS_ID, category_mapping=cat_mapping)

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


# ---------------------------------------------------------------------------
# Hardening added while reviewing #88
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        "KB0001-v1.0^ORDERBYDESCsys_created_on",  # appends an encoded-query clause
        "KB0001-v1.0^NQu_source_id!=",  # ^NQ starts a new OR query: matches everything
        "KB0001",  # no version suffix
        "kb0001-v1.0",  # lowercase
        "",
    ],
)
async def test_find_by_source_id_rejects_ids_that_could_inject_a_query(
    bad_id: str, fake: Any
) -> None:
    """The composed article_id is safe today only because Article validates its parts.

    find_by_source_id takes a bare str, so it re-checks rather than trusting the caller
    — otherwise an unvalidated id reintroduces the encoded-query injection of #43.
    """
    client = fake.build_client()
    try:
        with pytest.raises(ServiceNowKBError, match="Refusing to query"):
            await client.find_by_source_id(bad_id, kb_sys_id=KB_SYS_ID)
        # Nothing was sent.
        assert not fake.rows
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_find_by_source_id_accepts_a_well_formed_id(fake: Any) -> None:
    client = fake.build_client()
    try:
        assert await client.find_by_source_id("KB0010-v2.0", kb_sys_id=KB_SYS_ID) is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_refused_patch_on_changed_published_article_fails_loudly(
    sample_articles: list[Article], fake: Any
) -> None:
    """A 403 on a changed published article raises instead of reporting 'updated'."""
    client = fake.build_client()
    try:
        article = sample_articles[0]
        assert article.workflow_state.value == "published"
        await publish_article(client, article, KB_SYS_ID)

        # The corpus gains a resolution step; the instance now refuses the write.
        changed = article.model_copy(update={"body": article.body + "\n\n6. Restart the service."})
        fake.refuse_patch = True

        with pytest.raises(ServiceNowWriteRejectedError) as excinfo:
            await publish_article(client, changed, KB_SYS_ID)

        message = str(excinfo.value)
        assert "text" in message, "the error must name the field that drifted"
        assert "version" in message, "the error must point at the version-bump remedy"

        # The stored body is unchanged.
        stored = next(r for r in fake.rows if r[U_SOURCE_ID_FIELD] == article.article_id)
        assert "Restart the service." not in stored["text"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_identical_republish_reports_unchanged_without_patching(
    sample_articles: list[Article], fake: Any
) -> None:
    """An identical re-publish reports 'unchanged' and sends no PATCH."""
    client = fake.build_client()
    try:
        article = sample_articles[0]
        assert await publish_article(client, article, KB_SYS_ID) == "created"

        # Every PATCH now 403s. An identical re-publish must not need one.
        fake.refuse_patch = True
        assert await publish_article(client, article, KB_SYS_ID) == "unchanged"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_republish_is_unchanged_after_instance_html_sanitising(
    sample_articles: list[Article], fake: Any
) -> None:
    """Entity and whitespace rewrites by the instance are not a content change."""
    fake.sanitise_html = True
    client = fake.build_client()
    try:
        article = sample_articles[0]
        assert await publish_article(client, article, KB_SYS_ID) == "created"
        stored = next(r for r in fake.rows if r[U_SOURCE_ID_FIELD] == article.article_id)
        assert "&#61;" in stored["text"], "the fake must actually rewrite the HTML"

        fake.refuse_patch = True
        assert await publish_article(client, article, KB_SYS_ID) == "unchanged"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_new_article_is_moved_from_draft_to_its_target_state(
    sample_articles: list[Article], fake: Any
) -> None:
    """The platform creates articles as drafts; the publisher sets the state afterwards."""
    fake.force_draft_on_create = True
    client = fake.build_client()
    try:
        article = sample_articles[0]
        assert article.workflow_state.value == "published"
        assert await publish_article(client, article, KB_SYS_ID) == "created"

        stored = next(r for r in fake.rows if r[U_SOURCE_ID_FIELD] == article.article_id)
        assert stored["workflow_state"] == "published"
        assert {"workflow_state": "published"} in fake.patches
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_no_state_update_when_the_create_already_has_the_target_state(
    sample_articles: list[Article], fake: Any
) -> None:
    client = fake.build_client()
    try:
        assert await publish_article(client, sample_articles[0], KB_SYS_ID) == "created"
        assert fake.patches == []
    finally:
        await client.aclose()
