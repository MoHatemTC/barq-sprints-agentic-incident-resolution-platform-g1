"""Article feedback: what happened after the agent resolved an incident with an article.

Revision ID: 0009_article_feedback
Revises: 0008_event_contract_v2
Create Date: 2026-10-03

One row per (event, article): ``confirmed`` when an agent-resolved incident was closed,
``reopened`` when the caller came back. The agent reads the net score per article and
stops resolving on its own with an article that keeps failing (agent.feedback).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_article_feedback"
down_revision: str | None = "0008_event_contract_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "article_feedback",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("article_number", sa.String(length=32), nullable=False),
        sa.Column("incident_sys_id", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "outcome IN ('confirmed', 'reopened')", name=op.f("ck_article_feedback_outcome")
        ),
        sa.UniqueConstraint(
            "event_id", "article_number", name=op.f("uq_article_feedback_event_article")
        ),
    )
    op.create_index("ix_article_feedback_article", "article_feedback", ["article_number"])


def downgrade() -> None:
    op.drop_index("ix_article_feedback_article", table_name="article_feedback")
    op.drop_table("article_feedback")
