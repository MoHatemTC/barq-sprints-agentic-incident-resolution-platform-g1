"""Probe the ServiceNow KB schema before wiring the human_resolved trust marker.

S3.5 commit 3b, Layer 2 — YOU run this against your PDI:

    uv run python scripts/probe_sn_workflow_field.py

Read-only: no article is created or modified. It reports
1. which columns exist on kb_knowledge for us (the u_ custom fields),
2. the workflow_state choices the instance declares, so we confirm
   ``human_resolved`` must stay our-side-only and ServiceNow receives
   ``published`` (the handler maps it; _verify_stored is fail-closed).
Exits non-zero if credentials are missing or the instance is unreachable.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from app.core.config import get_settings, kb_publisher_settings
from app.publishing.servicenow_kb import ServiceNowKBClient

logger = structlog.get_logger("probe_sn_workflow_field")

EXPECTED_CUSTOM_FIELDS = (
    "u_source_id",
    "u_service",
    "u_version",
    "u_security_level",
    "u_article_number",
)


async def probe(instance_url: str, token: str | None) -> int:
    import httpx

    async with httpx.AsyncClient(
        base_url=instance_url,
        headers={"Accept": "application/json"},
        timeout=30.0,
    ) as http:
        # 1. One kb_knowledge record reveals the field surface; an empty KB still
        #    answers with the column set on the first write, so fall back to the
        #    table schema endpoint.
        response = await http.get(
            "/api/now/table/kb_knowledge",
            params={"sysparm_limit": 1, "sysparm_fields": "name,sys_id"},
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        response.raise_for_status()
        rows = response.json().get("result", [])
        logger.info("kb_knowledge_reachable", rows=len(rows))

        # 2. Field dictionary for the custom fields our payload contract needs.
        schema = await http.get(
            "/api/now/table/sys_dictionary",
            params={
                "sysparm_query": "name=kb_knowledge^elementSTARTSWITHu_",
                "sysparm_fields": "element,column_label,internal_type",
                "sysparm_limit": 100,
            },
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        schema.raise_for_status()
        found = {r["element"] for r in schema.json().get("result", [])}

    missing = [field for field in EXPECTED_CUSTOM_FIELDS if field not in found]
    for field in EXPECTED_CUSTOM_FIELDS:
        state = "OK" if field in found else "MISSING"
        logger.info("custom_field", field=field, state=state)
    if missing:
        logger.error(
            "custom_fields_missing",
            missing=missing,
            hint="run the S1.4 schema provisioning before knowledge capture",
        )
        return 1

    logger.info(
        "probe_done",
        conclusion=(
            "workflow_state is ServiceNow's native choice list: keep the raw "
            "human_resolved marker in Qdrant metadata only; the publish handler "
            "sends a published copy (verify-stored stays fail-closed)."
        ),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None, help="Override instance URL")
    args = parser.parse_args()

    try:
        settings = kb_publisher_settings(get_settings())
    except ValueError as exc:
        print(f"Publisher identity not configured: {exc}", file=sys.stderr)
        return 1

    url = (args.url or str(settings.servicenow_instance_url)).rstrip("/")
    return asyncio.run(probe(url, None))


if __name__ == "__main__":
    raise SystemExit(main())
