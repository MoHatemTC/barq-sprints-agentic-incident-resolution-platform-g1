"""One approval decision per execution and node.

Approvals are immutable audit records: a second, contradictory decision for the
same execution must be impossible to store even if the API's own already-decided
check is bypassed or raced (#147).

``workflow_state_id`` is nullable and is NULL for every row the route writes
today. Postgres treats NULLs as distinct in a unique index, so the column is
coalesced to the all-zero uuid in the indexed expression -- otherwise the index
would allow exactly the duplicate it exists to forbid.

Revision ID: 0002_unique_approval_index
Revises: 0001_postgresql_state_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_unique_approval_index"
down_revision: str | None = "0001_postgresql_state_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_approvals_execution_workflow_state",
        "approvals",
        [
            "execution_id",
            sa.text("COALESCE(workflow_state_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_approvals_execution_workflow_state", table_name="approvals")
