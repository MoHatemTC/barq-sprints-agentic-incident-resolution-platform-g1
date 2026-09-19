"""Async ServiceNow kb_knowledge Table API client and idempotent article publisher.

Authenticates via OAuth 2.0 using ServiceNowTokenManager as a dedicated
non-admin integration user (FR-06), scopes article lookups to the target
Knowledge Base, verifies every write with fail-closed read-back checks,
and synchronizes version display records.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx
import structlog

from app.auth.token_manager import ServiceNowTokenManager
from app.core.config import Settings
from app.exceptions.servicenow import (
    ServiceNowAuthenticationError,
    ServiceNowConnectionError,
    ServiceNowTimeoutError,
)
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

logger = structlog.get_logger(__name__)

KB_TABLE = "kb_knowledge"

#: article_id is composed as f"{article_number}-v{version}", so the only shape that can
#: reach a sysparm_query is KB####-vN.N. Re-checked here rather than relied on from the
#: Article model: find_by_source_id takes a bare str, and an unvalidated caller would
#: otherwise be able to inject encoded-query clauses with `^` — the same defect as #43.
_ARTICLE_ID_PATTERN = re.compile(r"^KB\d{4}-v\d+\.\d+$")

# Fields requested on find_by_source_id
_LIST_FIELDS: tuple[str, ...] = (
    "sys_id",
    "number",
    "workflow_state",
    "kb_knowledge_base",
    U_SOURCE_ID_FIELD,
)

# Fields that MUST match what was sent
_VERIFIED_FIELDS: tuple[str, ...] = (
    "short_description",
    "kb_knowledge_base",
    U_SOURCE_ID_FIELD,
    U_SERVICE_FIELD,
    U_VERSION_FIELD,
    U_SECURITY_LEVEL_FIELD,
    U_ARTICLE_NUMBER_FIELD,
)


_IMMUTABLE_STATES: frozenset[str] = frozenset({"published", "retired"})


def _unwrap(value: Any) -> Any:
    """Return a Table API reference field's value; pass scalars through unchanged."""
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


_BETWEEN_TAGS = re.compile(r">\s+<")


def _normalise_html(value: Any) -> Any:
    """Canonicalise HTML so the instance's sanitiser rewrites are not a diff.

    ServiceNow stores article HTML with entities decoded or re-encoded (``&middot;``
    becomes ``·``, ``=`` becomes ``&#61;``) and whitespace between tags removed.
    """
    if isinstance(value, str):
        text = _BETWEEN_TAGS.sub("><", html.unescape(value))
        return " ".join(text.split())
    return value


