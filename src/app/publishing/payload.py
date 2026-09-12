"""kb_knowledge Table API payload construction.

Maps the canonical `Article` model onto ServiceNow's `kb_knowledge` columns.
Fields the out-of-the-box table has no column for (service, version,
security_level) travel inside a visible "Source:" header line at the top of
the article body until the team decides on dedicated custom columns. The
composed article ID additionally travels in `u_source_id` — a custom String
field added to the table — which is the stable lookup key for idempotent
publishing (titles are human-editable; the source ID is not).
"""

from __future__ import annotations

from typing import Any

from app.models.knowledge import Article
from app.publishing.html import markdown_to_html

U_SOURCE_ID_FIELD = "u_source_id"


def build_kb_payload(article: Article, kb_sys_id: str) -> dict[str, Any]:
    """Build the Table API body for creating/updating one kb_knowledge record.

    Args:
        article: Canonical article from the corpus.
        kb_sys_id: sys_id of the target kb_knowledge_base record.

    Returns:
        JSON body for POST/PATCH against /api/now/table/kb_knowledge.

    Raises:
        ValueError: If ``kb_sys_id`` is empty — the target Knowledge Base
            must be created in the PDI and wired via ``SERVICENOW_KB_ID``
            before anything can be published.
    """
    if not kb_sys_id or not kb_sys_id.strip():
        raise ValueError(
            "kb_sys_id is empty. Create a Knowledge Base in your PDI and set "
            "SERVICENOW_KB_ID in .env before publishing."
        )

    article_id = article.article_id
    source_header = (
        f"<p><strong>Source:</strong> {article_id}"
        f" &middot; service={article.service}"
        f" &middot; version={article.version}"
        f" &middot; security={article.security_level.value}</p>"
    )

    return {
        "short_description": article.title,
        "text": source_header + markdown_to_html(article.body),
        "workflow_state": article.workflow_state.value,
        "kb_knowledge_base": kb_sys_id,
        U_SOURCE_ID_FIELD: article_id,
    }
