"""Article outcomes for knowledge owners: which articles keep working, which keep failing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.auth.auth import require_role, verify_bearer_token
from app.db.models import ArticleFeedback
from app.exceptions.app_errors import ServiceUnavailableError
from app.feedback import WEAK_SCORE

router = APIRouter(
    prefix="/api/v1/feedback",
    tags=["Feedback"],
    dependencies=[Depends(verify_bearer_token), Depends(require_role("operator"))],
)


class ArticleOutcome(BaseModel):
    article_number: str
    confirmed: int
    reopened: int
    score: int
    #: True when the agent no longer resolves on its own with this article.
    weak: bool


@router.get(
    "/articles",
    response_model=list[ArticleOutcome],
    summary="Outcome score per knowledge article, weakest first",
)
async def article_outcomes(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ArticleOutcome]:
    confirmed = func.sum(case((ArticleFeedback.outcome == "confirmed", 1), else_=0))
    reopened = func.sum(case((ArticleFeedback.outcome == "reopened", 1), else_=0))
    try:
        rows = (
            await db.execute(
                select(ArticleFeedback.article_number, confirmed, reopened)
                .group_by(ArticleFeedback.article_number)
                .order_by((confirmed - reopened).asc())
                .limit(limit)
            )
        ).all()
    except SQLAlchemyError as exc:
        raise ServiceUnavailableError("Database unavailable to read article feedback.") from exc
    result = []
    for article, ok, bad in rows:
        score = int(ok or 0) - int(bad or 0)
        result.append(
            ArticleOutcome(
                article_number=str(article),
                confirmed=int(ok or 0),
                reopened=int(bad or 0),
                score=score,
                weak=score <= WEAK_SCORE,
            )
        )
    return result


__all__ = ["router"]