def _diff_against_stored(stored: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Names of payload fields whose stored value differs from what would be sent."""
    differing: list[str] = []
    for field, sent in payload.items():
        current = _unwrap(stored.get(field))
        if field == "text":
            if _normalise_html(current) != _normalise_html(sent):
                differing.append(field)
        elif current != sent:
            differing.append(field)
    return sorted(differing)


class ServiceNowKBClient:
    """Async HTTP client for the ServiceNow kb_knowledge Table API using OAuth 2.0."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        token_manager: ServiceNowTokenManager | None = None,
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(
            base_url=str(settings.servicenow_instance_url).rstrip("/"),
            timeout=settings.servicenow_timeout_seconds,
        )
        self._owns_http_client = http_client is None
        self._tokens = token_manager or ServiceNowTokenManager(settings, self._http)
        self._provisioner = ServiceNowProvisioner(self)

    async def aclose(self) -> None:
        """Close the underlying HTTP client session if owned."""
        if self._owns_http_client:
            await self._http.aclose()

    async def __aenter__(self) -> ServiceNowKBClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Execute an HTTP request against the Table API with OAuth Bearer authentication."""
        try:
            token = await self._tokens.get_token()
        except ServiceNowAuthenticationError as exc:
            raise ServiceNowAuthError(
                f"ServiceNow rejected OAuth credentials (401): {exc}"
            ) from exc
        except (ServiceNowTimeoutError, ServiceNowConnectionError) as exc:
            raise ServiceNowRequestError(f"OAuth token request failed: {exc}") from exc

        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")
        headers.setdefault("Content-Type", "application/json")

        try:
            response = await self._http.request(method, path, headers=headers, **kwargs)
            if response.status_code == 401 and token:
                # Token may have expired prematurely; force-refresh and retry once
                try:
                    token = await self._tokens.get_token(force_refresh=True, failed_token=token)
                except ServiceNowAuthenticationError as exc:
                    raise ServiceNowAuthError(
                        f"ServiceNow rejected OAuth credentials (401) on refresh: {exc}"
                    ) from exc
                except (ServiceNowTimeoutError, ServiceNowConnectionError) as exc:
                    raise ServiceNowRequestError(f"OAuth token refresh failed: {exc}") from exc

                if token:
                    headers["Authorization"] = f"Bearer {token}"
                response = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ServiceNowRequestError(f"HTTP timeout for {method} {path}: {exc}") from exc
        except httpx.TransportError as exc:
            raise ServiceNowRequestError(
                f"HTTP transport error for {method} {path}: {exc}"
            ) from exc

        if response.status_code == 401:
            raise ServiceNowAuthError(
                f"ServiceNow rejected OAuth credentials (401) for {method} {path}. "
                "Verify SERVICENOW_CLIENT_ID, SERVICENOW_CLIENT_SECRET, "
                "SERVICENOW_USERNAME, and SERVICENOW_PASSWORD."
            )
        if response.status_code == 403:
            raise ServiceNowAccessError(
                f"Account lacks permission for {method} {path} (403). "
                "Knowledge Base permissions are required on the target instance."
            )
        if response.status_code == 400:
            raise ServiceNowKBSchemaError(
                f"ServiceNow rejected request as invalid (400) for {method} {path}: "
                f"{response.text[:300]}. If the query mentions custom fields, "
                "the kb_knowledge table may be missing required schema columns."
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

    async def find_by_source_id(
        self,
        article_id: str,
        *,
        kb_sys_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find the kb_knowledge row stamped with our article ID within target KB.

        Raises:
            ServiceNowKBError: If duplicate records exist for the same source ID,
                or if a row with the source ID belongs to a different Knowledge Base.
        """
        if not _ARTICLE_ID_PATTERN.match(article_id):
            raise ServiceNowKBError(
                f"Refusing to query with article_id={article_id!r}: it must match "
                f"{_ARTICLE_ID_PATTERN.pattern}. Unvalidated values can inject encoded-query "
                "clauses into sysparm_query."
            )

        # First query scoped to the target Knowledge Base
        query = f"{U_SOURCE_ID_FIELD}={article_id}"
        if kb_sys_id:
            query = f"{query}^kb_knowledge_base={kb_sys_id}"

        params = {
            "sysparm_query": query,
            "sysparm_fields": ",".join(_LIST_FIELDS),
            # 11 keeps the message honest about how many duplicates exist without
            # pulling an unbounded result set: "10" reads as "10", ">10" as "at least 10".
            "sysparm_limit": "11",
        }
        res = await self.request("GET", f"/api/now/table/{KB_TABLE}", params=params)
        records = res.json().get("result", [])

        if len(records) > 1:
            sys_ids = [r.get("sys_id") for r in records]
            count = f"at least {len(sys_ids)}" if len(sys_ids) > 10 else str(len(sys_ids))
            raise ServiceNowKBError(
                f"Duplicate kb_knowledge rows carry u_source_id={article_id!r}: "
                f"found {count} (sys_ids: {sys_ids}). Clean up duplicates before publishing."
            )

        if records:
            return records[0]

        # If scoped search found nothing, verify no match exists in another KB
        if kb_sys_id:
            unscoped_params = {
                "sysparm_query": f"{U_SOURCE_ID_FIELD}={article_id}",
                "sysparm_fields": ",".join(_LIST_FIELDS),
                "sysparm_limit": "1",
            }
            unscoped_res = await self.request(
                "GET", f"/api/now/table/{KB_TABLE}", params=unscoped_params
            )
            other_records = unscoped_res.json().get("result", [])
            if other_records:
                other_kb = other_records[0].get("kb_knowledge_base")
                raise ServiceNowKBError(
                    f"Article {article_id!r} already exists in a different Knowledge Base "
                    f"({other_kb!r}, sys_id={other_records[0].get('sys_id')!r}). "
                    "Articles cannot be moved between Knowledge Bases automatically."
                )

        return None

    async def create(self, payload: dict[str, Any]) -> str:
        """POST a new kb_knowledge record; returns its sys_id."""
        response = await self.request("POST", f"/api/now/table/{KB_TABLE}", json=payload)
        sys_id = response.json().get("result", {}).get("sys_id")
        if not sys_id:
            raise ServiceNowRequestError(
                f"Create succeeded but ServiceNow returned no sys_id: {response.text[:300]}"
            )
        return str(sys_id)

    async def update(self, sys_id: str, payload: dict[str, Any]) -> None:
        """PATCH an existing kb_knowledge record in place."""
        await self.request("PATCH", f"/api/now/table/{KB_TABLE}/{sys_id}", json=payload)

    async def get(self, sys_id: str) -> dict[str, Any]:
        """Fetch the full stored record for read-back verification."""
        res = await self.request("GET", f"/api/now/table/{KB_TABLE}/{sys_id}")
        return res.json().get("result", {})

    async def sync_version(self, kb_version_sys_id: str, version_str: str) -> None:
        """Synchronize the linked kb_version record to match the canonical version."""
        await self.request(
            "PATCH",
            f"/api/now/table/kb_version/{kb_version_sys_id}",
            json={"version": version_str},
        )

    # -------------------------------------------------------------------------
    # Provisioning & Preflight Delegations
    # -------------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Ensure custom fields exist on the target instance."""
        await self._provisioner.ensure_schema()

    async def ensure_categories(self, kb_sys_id: str, categories: list[str]) -> dict[str, str]:
        """Resolve or dynamically create categories under the target Knowledge Base."""
        return await self._provisioner.ensure_categories(kb_sys_id, categories)

    async def run_preflight(self, kb_sys_id: str, categories: list[str]) -> dict[str, str]:
        """Execute all preflight configuration checks."""
        return await self._provisioner.run_preflight(kb_sys_id, categories)


async def publish_article(
    client: ServiceNowKBClient,
    article: Article,
    kb_sys_id: str,
    category_mapping: dict[str, str] | None = None,
) -> str:
    """Idempotently publish one article; returns 'created', 'updated' or 'unchanged'.

    Workflow:
    1. Constructs the Table API payload with metadata and converted HTML.
    2. Queries by stable `u_source_id` key scoped to target KB.
    3. Creates new row or updates existing row in place. A new row is created as a draft
       and then moved to its target workflow state.
    4. Reads back the row and verifies stored values match sent values (fail-closed).
    5. Synchronizes the linked `kb_version` record so ServiceNow displays the true version.
    """
    payload = build_kb_payload(article, kb_sys_id, category_mapping=category_mapping)

    existing = await client.find_by_source_id(article.article_id, kb_sys_id=kb_sys_id)
    if existing is None:
        sys_id = await client.create(payload)
        outcome = "created"
        # The platform creates every article as a draft, so the target state is set by a
        # follow-up update of that one field.
        created = await client.get(sys_id)
        if created.get("workflow_state") != payload["workflow_state"]:
            await client.update(sys_id, {"workflow_state": payload["workflow_state"]})
    else:
        sys_id = str(existing["sys_id"])
        stored_state = existing.get("workflow_state")
        target_state = article.workflow_state.value
        # The lookup returns only _LIST_FIELDS, so compare against the full record.
        current = await client.get(sys_id)

        # Nothing to write is reported as "unchanged" with no PATCH. When content
        # differs the PATCH is attempted, and a refusal is raised, never swallowed.
        differing = _diff_against_stored(current, payload)
        if not differing and stored_state == target_state:
            outcome = "unchanged"
        else:
            try:
                await client.update(sys_id, payload)
            except ServiceNowAccessError as err:
                raise ServiceNowWriteRejectedError(
                    f"{article.article_id!r} is {stored_state} and this account may not "
                    f"modify it, but {differing or ['workflow_state']} differ from the "
                    f"corpus, so the stored article is now stale. Publishing changed "
                    f"content requires a version bump: the source id is "
                    f"<number>-v<version>, so raising the version creates a new row "
                    f"instead of editing a frozen one (sys_id={sys_id})."
                ) from err
            outcome = "updated"

    stored = await client.get(sys_id)
    _verify_stored(stored, payload, article.article_id)

    # Sync ServiceNow's version display so UI list view and forms show the exact version
    version_ref = stored.get("version")
    if isinstance(version_ref, dict) and "value" in version_ref:
        await client.sync_version(version_ref["value"], article.version)

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
    missing = [field for field in _VERIFIED_FIELDS if field not in sent]
    if missing:
        raise ServiceNowKBError(
            f"Cannot verify the write for {article_id!r}: the payload is missing "
            f"{missing}, which _VERIFIED_FIELDS requires. This is a payload-builder bug, "
            "not an instance problem."
        )

    # kb_category is only in the payload when the article's category has a mapping.
    compared = list(_VERIFIED_FIELDS)
    if "kb_category" in sent:
        compared.append("kb_category")

    for field in compared:
        stored_value = _unwrap(stored.get(field))
        if stored_value != sent[field]:
            raise ServiceNowWriteRejectedError(
                f"Read-back mismatch for {article_id!r}: field {field!r} sent as "
                f"{sent[field]!r} but stored as {stored_value!r} (sys_id={stored.get('sys_id')}). "
                "The write may have been silently altered — check instance state."
            )

    stored_state = stored.get("workflow_state")
    if stored_state != sent["workflow_state"]:
        raise ServiceNowWriteRejectedError(
            f"Read-back mismatch for {article_id!r}: field 'workflow_state' sent as "
            f"{sent['workflow_state']!r} but stored as {stored_state!r} "
            f"(sys_id={stored.get('sys_id')}). The article did not reach the target workflow state."
        )

    body = stored.get("text") or ""
    if article_id not in body:
        raise ServiceNowWriteRejectedError(
            f"Read-back mismatch for {article_id!r}: stored body no longer contains "
            f"the provenance Source marker (sys_id={stored.get('sys_id')})."
        )
