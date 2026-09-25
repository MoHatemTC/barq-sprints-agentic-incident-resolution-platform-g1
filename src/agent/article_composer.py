"""The Article Composer (S3.5) — restructure a human's solution into a KB article.

One structured LLM call per capture (purpose ``article_composer``); the code
around it owns every fact the model is not told: the article number, version,
trust metadata and slug normalization. Faithfulness to the human's words is a
deterministic backstop here (``check_faithfulness``), not just a prompt rule —
the LLM proposes, code disposes.

The composer never talks to ServiceNow: the article number is allocated by the
caller (knowledge capture) and passed in, keeping this module pure and faked
easily in tests.
"""

from __future__ import annotations

import re

from agent.dependencies import AgentDependencies
from agent.nodes.classify import incident_text
from agent.prompts import (
    ARTICLE_COMPOSER_SYSTEM,
    ComposedArticle,
    compose_article_prompt,
)
from agent.state import IncidentSnapshot
from app.models.knowledge import SecurityLevel, WorkflowState

#: Tokens below this length carry too little content to judge faithfulness on.
MIN_TOKEN_LENGTH = 4

_FAITHFULNESS_STOPWORDS = frozenset(
    {
        "also", "after", "before", "been", "being", "both", "done", "each", "every",
        "from", "have", "having", "into", "just", "must", "only", "onto", "over",
        "same", "should", "since", "some", "such", "than", "that", "their", "them",
        "then", "there", "these", "they", "this", "those", "through", "under",
        "until", "very", "was", "were", "what", "when", "which", "while", "will",
        "with", "would", "your", "yours",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _content_tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens long enough to be judged as content."""
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= MIN_TOKEN_LENGTH and token not in _FAITHFULNESS_STOPWORDS
    }


def check_faithfulness(
    article: object, solution_text: str, *, incident_context: str = ""
) -> list[str]:
    """Return content tokens in the article body that no source text contains.

    Deterministic and extractive — deliberately not a second LLM call, so the
    capture path stays at exactly one model invocation. Stopwords and tokens
    under ``MIN_TOKEN_LENGTH`` are ignored; numbers are checked (they are facts).
    """
    allowed = _content_tokens(solution_text) | _content_tokens(incident_context)
    body_tokens = _content_tokens(getattr(article, "body", ""))
    return sorted(body_tokens - allowed)


def _slugify(value: str | None) -> str:
    """Normalize arbitrary label text into the Article vocabulary's slug shape."""
    if not value:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug


def compose_article(
    incident: IncidentSnapshot,
    solution_text: str,
    *,
    deps: AgentDependencies,
    article_number: str,
) -> "object":
    """Compose one human-resolved KB article from a terse human solution.

    Raises ``ValueError`` for input the composer refuses before any model call
    (empty/garbage solution) or output that cannot form a real article body;
    LLM transport errors propagate to the caller, which owns retry policy.
    """
    if not solution_text or not solution_text.strip():
        raise ValueError("solution text is empty; nothing to compose from")
    if len(solution_text.strip()) < 3:
        raise ValueError("solution text is too short to compose from")

    answer: ComposedArticle = deps.llm.structured(
        purpose="article_composer",
        system=ARTICLE_COMPOSER_SYSTEM,
        prompt=compose_article_prompt(
            incident_text(incident, deps.settings.agent_max_incident_chars),
            solution_text,
        ),
        schema=ComposedArticle,
    )

    body = answer.body.strip()
    if len(body) < 10:
        raise ValueError("composed body is too short to be an article")

    title = answer.title.strip() or f"Resolution for {incident.number}"
    if len(title) < 5:
        title = f"Resolution for {incident.number}"

    short_description = answer.short_description.strip() or title
    category = _slugify(answer.category) or _slugify(incident.category) or "other"
    service = _slugify(incident.service) or "general"

    from app.models.knowledge import Article

    return Article(
        article_number=article_number,
        version="1.0",
        title=title,
        short_description=short_description[:255],
        body=body,
        category=category,
        service=service,
        workflow_state=WorkflowState.HUMAN_RESOLVED,
        security_level=SecurityLevel.INTERNAL,
    )
