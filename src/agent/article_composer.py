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
from dataclasses import dataclass

from agent.dependencies import AgentDependencies
from agent.nodes.classify import incident_text
from agent.prompts import (
    ARTICLE_COMPOSER_SYSTEM,
    ComposedArticle,
    compose_article_prompt,
)
from agent.state import IncidentSnapshot
from app.models.knowledge import Article, SecurityLevel, WorkflowState

#: The categories the seeded corpus and the retriever's category filter actually
#: use. A captured article outside this set is reachable only via the wide
#: unfiltered pass, so the category is worth grounding in a real field rather than
#: in a model guess.
CORPUS_CATEGORIES = frozenset({"hardware", "inquiry", "network", "software"})

#: Tokens below this length carry too little content to judge faithfulness on.
MIN_TOKEN_LENGTH = 4

_FAITHFULNESS_STOPWORDS = frozenset(
    {
        "also",
        "after",
        "before",
        "been",
        "being",
        "both",
        "done",
        "each",
        "every",
        "from",
        "have",
        "having",
        "into",
        "just",
        "must",
        "only",
        "onto",
        "over",
        "same",
        "should",
        "since",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "until",
        "very",
        "was",
        "were",
        "what",
        "when",
        "which",
        "while",
        "will",
        "with",
        "would",
        "your",
        "yours",
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


def _stem(token: str) -> str:
    """Fold a word to a rough stem so "restarted"/"restarting" match "restart".

    Tokens containing a digit are facts (versions, sizes, addresses, ids) and are compared
    exactly, never stemmed.
    """
    if any(ch.isdigit() for ch in token):
        return token
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


#: Content words a faithful article adds only to structure what the human said.
_SCAFFOLDING = frozenset(
    {
        "apply",
        "check",
        "confirm",
        "ensure",
        "follow",
        "following",
        "issue",
        "issues",
        "perform",
        "problem",
        "procedure",
        "resolution",
        "resolve",
        "resolved",
        "steps",
        "verify",
    }
)

#: An article whose content is mostly words nobody supplied is invention, not rewording.
MAX_UNGROUNDED_RATIO = 0.5


#: A whole number as written: ``30``, ``1.2``, ``10.0.0.5``, ``1,400``. The word tokeniser
#: drops anything under ``MIN_TOKEN_LENGTH`` and splits on the dots, so ports, minutes, retry
#: counts and versions were never compared.
_NUMBER_RE = re.compile(r"\d+(?:[.,:]\d+)*")
#: Numbering a composed article adds itself ("1." at the start of a line, "step 3").
_ORDINAL_RE = re.compile(r"(?im)^\s*\d+[.)]\s+|\bstep\s+\d+")


def _numbers(text: str) -> set[str]:
    return {match.replace(",", "") for match in _NUMBER_RE.findall(text)}


def check_faithfulness(
    article: object, solution_text: str, *, incident_context: str = ""
) -> list[str]:
    """Return content tokens and numbers in the article body that no source text contains.

    Deterministic and extractive — deliberately not a second LLM call, so the
    capture path stays at exactly one model invocation. Stopwords, structural
    scaffolding words and tokens under ``MIN_TOKEN_LENGTH`` are ignored, inflections of
    a source word count as grounded, and numbers are checked exactly and whole, at any
    length (they are facts), apart from the list numbering the composer adds itself.
    """
    allowed_tokens = _content_tokens(solution_text) | _content_tokens(incident_context)
    allowed = allowed_tokens | {_stem(token) for token in allowed_tokens}
    body = getattr(article, "body", "")
    ungrounded = {
        token
        for token in _content_tokens(body)
        if not token.isdigit()  # a bare number is judged whole, below
        and token not in allowed
        and _stem(token) not in allowed
        and token not in _SCAFFOLDING
    }
    source_numbers = _numbers(f"{solution_text}\n{incident_context}")
    ungrounded |= _numbers(_ORDINAL_RE.sub(" ", body)) - source_numbers
    return sorted(ungrounded)


@dataclass(frozen=True, slots=True)
class FaithfulnessVerdict:
    ok: bool
    ungrounded: tuple[str, ...]
    ratio: float
    states_unstated_numbers: bool


def faithfulness_verdict(
    article: object, solution_text: str, *, incident_context: str = ""
) -> FaithfulnessVerdict:
    """Decide whether a composed article may be published as human knowledge.

    It fails when the body states a number or identifier the human never gave (MTU 2048
    where they said 1400), or when most of its content words came from nowhere. Ordinary
    rewording and structuring pass. This is the enforcement of the backstop the design
    names; ``check_faithfulness`` alone only reports.
    """
    issues = check_faithfulness(article, solution_text, incident_context=incident_context)
    body_tokens = _content_tokens(getattr(article, "body", ""))
    ratio = len(issues) / len(body_tokens) if body_tokens else 0.0
    numbers = any(any(ch.isdigit() for ch in token) for token in issues)
    return FaithfulnessVerdict(
        ok=not numbers and ratio <= MAX_UNGROUNDED_RATIO,
        ungrounded=tuple(issues),
        ratio=ratio,
        states_unstated_numbers=numbers,
    )


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
) -> Article:
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
    # Ground the category in the incident's own ServiceNow field when that field
    # names a category the corpus actually uses, and only fall back to the model's
    # guess otherwise.
    #
    # The model was trusted unconditionally, and it guessed: a staff-directory
    # title correction was captured as "software" because the fix was performed
    # with a console (dev407364, 2026-09-28, KB1011). The incident's category was
    # "inquiry". Retrieval only found the article afterwards because the wide pass
    # ignores category, so the wrong label was invisible until it was inspected —
    # and an article the retriever cannot reach by category is one the category
    # filter can never promote.
    incident_category = _slugify(incident.category)
    category = (
        incident_category
        if incident_category in CORPUS_CATEGORIES
        else _slugify(answer.category) or incident_category or "other"
    )
    service = _slugify(incident.service) or "general"

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
