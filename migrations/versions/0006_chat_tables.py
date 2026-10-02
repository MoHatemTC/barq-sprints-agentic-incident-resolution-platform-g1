"""Admin chatbot persistence: sessions, conversations, turns, messages.

Separate from the incident-execution tables on purpose: a conversation is not
an execution, and the chat's per-turn state lives here rather than in
workflow_state, whose rows are bound to executions.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_chat_tables"
down_revision = "0005_cluster_waiters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("operator_subject", sa.String(length=255), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    op.create_index("ix_chat_sessions_operator", "chat_sessions", ["operator_subject"])

    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column("operator_subject", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_chat_conversations_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_conversations")),
        sa.CheckConstraint("btrim(title) <> ''", name="title_not_empty"),
    )
    op.create_index(
        "ix_chat_conversations_session", "chat_conversations", ["session_id", "created_at"]
    )

    op.create_table(
        "chat_turns",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("route", sa.String(length=32), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("usage", JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversations.id"],
            name="fk_chat_turns_conversation_id_chat_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_turns")),
        sa.UniqueConstraint(
            "conversation_id", "request_id", name="uq_chat_turns_conversation_request"
        ),
        sa.CheckConstraint(
            "route IS NULL OR route IN ('knowledge', 'clarification', 'incident_read', "
            "'work_note', 'unavailable')",
            name="route",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'blocked')", name="status"
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) "
            "OR (status <> 'running' AND completed_at IS NOT NULL)",
            name="terminal_state",
        ),
        sa.CheckConstraint("usage IS NULL OR jsonb_typeof(usage) = 'object'", name="usage_object"),
    )
    op.create_index(
        "uq_chat_turns_one_active",
        "chat_turns",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", JSONB(none_as_null=True), server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversations.id"],
            name="fk_chat_messages_conversation_id_chat_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            name="fk_chat_messages_turn_id_chat_turns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_chat_messages_conversation_seq"),
        sa.CheckConstraint("seq >= 1", name="positive_seq"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="role"),
        sa.CheckConstraint(
            "citations IS NULL OR jsonb_typeof(citations) = 'array'", name="citations_array"
        ),
    )
    op.create_index("ix_chat_messages_turn", "chat_messages", ["turn_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_turn", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("uq_chat_turns_one_active", table_name="chat_turns")
    op.drop_table("chat_turns")
    op.drop_index("ix_chat_conversations_session", table_name="chat_conversations")
    op.drop_table("chat_conversations")
    op.drop_index("ix_chat_sessions_operator", table_name="chat_sessions")
    op.drop_table("chat_sessions")
