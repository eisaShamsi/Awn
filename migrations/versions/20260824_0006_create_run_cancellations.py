"""Create durable, truthful run cancellation state.

Revision ID: 20260824_0006
Revises: 20260823_0005
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0006"
down_revision: str | Sequence[str] | None = "20260823_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("ck_runs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_runs_status",
            "status IN ('received', 'planning', 'needs_clarification', 'ready', "
            "'awaiting_approval', 'executing', 'cancellation_requested', "
            "'cancellation_uncertain', 'verifying', 'succeeded', "
            "'partially_succeeded', 'failed', 'denied', 'cancelled')",
        )

    with op.batch_alter_table("plan_steps") as batch_op:
        batch_op.drop_constraint("ck_plan_steps_status", type_="check")
        batch_op.create_check_constraint(
            "ck_plan_steps_status",
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'skipped', "
            "'cancelled', 'outcome_unknown')",
        )

    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_constraint("ck_tool_calls_status", type_="check")
        batch_op.create_check_constraint(
            "ck_tool_calls_status",
            "status IN ('pending', 'executing', 'succeeded', 'failed', 'cancelled', "
            "'outcome_unknown')",
        )
        batch_op.add_column(
            sa.Column("effect_committed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("effect_commit_token", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("effect_commit_worker_id", sa.String(length=100), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_tool_calls_effect_commit",
            "(effect_committed_at IS NULL AND effect_commit_token IS NULL AND "
            "effect_commit_worker_id IS NULL) OR "
            "(effect_committed_at IS NOT NULL AND effect_commit_token IS NOT NULL AND "
            "effect_commit_worker_id IS NOT NULL)",
        )

    op.create_table(
        "run_cancellations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('accepted', 'uncertain', 'cancelled', 'partially_succeeded', "
            "'completed', 'execution_failed')",
            name="ck_run_cancellations_status",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_run_cancellations_run_id"),
    )
    op.create_index(
        "ix_run_cancellations_status",
        "run_cancellations",
        ["status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "run_cancellation_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cancellation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("evidence_code", sa.String(length=100), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("related_evidence_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("superseded_status", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('request_accepted', 'call_cancelled_before_effect', "
            "'effect_committed', 'cancelled_no_effect', 'partial_effect', "
            "'effect_completed', 'outcome_unknown', 'execution_failed', "
            "'late_effect_evidence', 'evidence_conflict')",
            name="ck_run_cancellation_events_type",
        ),
        sa.CheckConstraint(
            "source_type IN ('owner_action', 'cancellation_api', 'current_worker', "
            "'reconciliation_worker', 'database_verification')",
            name="ck_run_cancellation_events_source",
        ),
        sa.CheckConstraint(
            "evidence_code IN ("
            "'OWNER_REQUEST_COMMITTED', 'CANCEL_WON_EFFECT_RACE', "
            "'EFFECT_COMMIT_WON_CANCELLATION_RACE', "
            "'EFFECT_COMMITTED_BEFORE_CANCELLATION', "
            "'CANCELLATION_OBSERVED_AT_EFFECT_GATE', 'VALIDATED_TOOL_OUTPUT', "
            "'LEASE_EXPIRED_AFTER_EFFECT_COMMIT', "
            "'HANDLER_FAILURE_AFTER_CANCELLATION', 'NO_EFFECT_VERIFIED', "
            "'PARTIAL_EFFECT_VERIFIED', 'FULL_EFFECT_VERIFIED', "
            "'EXECUTION_FAILED_NO_EFFECT_VERIFIED', "
            "'CANCELLATION_OUTCOME_UNKNOWN', 'CONFLICTING_SUCCESS_EVIDENCE', "
            "'SUCCESS_CONFLICTS_WITH_FINAL_NO_EFFECT', "
            "'NO_EFFECT_CONFLICTS_WITH_SUCCESS', "
            "'EVIDENCE_ADDED_TO_OPEN_CONFLICT', 'TASK_NOT_FOUND_BY_SOURCE_CALL', "
            "'FILE_NOT_FOUND_AT_SAFE_PATH')",
            name="ck_run_cancellation_events_evidence_code",
        ),
        sa.ForeignKeyConstraint(
            ["cancellation_id"],
            ["run_cancellations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cancellation_id",
            "sequence_no",
            name="uq_run_cancellation_events_sequence",
        ),
    )
    op.create_index(
        "ix_run_cancellation_events_cancellation",
        "run_cancellation_events",
        ["cancellation_id", "sequence_no"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    tool_call_columns = {column["name"] for column in sa.inspect(bind).get_columns("tool_calls")}
    unsafe = bind.execute(
        sa.text(
            "SELECT "
            "(SELECT count(*) FROM runs WHERE status IN "
            "('cancellation_requested', 'cancellation_uncertain')) + "
            "(SELECT count(*) FROM plan_steps WHERE status = 'outcome_unknown') + "
            "(SELECT count(*) FROM tool_calls WHERE status = 'outcome_unknown')"
        )
    ).scalar_one()
    if unsafe:
        raise RuntimeError("cannot downgrade while FC-002-only states exist")

    op.drop_index(
        "ix_run_cancellation_events_cancellation",
        table_name="run_cancellation_events",
    )
    op.drop_table("run_cancellation_events")
    op.drop_index("ix_run_cancellations_status", table_name="run_cancellations")
    op.drop_table("run_cancellations")

    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_constraint("ck_tool_calls_effect_commit", type_="check")
        if "effect_commit_worker_id" in tool_call_columns:
            batch_op.drop_column("effect_commit_worker_id")
        batch_op.drop_column("effect_commit_token")
        batch_op.drop_column("effect_committed_at")
        batch_op.drop_constraint("ck_tool_calls_status", type_="check")
        batch_op.create_check_constraint(
            "ck_tool_calls_status",
            "status IN ('pending', 'executing', 'succeeded', 'failed', 'cancelled')",
        )

    with op.batch_alter_table("plan_steps") as batch_op:
        batch_op.drop_constraint("ck_plan_steps_status", type_="check")
        batch_op.create_check_constraint(
            "ck_plan_steps_status",
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'skipped', 'cancelled')",
        )

    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_constraint("ck_runs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_runs_status",
            "status IN ('received', 'planning', 'needs_clarification', 'ready', "
            "'awaiting_approval', 'executing', 'verifying', 'succeeded', "
            "'partially_succeeded', 'failed', 'denied', 'cancelled')",
        )
