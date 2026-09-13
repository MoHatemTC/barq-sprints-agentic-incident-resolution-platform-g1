"""ServiceNow Table API client and publishing service for the Knowledge Base.

Follows Clean Architecture:
- Core Table API CRUD operations are isolated in ``ServiceNowKBClient``.
- Infrastructure preflight / provisioning is delegated to ``ServiceNowProvisioner``.
- Idempotent upsert and read-back verification are encapsulated in ``publish_article``.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import structlog

from app.models.knowledge import Article
from app.publishing.exceptions import (
    ServiceNowAccessError,
    ServiceNowAuthError,
    ServiceNowKBError,
    ServiceNowKBSchemaError,
    ServiceNowRequestError,
    ServiceNowWriteRejectedError,
)
from app.publishing.payload import (
    U_ARTICLE_NUMBER_FIELD,
    U_SECURITY_LEVEL_FIELD,
    U_SERVICE_FIELD,
    U_SOURCE_ID_FIELD,
    U_VERSION_FIELD,
    build_kb_payload,
)
from app.publishing.provisioning import ServiceNowProvisioner

__all__ = [
    "KB_TABLE",
    "ServiceNowAccessError",
    "ServiceNowAuthError",
    "ServiceNowKBClient",
    "ServiceNowKBError",
    "ServiceNowKBSchemaError",
    "ServiceNowProvisioner",
    "ServiceNowRequestError",
    "ServiceNowWriteRejectedError",
    "publish_article",
]

logger = structlog.get_logger(__name__)

KB_TABLE = "kb_knowledge"

# Fields compared exactly on read-back verification;
# The article body is checked for the provenance marker instead of raw byte
# equality because ServiceNow sanitizes and reformats incoming HTML.
_VERIFIED_FIELDS: tuple[str, ...] = (
    "short_description",
    "kb_knowledge_base",
    U_SOURCE_ID_FIELD,
    U_SERVICE_FIELD,
    U_VERSION_FIELD,
    U_SECURITY_LEVEL_FIELD,
    U_ARTICLE_NUMBER_FIELD,
)

_LIST_FIELDS: tuple[str, ...] = (
    "sys_id",
    "short_description",
    "workflow_state",
    "kb_knowledge_base",
    "text",
    U_SOURCE_ID_FIELD,
    U_SERVICE_FIELD,
    U_VERSION_FIELD,
    U_SECURITY_LEVEL_FIELD,
    U_ARTICLE_NUMBER_FIELD,
)


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


class ServiceNowKBClient:
    """HTTP client for the ServiceNow kb_knowledge Table API."""

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
        self._provisioner = ServiceNowProvisioner(self)

    def close(self) -> None:
        """Close the underlying HTTP client session."""
        self._client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Execute an HTTP request against the Table API with normalized error handling."""
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise ServiceNowRequestError(
                f"HTTP transport error for {method} {path}: {exc}"
            ) from exc

        if response.status_code == 401:
            raise ServiceNowAuthError(
                f"ServiceNow rejected credentials (401) for {method} {path}. "
                "Check SERVICENOW_USERNAME / SERVICENOW_PASSWORD."
            )
        if response.status_code == 403:
            raise ServiceNowAccessError(
                f"Account lacks permission for {method} {path} (403). "
                "Admin or Knowledge Admin credentials are required on the target instance."
            )
        if response.status_code == 400:
            raise ServiceNowKBSchemaError(
                f"ServiceNow rejected request as invalid (400) for {method} {path}: "
                f"{response.text[:300]}. If the query mentions u_source_id, "
                "the kb_knowledge table is missing the u_source_id column."
            )
        if response.status_code >= 400:
            raise ServiceNowRequestError(
                f"ServiceNow returned HTTP {response.status_code} for {method} {path}: "
                f"{response.text[:300]}"
            )
        return response

    # Private alias for backwards compatibility
    _request = request

    # -------------------------------------------------------------------------
    # Knowledge Article CRUD
    # -------------------------------------------------------------------------

    def find_by_source_id(self, article_id: str) -> dict[str, Any] | None:
        """Find the kb_knowledge row stamped with our article ID, if any.

        Raises:
            ServiceNowKBError: If duplicate records exist for the same source ID.
        """
        params = {
            "sysparm_query": f"{U_SOURCE_ID_FIELD}={article_id}",
            "sysparm_fields": ",".join(_LIST_FIELDS),
            "sysparm_limit": "2",
        }
        records = self.request("GET", f"/api/now/table/{KB_TABLE}", params=params).json()["result"]
        if len(records) > 1:
            sys_ids = [r.get("sys_id") for r in records]
            raise ServiceNowKBError(
                f"Duplicate kb_knowledge rows carry u_source_id={article_id!r} "
                f"(sys_ids: {sys_ids}). Clean up duplicates before publishing."
            )
        return records[0] if records else None

    def create(self, payload: dict[str, Any]) -> str:
        """POST a new kb_knowledge record; returns its sys_id."""
        response = self.request("POST", f"/api/now/table/{KB_TABLE}", json=payload)
        sys_id = response.json()["result"].get("sys_id")
        if not sys_id:
            raise ServiceNowRequestError(
                f"Create succeeded but ServiceNow returned no sys_id: {response.text[:300]}"
            )
        return str(sys_id)

    def update(self, sys_id: str, payload: dict[str, Any]) -> None:
        """PATCH an existing kb_knowledge record in place."""
        self.request("PATCH", f"/api/now/table/{KB_TABLE}/{sys_id}", json=payload)

    def get(self, sys_id: str) -> dict[str, Any]:
        """Fetch the full stored record for read-back verification."""
        return self.request("GET", f"/api/now/table/{KB_TABLE}/{sys_id}").json()["result"]

    def sync_version(self, kb_version_sys_id: str, version_str: str) -> None:
        """Synchronize the linked kb_version record to match the canonical version."""
        try:
            self.request(
                "PATCH",
                f"/api/now/table/kb_version/{kb_version_sys_id}",
                json={"version": version_str},
            )
        except (ServiceNowAuthError, ServiceNowAccessError):
            raise
        except Exception as exc:
            logger.warning(
                "sync_version_failed",
                kb_version_sys_id=kb_version_sys_id,
                error=str(exc),
            )

    # -------------------------------------------------------------------------
    # Provisioning & Preflight Delegations
    # -------------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Ensure custom fields and list views exist on the target instance."""
        self._provisioner.ensure_schema()

    def ensure_categories(self, kb_sys_id: str, categories: list[str]) -> dict[str, str]:
        """Resolve or dynamically create categories under the target Knowledge Base."""
        return self._provisioner.ensure_categories(kb_sys_id, categories)

    def run_preflight(self, kb_sys_id: str, categories: list[str]) -> dict[str, str]:
        """Execute all preflight configuration checks."""
        return self._provisioner.run_preflight(kb_sys_id, categories)


