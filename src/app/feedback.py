"""Learning from outcomes: which knowledge articles keep working, which keep failing.

When an incident the agent resolved is closed, every article its fix cited gets a
``confirmed`` row; when the caller reopens it, a ``reopened`` row. The net score
(confirmed − reopened) is read before the agent resolves on its own: an article with a
score at or below ``WEAK_SCORE`` no longer resolves alone — the fix goes to an engineer
instead (scenario K3). The rows are also the "weak articles" report for knowledge owners.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ArticleFeedback

logger = structlog.getLogger(__name__)

#: At this net score or below an article no longer lets the agent resolve on its own.
WEAK_SCORE = -2
_ARTICLE = re.compile(r"\bKB\d{4,7}\b")


def cited_articles(text: str | None) -> list[str]:
    return list(dict.fromkeys(_ARTICLE.findall(text or "")))


class ArticleFeedbackStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def record(
        self, *, incident_sys_id: str, articles: Iterable[str], outcome: str, event_id: str
    ) -> int:
        rows = [
            {
                "article_number": article,
                "incident_sys_id": incident_sys_id,
                "outcome": outcome,
                "event_id": event_id[:64],
            }
            for article in dict.fromkeys(articles)
        ]
        if not rows:
            return 0
        with self._session_factory() as session:
            # A redelivered event is recorded once.
            session.execute(
                insert(ArticleFeedback)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_article_feedback_event_article")
            )
            session.commit()
        return len(rows)

    def scores(self, articles: Iterable[str]) -> dict[str, int]:
        wanted = list(dict.fromkeys(articles))
        if not wanted:
            return {}
        score = func.sum(case((ArticleFeedback.outcome == "confirmed", 1), else_=-1))
        with self._session_factory() as session:
            rows = session.execute(
                select(ArticleFeedback.article_number, score)
                .where(ArticleFeedback.article_number.in_(wanted))
                .group_by(ArticleFeedback.article_number)
            ).all()
        return {str(article): int(total or 0) for article, total in rows}

    def summary(self, limit: int = 100) -> list[dict[str, Any]]:
        confirmed = func.sum(case((ArticleFeedback.outcome == "confirmed", 1), else_=0))
        reopened = func.sum(case((ArticleFeedback.outcome == "reopened", 1), else_=0))
        with self._session_factory() as session:
            rows = session.execute(
                select(ArticleFeedback.article_number, confirmed, reopened)
                .group_by(ArticleFeedback.article_number)
                .order_by((confirmed - reopened).asc())
                .limit(limit)
            ).all()
        return [
            {
                "article_number": str(article),
                "confirmed": int(ok or 0),
                "reopened": int(bad or 0),
                "score": int(ok or 0) - int(bad or 0),
                "weak": int(ok or 0) - int(bad or 0) <= WEAK_SCORE,
            }
            for article, ok, bad in rows
        ]


def weak_articles(trust: Callable[[list[str]], dict[str, int]] | None, fix: str) -> list[str]:
    """Articles cited by ``fix`` whose record says they keep failing; [] when unknown."""
    if trust is None:
        return []
    articles = cited_articles(fix)
    if not articles:
        return []
    try:
        scores = trust(articles)
    except Exception as exc:  # noqa: BLE001 - no record means no penalty
        logger.warning("article_trust_unavailable", error_type=type(exc).__name__)
        return []
    return [article for article in articles if scores.get(article, 0) <= WEAK_SCORE]


__all__ = ["WEAK_SCORE", "ArticleFeedbackStore", "cited_articles", "weak_articles"]
