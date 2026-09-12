"""Instance provisioning and preflight configuration for ServiceNow Knowledge Base.

Ensures that any target instance (new PDI or production) has the required
database columns, category hierarchy, list view layouts, and instance settings
in place before articles are published.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
REQUIRED_SCHEMA_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "name": "kb_knowledge",
        "element": U_SOURCE_ID_FIELD,
        "column_label": "Source ID",
        "internal_type": "string",
        "max_length": 40,
    },
    {
        "name": "kb_knowledge",
        "element": U_SERVICE_FIELD,
        "column_label": "Service",
        "internal_type": "string",
        "max_length": 50,
    },
    {
        "name": "kb_knowledge",
        "element": U_VERSION_FIELD,
        "column_label": "Version",
        "internal_type": "string",
        "max_length": 20,
    },
    {
        "name": "kb_knowledge",
        "element": U_SECURITY_LEVEL_FIELD,
        "column_label": "Security Level",
        "internal_type": "string",
        "max_length": 50,
    },
    {
        "name": "kb_knowledge",
        "element": U_ARTICLE_NUMBER_FIELD,
        "column_label": "Article Number",
        "internal_type": "string",
        "max_length": 20,
    },
)

# Columns to display by default in the list view
DEFAULT_LIST_COLUMNS: tuple[str, ...] = (
    "workflow_state",
    U_SERVICE_FIELD,
    U_VERSION_FIELD,
    U_SECURITY_LEVEL_FIELD,
    U_SOURCE_ID_FIELD,
)


class ServiceNowProvisioner:
    """Provisions and validates ServiceNow infrastructure prerequisites."""

    def __init__(self, client: ServiceNowKBClient) -> None:
        self._client = client

    def ensure_schema(self) -> None:
        """Ensure all custom fields exist on the kb_knowledge table.

        Raises:
            ServiceNowKBSchemaError: If inspecting or creating custom columns fails.
            ServiceNowAuthError: If authentication fails.
            ServiceNowAccessError: If account lacks dictionary admin permissions.
        """
        for field in REQUIRED_SCHEMA_FIELDS:
            element = field["element"]
            query = f"name=kb_knowledge^element={element}"
            try:
                res = (
                    self._client.request(
                        "GET",
                        "/api/now/table/sys_dictionary",
                        params={"sysparm_query": query},
                    )
                    .json()
                    .get("result", [])
                )
                if not res:
                    self._client.request("POST", "/api/now/table/sys_dictionary", json=field)
                    logger.info("schema_field_created", element=element)
            except (ServiceNowAuthError, ServiceNowAccessError):
                raise
            except Exception as exc:
                raise ServiceNowKBSchemaError(
                    f"Failed to inspect or provision custom schema column {element!r} "
                    f"on kb_knowledge: {exc}"
                ) from exc

        self.ensure_list_views()

    def ensure_list_views(self) -> None:
        """Ensure the default list views display workflow state and custom fields."""
        try:
            lists = (
                self._client.request(
                    "GET",
                    "/api/now/table/sys_ui_list",
                    params={"sysparm_query": "name=kb_knowledge^view=Default view"},
                )
                .json()
                .get("result", [])
            )

            for list_rec in lists:
                lid = list_rec.get("sys_id")
                if not lid:
                    continue
                elements = (
                    self._client.request(
                        "GET",
                        "/api/now/table/sys_ui_list_element",
                        params={"sysparm_query": f"list_id={lid}"},
                    )
                    .json()
                    .get("result", [])
                )
                existing_cols = {el.get("element") for el in elements}
                position = len(elements)

                for col in DEFAULT_LIST_COLUMNS:
                    if col not in existing_cols:
                        self._client.request(
                            "POST",
                            "/api/now/table/sys_ui_list_element",
                            json={"list_id": lid, "element": col, "position": position},
                        )
                        position += 1
        except Exception as exc:
            logger.debug("ensure_list_view_elements_failed", error=str(exc))

    def ensure_categories(
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
            res = (
                self._client.request(
                    "GET",
                    "/api/now/table/kb_category",
                    params={"sysparm_query": f"parent_id={kb_sys_id}"},
                )
                .json()
                .get("result", [])
            )
        except (ServiceNowAuthError, ServiceNowAccessError):
            raise
        except Exception as exc:
            raise ServiceNowRequestError(
                f"Failed to query kb_category for KB {kb_sys_id!r}: {exc}"
            ) from exc

        existing_by_value = {r.get("value", "").lower(): r["sys_id"] for r in res if "sys_id" in r}
        existing_by_label = {r.get("label", "").lower(): r["sys_id"] for r in res if "sys_id" in r}

        for cat in categories:
            cat_lower = cat.lower()
            if cat_lower in existing_by_value:
                mapping[cat] = existing_by_value[cat_lower]
            elif cat_lower in existing_by_label:
                mapping[cat] = existing_by_label[cat_lower]
            else:
                try:
                    create_res = (
                        self._client.request(
                            "POST",
                            "/api/now/table/kb_category",
                            json={
                                "label": cat.capitalize(),
                                "value": cat_lower,
                                "parent_id": kb_sys_id,
                                "parent_table": "kb_knowledge_base",
                            },
                        )
                        .json()
                        .get("result", {})
                    )
                except (ServiceNowAuthError, ServiceNowAccessError):
                    raise
                except Exception as exc:
                    raise ServiceNowRequestError(
                        f"Failed to create kb_category {cat!r} for KB {kb_sys_id!r}: {exc}"
                    ) from exc

                if "sys_id" in create_res:
                    mapping[cat] = create_res["sys_id"]
                    logger.info("category_created", category=cat, sys_id=create_res["sys_id"])
                else:
                    raise ServiceNowRequestError(
                        f"ServiceNow returned no sys_id when creating kb_category {cat!r}"
                    )
        return mapping

    def run_preflight(
        self,
        kb_sys_id: str,
        categories: list[str],
    ) -> dict[str, str]:
        """Execute all preflight configuration checks in a single orchestrated call."""
        logger.info("running_preflight_provisioning")
        self.ensure_schema()
        category_mapping = self.ensure_categories(kb_sys_id, categories)
        logger.info("preflight_provisioning_complete", categories=len(category_mapping))
        return category_mapping
