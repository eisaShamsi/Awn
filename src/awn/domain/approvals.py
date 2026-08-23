"""Approval contracts and tamper-evident action fingerprints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from awn.domain.runs import PlanStep, Run, RunRisk


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    CONSUMED = "consumed"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalDecisionOutcome(StrEnum):
    RESOLVED = "resolved"
    ALREADY_RESOLVED = "already_resolved"
    CONFLICT = "conflict"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    PLAN_CHANGED = "plan_changed"
    EXPIRED = "expired"


class ApprovalDecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    note: str | None = Field(default=None, max_length=1_000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        note = value.strip()
        return note or None


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    operation: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    risk: RunRisk
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_note: str | None = Field(default=None, max_length=1_000)
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None

    _validate_timestamps = field_validator(
        "requested_at",
        "expires_at",
        "decided_at",
    )(_require_aware)

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> Self:
        if self.expires_at <= self.requested_at:
            raise ValueError("approval expiry must follow its request time")
        if self.status is ApprovalStatus.PENDING and self.decided_at is not None:
            raise ValueError("pending approvals cannot have a decision timestamp")
        if self.status is not ApprovalStatus.PENDING and self.decided_at is None:
            raise ValueError("resolved approvals require a decision timestamp")
        return self


class ApprovalDecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: ApprovalDecisionOutcome
    approval: ApprovalRequest
    run: Run


def action_fingerprint(
    run: Run,
    steps: tuple[PlanStep, ...] | list[PlanStep],
    *,
    operation: str,
) -> str:
    """Hash exactly what an approval authorizes using a stable canonical encoding."""

    ordered_steps = sorted(steps, key=lambda step: (step.position, str(step.id)))
    payload = {
        "schema": "awn.approval.v1",
        "operation": operation,
        "run": {
            "id": str(run.id),
            "workspace_id": str(run.workspace_id),
            "conversation_id": str(run.conversation_id),
            "autonomy_level": run.autonomy_level,
            "risk": run.risk.value,
        },
        "steps": [
            {
                "id": str(step.id),
                "position": step.position,
                "title": step.title,
                "risk": step.risk.value,
                "requires_approval": step.requires_approval,
                "tool_name": step.tool_name,
                "operation": step.operation,
                "tool_input": step.tool_input,
            }
            for step in ordered_steps
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
