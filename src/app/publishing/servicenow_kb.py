"""Thin ServiceNow Table API client for kb_knowledge publishing.

Deliberately minimal: Basic Auth against a personal developer instance,
idempotent upsert keyed by our composed article ID, and read-back
verification after every write (stored == sent). When the team's shared
ServiceNow client (S1.5) merges, the HTTP/auth layer here is replaced by
it — the upsert/verify logic stays.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

from app.models.knowledge import Article
from app.publishing.payload import U_SOURCE_ID_FIELD, build_kb_payload

logger = structlog.get_logger(__name__)

KB_TABLE = "kb_knowledge"

# Fields compared exactly on read-back; the body is checked for the source
# marker instead of byte equality because ServiceNow sanitizes HTML.
_VERIFIED_FIELDS = ("short_description", "workflow_state", "kb_knowledge_base", U_SOURCE_ID_FIELD)

_LIST_FIELDS = (
    "sys_id",
    "short_description",
    "workflow_state",
    "kb_knowledge_base",
    "text",
    U_SOURCE_ID_FIELD,
)


class ServiceNowKBError(Exception):
    """Base error for kb_knowledge publishing failures."""


class ServiceNowAuthError(ServiceNowKBError):
    """Credentials rejected (HTTP 401)."""


class ServiceNowAccessError(ServiceNowKBError):
    """Account lacks permission for the operation (HTTP 403)."""


class ServiceNowKBSchemaError(ServiceNowKBError):
    """The kb_knowledge table is missing the expected custom setup."""


class ServiceNowRequestError(ServiceNowKBError):
    """Unexpected Table API response."""


class ServiceNowWriteRejectedError(ServiceNowKBError):
    """Read-back after a write did not match what was sent."""


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


class ServiceNowKBClient:
    """HTTP client for the kb_knowledge Table API on one instance."""

    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=instance_url.rstrip("/"),
            headers={"Authorization": _basic_auth_header(username, password)},
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.status_code == 401:
            raise ServiceNowAuthError(
                f"ServiceNow rejected the credentials (401) for {method} {path}. "
                "Check SERVICENOW_USERNAME / SERVICENOW_PASSWORD."
            )
        if response.status_code == 403:
            raise ServiceNowAccessError(
                f"Account lacks permission for {method} {path} (403). "
                "Admin credentials are required on the target PDI."
            )
        if response.status_code == 400:
            raise ServiceNowKBSchemaError(
                f"ServiceNow rejected the request as invalid (400) for {method} {path}: "
                f"{response.text[:300]}. If the message mentions u_source_id or "
                "sysparm_query, the kb_knowledge table is missing the u_source_id "
                "String field — add it in System Definition > Tables, or see "
                "docs/sprint-1/s1.4-knowledge-and-qdrant/."
            )
        if response.status_code >= 400:
            raise ServiceNowRequestError(
                f"ServiceNow returned {response.status_code} for {method} {path}: "
                f"{response.text[:300]}"
            )
        return response

    def find_by_source_id(self, article_id: str) -> dict[str, Any] | None:
        """Find the kb_knowledge row stamped with our article ID, if any.

        Raises ServiceNowKBError when more than one row matches — that is
        duplicate data corruption, never a state to publish over silently.
        """
        params = {
            "sysparm_query": f"{U_SOURCE_ID_FIELD}={article_id}",
            "sysparm_fields": ",".join(_LIST_FIELDS),
            "sysparm_limit": "2",
        }
        records = self._request("GET", f"/api/now/table/{KB_TABLE}", params=params).json()["result"]
        if len(records) > 1:
            raise ServiceNowKBError(
                f"Duplicate kb_knowledge rows carry u_source_id={article_id!r} "
                f"(sys_ids: {[r['sys_id'] for r in records]}). Clean up the "
                "duplicates before re-running the publisher."
            )
        return records[0] if records else None

    def create(self, payload: dict[str, Any]) -> str:
        """POST a new kb_knowledge record; returns its ServiceNow sys_id."""
        response = self._request("POST", f"/api/now/table/{KB_TABLE}", json=payload)
        sys_id = response.json()["result"].get("sys_id")
        if not sys_id:
            raise ServiceNowRequestError(
                f"Create succeeded but ServiceNow returned no sys_id: {response.text[:300]}"
            )
        return str(sys_id)

    def update(self, sys_id: str, payload: dict[str, Any]) -> None:
        """PATCH an existing kb_knowledge record in place."""
        self._request("PATCH", f"/api/now/table/{KB_TABLE}/{sys_id}", json=payload)

    def get(self, sys_id: str) -> dict[str, Any]:
        """Fetch the full stored record for read-back verification."""
        return self._request("GET", f"/api/now/table/{KB_TABLE}/{sys_id}").json()["result"]


def publish_article(
    client: ServiceNowKBClient,
    article: Article,
    kb_sys_id: str,
) -> str:
    """Idempotently publish one article; returns 'created' or 'updated'.

    Lookup is by u_source_id (our stable article ID), so re-runs update in
    place even if the title was edited in the UI. Every write is verified
    by reading the row back and comparing it against what was sent.
    """
    payload = build_kb_payload(article, kb_sys_id)

    existing = client.find_by_source_id(article.article_id)
    if existing is None:
        sys_id = client.create(payload)
        outcome = "created"
    else:
        sys_id = str(existing["sys_id"])
        client.update(sys_id, payload)
        outcome = "updated"

    stored = client.get(sys_id)
    _verify_stored(stored, payload, article.article_id)

    logger.info(
        "article_published",
        article_id=article.article_id,
        outcome=outcome,
        sys_id=sys_id,
    )
    return outcome


def _verify_stored(
    stored: dict[str, Any],
    sent: dict[str, Any],
    article_id: str,
) -> None:
    """Fail loud unless the stored row matches the sent payload."""
    for field in _VERIFIED_FIELDS:
        stored_value = stored.get(field)
        if stored_value != sent[field]:
            raise ServiceNowWriteRejectedError(
                f"Read-back mismatch for {article_id!r}: field {field!r} sent as "
                f"{sent[field]!r} but stored as {stored_value!r} (sys_id={stored.get('sys_id')}). "
                "The write may have been silently altered — fix the instance state "
                "before re-running."
            )
    body = stored.get("text") or ""
    if article_id not in body:
        raise ServiceNowWriteRejectedError(
            f"Read-back mismatch for {article_id!r}: the stored body no longer "
            "contains the Source marker. ServiceNow may have sanitized the "
            f"article text (sys_id={stored.get('sys_id')})."
        )
