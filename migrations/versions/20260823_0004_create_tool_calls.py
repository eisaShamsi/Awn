"""Create scoped tasks, executable plan actions, and durable tool calls.

Revision ID: 20260823_0004
Revises: 20260823_0003
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823_0004"
down_revision: str | Sequence[str] | None = "20260823_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

structured_content_type = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True),
    "postgresql",
)


def upgrade() -> None:
    with op.batch_alter_table("plan_steps") as batch_op:
        batch_op.add_column(sa.Column("tool_name", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("operation", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("tool_input", structured_content_type, nullable=True))
        batch_op.create_check_constraint(
            "ck_plan_steps_tool_action",
            "(tool_name IS NULL AND operation IS NULL AND tool_input IS NULL) OR "
            "(tool_name IS NOT NULL AND operation IS NOT NULL AND tool_input IS NOT NULL)",
        )

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("plan_step_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("input", structured_content_type, nullable=False),
        sa.Column("output", structured_content_type, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "risk IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_calls_risk",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'executing', 'succeeded', 'failed', 'cancelled')",
            name="ck_tool_calls_status",
        ),
        sa.ForeignKeyConstraint(["plan_step_id"], ["plan_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_tool_calls_idempotency_key"),
        sa.UniqueConstraint("plan_step_id", name="uq_tool_calls_plan_step"),
    )
    op.create_index(
        "ix_tool_calls_run_status",
        "tool_calls",
        ["run_id", "status"],
        unique=False,
    )

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("workspace_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("source_tool_call_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_tasks_workspace",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_tasks_source_tool_call",
            "tool_calls",
            ["source_tool_call_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_tasks_source_tool_call",
            ["source_tool_call_id"],
        )

    connection = op.get_bind()
    task_count = connection.scalar(sa.text("SELECT COUNT(*) FROM tasks"))
    workspace_id = connection.scalar(
        sa.text("SELECT id FROM workspaces ORDER BY created_at, id LIMIT 1")
    )
    if task_count and workspace_id is None:
        raise RuntimeError(
            "Legacy tasks exist without a workspace; run Awn setup before this migration"
        )
    if workspace_id is not None:
        connection.execute(
            sa.text("UPDATE tasks SET workspace_id = :workspace_id WHERE workspace_id IS NULL"),
            {"workspace_id": workspace_id},
        )

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column(
            "workspace_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch_op.create_index(
            "ix_tasks_workspace_status",
            ["workspace_id", "status"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_workspace_status")
        batch_op.drop_constraint("uq_tasks_source_tool_call", type_="unique")
        batch_op.drop_constraint("fk_tasks_source_tool_call", type_="foreignkey")
        batch_op.drop_constraint("fk_tasks_workspace", type_="foreignkey")
        batch_op.drop_column("source_tool_call_id")
        batch_op.drop_column("workspace_id")

    op.drop_index("ix_tool_calls_run_status", table_name="tool_calls")
    op.drop_table("tool_calls")

    with op.batch_alter_table("plan_steps") as batch_op:
        batch_op.drop_constraint("ck_plan_steps_tool_action", type_="check")
        batch_op.drop_column("tool_input")
        batch_op.drop_column("operation")
        batch_op.drop_column("tool_name")
