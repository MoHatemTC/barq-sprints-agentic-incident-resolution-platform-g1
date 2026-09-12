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
    KB_TABLE,
    ServiceNowAuthError,
    ServiceNowKBClient,
    ServiceNowKBError,
    ServiceNowKBSchemaError,
    ServiceNowWriteRejectedError,
    publish_article,
)

INSTANCE = "https://fake-pdi.service-now.com"
KB_SYS_ID = "kb-base-1111111111111111"


class FakeServiceNow:
    """In-memory kb_knowledge table speaking the Table API wire format."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.next_sys_id = 1
        self.query_returns_400 = False
        self.reject_auth = False
        # consumed once: corrupt one field on the next read-back
        self.tamper_next_readback: tuple[str, str] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.reject_auth:
            return httpx.Response(401, json={"error": "unauthorized"})

        path = request.url.path
        params = dict(request.url.params)

        if request.method == "GET" and path == f"/api/now/table/{KB_TABLE}":
            if self.query_returns_400:
                return httpx.Response(400, json={"error": "Invalid query"})
            query = params.get("sysparm_query", "")
            prefix = f"{U_SOURCE_ID_FIELD}="
            if query.startswith(prefix):
                wanted = query[len(prefix) :]
                matches = [r for r in self.rows if r[U_SOURCE_ID_FIELD] == wanted]
            else:
                matches = self.rows
            return httpx.Response(200, json={"result": matches})

        if request.method == "POST" and path == f"/api/now/table/{KB_TABLE}":
            payload = httpx.Request(  # noqa: F841 — parse body below
                request.method, request.url, content=request.read()
            )
            import json as _json

            body = _json.loads(request.read())
            row = {"sys_id": f"sys{self.next_sys_id:011d}", **body}
            self.next_sys_id += 1
            self.rows.append(row)
            return httpx.Response(201, json={"result": row})

        if request.method == "PATCH" and path.startswith(f"/api/now/table/{KB_TABLE}/"):
            import json as _json

            sys_id = path.rsplit("/", 1)[-1]
            body = _json.loads(request.read())
            for row in self.rows:
                if row["sys_id"] == sys_id:
                    row.update(body)
                    return httpx.Response(200, json={"result": row})
            return httpx.Response(404, json={"error": "not found"})

        if request.method == "GET" and path.startswith(f"/api/now/table/{KB_TABLE}/"):
            sys_id = path.rsplit("/", 1)[-1]
            for row in self.rows:
                if row["sys_id"] == sys_id:
                    served = dict(row)
                    if self.tamper_next_readback:
                        field, value = self.tamper_next_readback
                        served[field] = value
                        self.tamper_next_readback = None
                    return httpx.Response(200, json={"result": served})
            return httpx.Response(404, json={"error": "not found"})

        return httpx.Response(405, json={"error": "method not allowed"})

    def build_client(self) -> ServiceNowKBClient:
        return ServiceNowKBClient(
            INSTANCE,
            "admin",
            "secret",
            transport=httpx.MockTransport(self.handler),
        )


@pytest.fixture
def fake() -> FakeServiceNow:
    return FakeServiceNow()


def test_publish_to_fresh_instance_creates_all(
    sample_articles: list[Article], fake: FakeServiceNow
) -> None:
    client = fake.build_client()
    outcomes = [publish_article(client, a, KB_SYS_ID) for a in sample_articles]

    assert outcomes == ["created"] * len(sample_articles)
    assert len(fake.rows) == len(sample_articles)
    stored_ids = {row[U_SOURCE_ID_FIELD] for row in fake.rows}
    assert stored_ids == {a.article_id for a in sample_articles}


def test_republish_updates_in_place_with_zero_duplicates(
    sample_articles: list[Article], fake: FakeServiceNow
) -> None:
    client = fake.build_client()
    for article in sample_articles:
        publish_article(client, article, KB_SYS_ID)
    post_count = len(fake.rows)

    outcomes = [publish_article(client, a, KB_SYS_ID) for a in sample_articles]

    assert outcomes == ["updated"] * len(sample_articles)
    assert len(fake.rows) == post_count, "re-run must never duplicate rows"


def test_renamed_title_still_finds_row_by_source_id(
    sample_articles: list[Article], fake: FakeServiceNow
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


def test_readback_mismatch_fails_loud(sample_articles: list[Article], fake: FakeServiceNow) -> None:
    client = fake.build_client()
    fake.tamper_next_readback = ("workflow_state", "draft")  # instance says something else

    with pytest.raises(ServiceNowWriteRejectedError, match="workflow_state"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_duplicate_source_id_rows_fail_loud(
    sample_articles: list[Article], fake: FakeServiceNow
) -> None:
    client = fake.build_client()
    payload = build_kb_payload(sample_articles[0], KB_SYS_ID)
    fake.rows.append({"sys_id": "dup1", **payload})
    fake.rows.append({"sys_id": "dup2", **payload})

    with pytest.raises(ServiceNowKBError, match="Duplicate"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_missing_u_source_id_column_shows_remedy(
    sample_articles: list[Article], fake: FakeServiceNow
) -> None:
    """Running against a table without the custom field must explain the fix."""
    fake.query_returns_400 = True
    client = fake.build_client()

    with pytest.raises(ServiceNowKBSchemaError, match="u_source_id"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_bad_credentials_raise_auth_error(
    sample_articles: list[Article], fake: FakeServiceNow
) -> None:
    fake.reject_auth = True
    client = fake.build_client()

    with pytest.raises(ServiceNowAuthError, match="401"):
        publish_article(client, sample_articles[0], KB_SYS_ID)


def test_client_sends_basic_auth_header(fake: FakeServiceNow) -> None:
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
