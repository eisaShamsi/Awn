"""Durable tool-call lifecycle models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from awn.domain.runs import RunRisk


class ToolCallStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    _validate_timestamps = field_validator(
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    )(_require_aware)
