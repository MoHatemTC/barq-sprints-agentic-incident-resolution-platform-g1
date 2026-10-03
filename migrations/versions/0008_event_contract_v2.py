"""Event contract v2: conversation event types and the acting user.

Revision ID: 0008_event_contract_v2
Revises: 0007_chat_history_summary
Create Date: 2026-10-03

Widens the events.event_type check to every type in ``app.events.EVENT_TYPES`` and
adds ``actor_sys_id`` (who in ServiceNow caused the event; NULL for v1 events).
Downgrade restores the v1 check as NOT VALID so rows already accepted under v2 are
kept rather than deleted, while new rows are held to v1 again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_event_contract_v2"
down_revision: str | None = "0007_chat_history_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

V2_TYPES = (
    "incident.created",
    "incident.updated",
    "incident.caller_replied",
    "incident.caller_updated",
    "incident.handed_back",
    "incident.engineer_replied",
    "incident.reopened",
    "incident.closed",
)


def _in(values: Sequence[str]) -> str:
    return "event_type IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint("ck_events_event_type", "events", type_="check")
    op.create_check_constraint("ck_events_event_type", "events", _in(V2_TYPES))
    op.add_column("events", sa.Column("actor_sys_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "actor_sys_id")
    op.drop_constraint("ck_events_event_type", "events", type_="check")
    op.execute(
        "ALTER TABLE events ADD CONSTRAINT ck_events_event_type CHECK ("
        + _in(V2_TYPES[:2])
        + ") NOT VALID"
    )
