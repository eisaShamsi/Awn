"""Create the durable leased tool-call queue.

Revision ID: 20260823_0005
Revises: 20260823_0004
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0005"
down_revision: str | Sequence[str] | None = "20260823_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3")
        )
        batch_op.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("lease_owner", sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.execute("UPDATE tool_calls SET available_at = created_at WHERE available_at IS NULL")

    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.alter_column(
            "available_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_tool_calls_attempts",
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
        )
        batch_op.create_index(
            "ix_tool_calls_queue",
            ["status", "available_at", "lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_index("ix_tool_calls_queue")
        batch_op.drop_constraint("ck_tool_calls_attempts", type_="check")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_owner")
        batch_op.drop_column("available_at")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempt_count")
