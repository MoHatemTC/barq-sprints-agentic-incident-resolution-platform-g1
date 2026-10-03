"""Chat conversation memory: rolling older-history summary.

Revision ID: 0007_chat_history_summary
Revises: 0006_chat_tables
Create Date: 2026-10-03

The service summarizes messages that aged out of the recent-history window and
stores the result on the conversation; summary_seq marks the newest message
seq the summary covers so summarization resumes incrementally.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_chat_history_summary"
down_revision: str | None = "0006_chat_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_conversations",
        sa.Column("history_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "chat_conversations",
        sa.Column(
            "summary_seq",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_conversations", "summary_seq")
    op.drop_column("chat_conversations", "history_summary")
