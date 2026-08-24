"""Relational records for Awn's core execution schema."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from awn.infrastructure.database import Base

structured_content_type = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True),
    "postgresql",
)


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workspaces: Mapped[list["WorkspaceRecord"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_workspaces_status",
        ),
        Index("ix_workspaces_owner_id", "owner_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    owner: Mapped[UserRecord] = relationship(back_populates="workspaces")
    conversations: Mapped[list["ConversationRecord"]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs: Mapped[list["RunRecord"]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        passive_deletes=True,
        overlaps="conversation,runs",
    )
    tasks: Mapped[list["TaskRecord"]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ConversationRecord(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_conversations_status",
        ),
        UniqueConstraint(
            "id",
            "workspace_id",
            name="uq_conversations_id_workspace",
        ),
        Index("ix_conversations_workspace_status", "workspace_id", "status"),
        Index("ix_conversations_updated_at", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workspace: Mapped[WorkspaceRecord] = relationship(back_populates="conversations")
    messages: Mapped[list["MessageRecord"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs: Mapped[list["RunRecord"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        overlaps="runs,workspace",
    )


class MessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'tool')",
            name="ck_messages_role",
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[list[dict[str, object]]] = mapped_column(
        structured_content_type,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    conversation: Mapped[ConversationRecord] = relationship(back_populates="messages")


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'planning', 'needs_clarification', 'ready', "
            "'awaiting_approval', 'executing', 'cancellation_requested', "
            "'cancellation_uncertain', 'verifying', 'succeeded', "
            "'partially_succeeded', 'failed', 'denied', 'cancelled')",
            name="ck_runs_status",
        ),
        CheckConstraint(
            "risk IN ('low', 'medium', 'high', 'critical')",
            name="ck_runs_risk",
        ),
        CheckConstraint(
            "autonomy_level BETWEEN 0 AND 3",
            name="ck_runs_autonomy_level",
        ),
        CheckConstraint(
            "((status IN ('succeeded', 'partially_succeeded', 'failed', 'denied', "
            "'cancelled')) AND completed_at IS NOT NULL) OR "
            "((status NOT IN ('succeeded', 'partially_succeeded', 'failed', 'denied', "
            "'cancelled')) AND completed_at IS NULL)",
            name="ck_runs_completion",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            ondelete="CASCADE",
            name="fk_runs_conversation_workspace",
        ),
        UniqueConstraint("trace_id", name="uq_runs_trace_id"),
        Index("ix_runs_workspace_status", "workspace_id", "status"),
        Index("ix_runs_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    request_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    trace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workspace: Mapped[WorkspaceRecord] = relationship(
        back_populates="runs",
        overlaps="conversation,runs",
    )
    conversation: Mapped[ConversationRecord] = relationship(
        back_populates="runs",
        overlaps="runs,workspace",
    )
    steps: Mapped[list["PlanStepRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PlanStepRecord.position",
    )
    approvals: Mapped[list["ApprovalRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApprovalRecord.requested_at",
    )
    tool_calls: Mapped[list["ToolCallRecord"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ToolCallRecord.created_at",
    )
    cancellation: Mapped["RunCancellationRecord | None"] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class PlanStepRecord(Base):
    __tablename__ = "plan_steps"
    __table_args__ = (
        CheckConstraint(
            "position >= 0",
            name="ck_plan_steps_position",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'skipped', "
            "'cancelled', 'outcome_unknown')",
            name="ck_plan_steps_status",
        ),
        CheckConstraint(
            "risk IN ('low', 'medium', 'high', 'critical')",
            name="ck_plan_steps_risk",
        ),
        CheckConstraint(
            "(tool_name IS NULL AND operation IS NULL AND tool_input IS NULL) OR "
            "(tool_name IS NOT NULL AND operation IS NOT NULL AND tool_input IS NOT NULL)",
            name="ck_plan_steps_tool_action",
        ),
        UniqueConstraint("run_id", "position", name="uq_plan_steps_run_position"),
        Index("ix_plan_steps_run_status", "run_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    operation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tool_input: Mapped[dict[str, object] | None] = mapped_column(
        structured_content_type,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[RunRecord] = relationship(back_populates="steps")
    tool_call: Mapped["ToolCallRecord | None"] = relationship(
        back_populates="plan_step",
        uselist=False,
    )


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired', 'invalidated', 'consumed')",
            name="ck_approvals_status",
        ),
        CheckConstraint(
            "risk IN ('low', 'medium', 'high', 'critical')",
            name="ck_approvals_risk",
        ),
        UniqueConstraint(
            "run_id",
            "action_fingerprint",
            name="uq_approvals_run_fingerprint",
        ),
        Index("ix_approvals_run_status", "run_id", "status"),
        Index("ix_approvals_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    run: Mapped[RunRecord] = relationship(back_populates="approvals")


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'executing', 'succeeded', 'failed', 'cancelled', "
            "'outcome_unknown')",
            name="ck_tool_calls_status",
        ),
        CheckConstraint(
            "risk IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_calls_risk",
        ),
        UniqueConstraint("plan_step_id", name="uq_tool_calls_plan_step"),
        UniqueConstraint("idempotency_key", name="uq_tool_calls_idempotency_key"),
        Index("ix_tool_calls_run_status", "run_id", "status"),
        Index("ix_tool_calls_queue", "status", "available_at", "lease_expires_at"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_tool_calls_attempts",
        ),
        CheckConstraint(
            "(effect_committed_at IS NULL AND effect_commit_token IS NULL AND "
            "effect_commit_worker_id IS NULL) OR "
            "(effect_committed_at IS NOT NULL AND effect_commit_token IS NOT NULL AND "
            "effect_commit_worker_id IS NOT NULL)",
            name="ck_tool_calls_effect_commit",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_step_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    input: Mapped[dict[str, object]] = mapped_column(
        structured_content_type,
        nullable=False,
    )
    output: Mapped[dict[str, object] | None] = mapped_column(
        structured_content_type,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    effect_committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    effect_commit_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    effect_commit_worker_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[RunRecord] = relationship(back_populates="tool_calls")
    plan_step: Mapped[PlanStepRecord] = relationship(back_populates="tool_call")
    created_task: Mapped["TaskRecord | None"] = relationship(
        back_populates="source_tool_call",
        uselist=False,
    )
    cancellation_events: Mapped[list["RunCancellationEventRecord"]] = relationship(
        back_populates="tool_call",
        passive_deletes=True,
    )


class RunCancellationRecord(Base):
    __tablename__ = "run_cancellations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('accepted', 'uncertain', 'cancelled', 'partially_succeeded', "
            "'completed', 'execution_failed')",
            name="ck_run_cancellations_status",
        ),
        UniqueConstraint("run_id", name="uq_run_cancellations_run_id"),
        Index("ix_run_cancellations_status", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[RunRecord] = relationship(back_populates="cancellation")
    events: Mapped[list["RunCancellationEventRecord"]] = relationship(
        back_populates="cancellation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RunCancellationEventRecord.sequence_no",
    )


class RunCancellationEventRecord(Base):
    __tablename__ = "run_cancellation_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('request_accepted', 'call_cancelled_before_effect', "
            "'effect_committed', 'cancelled_no_effect', 'partial_effect', "
            "'effect_completed', 'outcome_unknown', 'execution_failed', "
            "'late_effect_evidence', 'evidence_conflict')",
            name="ck_run_cancellation_events_type",
        ),
        CheckConstraint(
            "source_type IN ('owner_action', 'cancellation_api', 'current_worker', "
            "'reconciliation_worker', 'database_verification')",
            name="ck_run_cancellation_events_source",
        ),
        CheckConstraint(
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
        UniqueConstraint(
            "cancellation_id",
            "sequence_no",
            name="uq_run_cancellation_events_sequence",
        ),
        Index("ix_run_cancellation_events_cancellation", "cancellation_id", "sequence_no"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    cancellation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("run_cancellations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tool_calls.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_evidence_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    superseded_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    cancellation: Mapped[RunCancellationRecord] = relationship(back_populates="events")
    tool_call: Mapped[ToolCallRecord | None] = relationship(
        back_populates="cancellation_events",
    )


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high')",
            name="ck_tasks_priority",
        ),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_due_at", "due_at"),
        Index("ix_tasks_workspace_status", "workspace_id", "status"),
        UniqueConstraint("source_tool_call_id", name="uq_tasks_source_tool_call"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_tool_call_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tool_calls.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workspace: Mapped[WorkspaceRecord] = relationship(back_populates="tasks")
    source_tool_call: Mapped[ToolCallRecord | None] = relationship(back_populates="created_task")
