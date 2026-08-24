"""Durable tool-call lifecycle models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from awn.domain.runs import Run, RunRisk


class ToolCallStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    plan_step_id: UUID
    tool_name: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=100)
    input: dict[str, object]
    output: dict[str, object] | None = None
    status: ToolCallStatus = ToolCallStatus.PENDING
    risk: RunRisk
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, max_length=100)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=10)
    available_at: datetime
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    effect_committed_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    _validate_timestamps = field_validator(
        "started_at",
        "completed_at",
        "available_at",
        "lease_expires_at",
        "effect_committed_at",
        "created_at",
        "updated_at",
    )(_require_aware)


class LeasedToolCall(BaseModel):
    """A queue item plus the immutable security context required to execute it."""

    model_config = ConfigDict(frozen=True)

    owner_id: UUID
    run: Run
    call: ToolCall
