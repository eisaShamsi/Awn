"""Agent run and plan-step lifecycle models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RunStatus(StrEnum):
    RECEIVED = "received"
    PLANNING = "planning"
    NEEDS_CLARIFICATION = "needs_clarification"
    READY = "ready"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class RunRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_message_id: UUID
    autonomy_level: int = Field(default=0, ge=0, le=3)


TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.PARTIALLY_SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.DENIED,
        RunStatus.CANCELLED,
    }
)

ALLOWED_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.RECEIVED: frozenset({RunStatus.PLANNING, RunStatus.CANCELLED}),
    RunStatus.PLANNING: frozenset(
        {
            RunStatus.NEEDS_CLARIFICATION,
            RunStatus.READY,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.NEEDS_CLARIFICATION: frozenset({RunStatus.PLANNING, RunStatus.CANCELLED}),
    RunStatus.READY: frozenset(
        {
            RunStatus.AWAITING_APPROVAL,
            RunStatus.EXECUTING,
            RunStatus.DENIED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.AWAITING_APPROVAL: frozenset({RunStatus.EXECUTING, RunStatus.CANCELLED}),
    RunStatus.EXECUTING: frozenset({RunStatus.VERIFYING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.VERIFYING: frozenset(
        {
            RunStatus.SUCCEEDED,
            RunStatus.PARTIALLY_SUCCEEDED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.PARTIALLY_SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.DENIED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value


class Run(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    request_message_id: UUID | None = None
    trace_id: UUID
    status: RunStatus = RunStatus.RECEIVED
    risk: RunRisk = RunRisk.LOW
    autonomy_level: int = Field(default=0, ge=0, le=3)
    error_code: str | None = Field(default=None, max_length=100)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    _validate_timestamps = field_validator(
        "started_at", "completed_at", "created_at", "updated_at"
    )(_require_aware)

    @model_validator(mode="after")
    def completion_matches_status(self) -> Self:
        if self.status in TERMINAL_RUN_STATUSES and self.completed_at is None:
            raise ValueError("terminal runs require completed_at")
        if self.status not in TERMINAL_RUN_STATUSES and self.completed_at is not None:
            raise ValueError("non-terminal runs cannot have completed_at")
        return self

    def transition_to(self, status: RunStatus, *, at: datetime | None = None) -> Self:
        if status not in ALLOWED_RUN_TRANSITIONS[self.status]:
            raise ValueError(f"invalid run transition: {self.status.value} -> {status.value}")

        changed_at = at or datetime.now(UTC)
        _require_aware(changed_at)
        return self.model_copy(
            update={
                "status": status,
                "started_at": self.started_at
                or (changed_at if status is RunStatus.EXECUTING else None),
                "completed_at": changed_at if status in TERMINAL_RUN_STATUSES else None,
                "updated_at": changed_at,
            }
        )


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    position: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=300)
    status: PlanStepStatus = PlanStepStatus.PENDING
    risk: RunRisk = RunRisk.LOW
    requires_approval: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("title must not be blank")
        return title

    _validate_timestamps = field_validator("created_at", "updated_at")(_require_aware)
