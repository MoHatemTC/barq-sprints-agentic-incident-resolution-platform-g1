"""Instance provisioning and preflight configuration for ServiceNow Knowledge Base.

Verifies that target instances have the required database schema columns
in place (via scoped application update set), dynamically resolves categories
under the target Knowledge Base, and fails loud on any missing prerequisites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.publishing.exceptions import (
    ServiceNowAccessError,
    ServiceNowAuthError,
    ServiceNowKBSchemaError,
    ServiceNowRequestError,
)
from app.publishing.payload import (
    U_ARTICLE_NUMBER_FIELD,
    U_SECURITY_LEVEL_FIELD,
    U_SERVICE_FIELD,
    U_SOURCE_ID_FIELD,
    U_VERSION_FIELD,
)

if TYPE_CHECKING:
    from app.publishing.servicenow_kb import ServiceNowKBClient

logger = structlog.get_logger(__name__)

# Required custom schema columns for the canonical corpus
REQUIRED_SCHEMA_FIELDS: tuple[str, ...] = (
    U_SOURCE_ID_FIELD,
    U_SERVICE_FIELD,
    U_VERSION_FIELD,
    U_SECURITY_LEVEL_FIELD,
    U_ARTICLE_NUMBER_FIELD,
)


class ServiceNowProvisioner:
    """Validates and prepares ServiceNow Knowledge Base infrastructure."""

    def __init__(self, client: ServiceNowKBClient) -> None:
        self._client = client

    async def ensure_schema(self) -> None:
        """Verify all required custom columns exist on the kb_knowledge table.

        Fails loud if any required column is missing, instructing the user
        to install the update set ('servicenow/kb_knowledge_custom_fields.xml').
        Does not perform runtime DDL in Global scope.

        Raises:
            ServiceNowKBSchemaError: If required columns are missing or dictionary query fails.
            ServiceNowAuthError: If authentication fails.
            ServiceNowAccessError: If account lacks dictionary read permissions.
        """
        missing_fields: list[str] = []

        for element in REQUIRED_SCHEMA_FIELDS:
            query = f"name=kb_knowledge^element={element}"
            try:
                res = await self._client.request(
                    "GET",
                    "/api/now/table/sys_dictionary",
                    params={"sysparm_query": query, "sysparm_fields": "element"},
                )
                records = res.json().get("result", [])
                if not records:
                    missing_fields.append(element)
            except (ServiceNowAuthError, ServiceNowAccessError):
                raise
            except Exception as exc:
                raise ServiceNowKBSchemaError(
                    f"Failed to inspect schema column {element!r} on kb_knowledge: {exc}"
                ) from exc

        if missing_fields:
            raise ServiceNowKBSchemaError(
                f"Required schema column(s) {missing_fields} are missing from kb_knowledge. "
                "Ensure custom fields are created on kb_knowledge in ServiceNow "
                "before publishing articles."
            )

        logger.info("schema_verified", verified_columns=list(REQUIRED_SCHEMA_FIELDS))

    async def ensure_categories(
        self,
        kb_sys_id: str,
        categories: list[str],
    ) -> dict[str, str]:
        """Resolve or dynamically create categories in the target KB.

        Returns:
            Dictionary mapping normalized category names to ServiceNow kb_category sys_ids.

        Raises:
            ServiceNowRequestError: If querying or creating categories fails.
            ServiceNowAuthError: If authentication fails.
            ServiceNowAccessError: If account lacks category write permissions.
        """
        mapping: dict[str, str] = {}
        try:
            res = await self._client.request(
                "GET",
                "/api/now/table/kb_category",
                params={
                    "sysparm_query": f"parent_id={kb_sys_id}",
                    "sysparm_fields": "sys_id,value,label",
                },
            )
            existing = {
                (row.get("value") or "").lower(): str(row["sys_id"])
                for row in res.json().get("result", [])
                if row.get("value") or row.get("label")
            }
            # Also index by label in case values differ
            existing.update(
                {
                    (row.get("label") or "").lower(): str(row["sys_id"])
                    for row in res.json().get("result", [])
                    if row.get("label")
                }
            )
        except (ServiceNowAuthError, ServiceNowAccessError):
            raise
        except Exception as exc:
            raise ServiceNowRequestError(f"Failed to query kb_category: {exc}") from exc

        for cat in categories:
            normalized = cat.strip().lower()
            if not normalized:
                continue

            if normalized in existing:
                mapping[cat] = existing[normalized]
                continue

            display_label = cat.strip().title()
            try:
                create_res = await self._client.request(
                    "POST",
                    "/api/now/table/kb_category",
                    json={
                        "parent_id": kb_sys_id,
                        "parent_table": "kb_knowledge_base",
                        "label": display_label,
                        "value": normalized,
                    },
                )
                cat_sys_id = create_res.json().get("result", {}).get("sys_id")
                if not cat_sys_id:
                    raise ServiceNowRequestError(
                        f"ServiceNow created category {cat!r} but returned no sys_id: "
                        f"{create_res.text[:300]}"
                    )
                mapping[cat] = str(cat_sys_id)
                existing[normalized] = str(cat_sys_id)
                logger.info(
                    "category_created",
                    category=cat,
                    sys_id=cat_sys_id,
                    kb_sys_id=kb_sys_id,
                )
            except (ServiceNowAuthError, ServiceNowAccessError):
                raise
            except Exception as exc:
                raise ServiceNowRequestError(
                    f"Failed to create category {cat!r} in kb_category: {exc}"
                ) from exc

        return mapping

    async def run_preflight(
        self,
        kb_sys_id: str,
        categories: list[str],
    ) -> dict[str, str]:
        """Execute preflight configuration checks.

        Returns:
            Resolved category mapping.
        """
        logger.info("running_preflight_provisioning")
        await self.ensure_schema()
        mapping = await self.ensure_categories(kb_sys_id, categories)
        logger.info("preflight_provisioning_complete", categories=len(mapping))
        return mapping
