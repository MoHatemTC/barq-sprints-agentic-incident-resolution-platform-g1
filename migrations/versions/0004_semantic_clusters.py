"""Authoritative schema for semantic clusters and cluster memberships.

Sprint 4.2 Semantic Deduplication & Single-Flight Execution:
Creates 'semantic_clusters' and 'semantic_cluster_members' tables to support
distributed single-flight execution, durable shared resolution caching, and
authoritative cluster membership tracking with strict record-level isolation.

Revision ID: 0004_semantic_clusters
Revises: 0003_execution_lease
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_semantic_clusters"
down_revision: str | None = "0003_execution_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_clusters",
        sa.Column(
            "cluster_id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("anchor_incident_sys_id", sa.String(length=32), nullable=False),
        sa.Column("anchor_incident_number", sa.String(length=32), nullable=False),
        sa.Column(
            "anchor_execution_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "executions.execution_id",
                name="fk_semantic_clusters_execution",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "pipeline_execution_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
            comment="The execution ID of the shared LangGraph pipeline run (leader execution).",
        ),
        sa.Column("service", sa.String(length=100), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("similarity_threshold", sa.Float(), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'creating'"),
        ),
        sa.Column("solution", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("anchor_vector", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('creating', 'running', 'awaiting_approval', "
            "'resolved', 'failed', 'expired')",
            name="ck_semantic_clusters_status",
        ),
    )
    op.create_index(
        "ix_semantic_clusters_status_expires",
        "semantic_clusters",
        ["status", "expires_at"],
    )
    op.create_index(
        "ix_semantic_clusters_service",
        "semantic_clusters",
        ["service"],
    )

    op.create_table(
        "semantic_cluster_members",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cluster_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "semantic_clusters.cluster_id",
                name="fk_cluster_members_cluster",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "execution_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "executions.execution_id",
                name="fk_cluster_members_execution",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("incident_sys_id", sa.String(length=32), nullable=False),
        sa.Column("incident_number", sa.String(length=32), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "cluster_id", "execution_id", name="uq_cluster_member_cluster_execution"
        ),
        sa.UniqueConstraint("execution_id", name="uq_cluster_member_execution_unique"),
        sa.UniqueConstraint("incident_sys_id", name="uq_cluster_member_incident_unique"),
        sa.CheckConstraint("role IN ('anchor', 'follower')", name="ck_cluster_member_role"),
    )
    op.create_index(
        "ix_cluster_members_incident_sys_id",
        "semantic_cluster_members",
        ["incident_sys_id"],
    )


def downgrade() -> None:
    op.drop_table("semantic_cluster_members")
    op.drop_table("semantic_clusters")
