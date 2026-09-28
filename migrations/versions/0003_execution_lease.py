"""Execution lease columns so a crashed run can be identified and reclaimed.

A worker that dies mid-graph leaves its execution row at ``running`` forever.
Nothing in the schema could tell that run apart from a live one, so the crash
reaper had no signal to act on: a SIGKILL of the Celery worker during ``validate``
on dev407364 (2026-09-28) left the row ``running`` at that node indefinitely, and 96
such rows had already accumulated. ``abandoned`` was in the status enum the whole
time; nothing ever set it.

``heartbeat_at`` is the lease clock the reaper reads and ``worker_id`` names the
holder for the crash report. Both are nullable, so existing rows are untouched and
are treated as stale (their heartbeat is NULL) by the reaper.

Revision ID: 0003_execution_lease
Revises: 0002_unique_approval_index
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_execution_lease"
down_revision: str | None = "0002_unique_approval_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("executions", sa.Column("worker_id", sa.String(length=128), nullable=True))
    op.create_index(
        "ix_executions_running_heartbeat",
        "executions",
        ["status", "heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_executions_running_heartbeat", table_name="executions")
    op.drop_column("executions", "worker_id")
    op.drop_column("executions", "heartbeat_at")
