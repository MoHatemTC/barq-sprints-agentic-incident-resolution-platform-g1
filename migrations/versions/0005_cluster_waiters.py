"""Persist cluster waiter trace context; allow later events for the same incident."""

import sqlalchemy as sa
from alembic import op

revision = "0005_cluster_waiters"
down_revision = "0004_semantic_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("semantic_cluster_members", sa.Column("correlation_id", sa.Text()))
    op.drop_constraint(
        "uq_cluster_member_incident_unique", "semantic_cluster_members", type_="unique"
    )


def downgrade() -> None:
    # Fail visibly if later events make the old uniqueness rule impossible.
    op.create_unique_constraint(
        "uq_cluster_member_incident_unique", "semantic_cluster_members", ["incident_sys_id"]
    )
    op.drop_column("semantic_cluster_members", "correlation_id")