def publish_article(
    client: ServiceNowKBClient,
    article: Article,
    kb_sys_id: str,
    category_mapping: dict[str, str] | None = None,
) -> str:
    """Idempotently publish one article into ServiceNow; returns 'created' or 'updated'.

    Workflow:
    1. Constructs the Table API payload with metadata and converted HTML.
    2. Queries by stable `u_source_id` key.
    3. Creates new row or updates existing row in place.
    4. Reads back the row and verifies stored values match sent values.
    5. Synchronizes the linked `kb_version` record so ServiceNow displays the true version.
    """
    payload = build_kb_payload(article, kb_sys_id, category_mapping=category_mapping)

    existing = client.find_by_source_id(article.article_id)
    if existing is None:
        sys_id = client.create(payload)
        outcome = "created"
    else:
        sys_id = str(existing["sys_id"])
        # In ServiceNow with versioning enabled, retired articles are immutable (403 on PATCH).
        # If the record is already retired, skip redundant PATCH and let read-back verify it.
        if (
            existing.get("workflow_state") == "retired"
            and article.workflow_state.value == "retired"
        ):
            outcome = "updated"
        else:
            client.update(sys_id, payload)
            outcome = "updated"

    stored = client.get(sys_id)
    _verify_stored(stored, payload, article.article_id)

    # Sync ServiceNow's version display so UI list view and forms show the exact version
    version_ref = stored.get("version")
    if isinstance(version_ref, dict) and "value" in version_ref:
        client.sync_version(version_ref["value"], article.version)

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
    """Validate stored row against sent payload, failing loud on any mismatch."""
    for field in _VERIFIED_FIELDS:
        stored_value = stored.get(field)
        if isinstance(stored_value, dict) and "value" in stored_value:
            stored_value = stored_value["value"]
        if stored_value != sent[field]:
            raise ServiceNowWriteRejectedError(
                f"Read-back mismatch for {article_id!r}: field {field!r} sent as "
                f"{sent[field]!r} but stored as {stored_value!r} (sys_id={stored.get('sys_id')}). "
                "The write may have been silently altered — check instance state."
            )

    stored_state = stored.get("workflow_state")
    if stored_state not in (sent["workflow_state"], "draft"):
        raise ServiceNowWriteRejectedError(
            f"Read-back mismatch for {article_id!r}: field 'workflow_state' sent as "
            f"{sent['workflow_state']!r} but stored as {stored_state!r} "
            f"(sys_id={stored.get('sys_id')})."
        )

    body = stored.get("text") or ""
    if article_id not in body:
        raise ServiceNowWriteRejectedError(
            f"Read-back mismatch for {article_id!r}: stored body no longer contains "
            f"the provenance Source marker (sys_id={stored.get('sys_id')})."
        )
