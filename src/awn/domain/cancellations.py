"""Truthful, durable run-cancellation models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    CANCELLED = "cancelled"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    COMPLETED = "completed"
    EXECUTION_FAILED = "execution_failed"


class CancellationDecision(StrEnum):
    ACCEPTED = "accepted"
    ALREADY_REQUESTED = "already_requested"
    TOO_LATE = "too_late"
    NOT_CANCELLABLE = "not_cancellable"


class CancellationEventType(StrEnum):
    REQUEST_ACCEPTED = "request_accepted"
    CALL_CANCELLED_BEFORE_EFFECT = "call_cancelled_before_effect"
    EFFECT_COMMITTED = "effect_committed"
    CANCELLED_NO_EFFECT = "cancelled_no_effect"
    PARTIAL_EFFECT = "partial_effect"
    EFFECT_COMPLETED = "effect_completed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    EXECUTION_FAILED = "execution_failed"
    LATE_EFFECT_EVIDENCE = "late_effect_evidence"
    EVIDENCE_CONFLICT = "evidence_conflict"


class CancellationEvidenceSource(StrEnum):
    OWNER_ACTION = "owner_action"
    CANCELLATION_API = "cancellation_api"
    CURRENT_WORKER = "current_worker"
    RECONCILIATION_WORKER = "reconciliation_worker"
    DATABASE_VERIFICATION = "database_verification"


class CancellationEvidenceCode(StrEnum):
    """Closed, non-secret evidence vocabulary exposed by the cancellation API."""

    OWNER_REQUEST_COMMITTED = "OWNER_REQUEST_COMMITTED"
    CANCEL_WON_EFFECT_RACE = "CANCEL_WON_EFFECT_RACE"
    EFFECT_COMMIT_WON_CANCELLATION_RACE = "EFFECT_COMMIT_WON_CANCELLATION_RACE"
    EFFECT_COMMITTED_BEFORE_CANCELLATION = "EFFECT_COMMITTED_BEFORE_CANCELLATION"
    CANCELLATION_OBSERVED_AT_EFFECT_GATE = "CANCELLATION_OBSERVED_AT_EFFECT_GATE"
    VALIDATED_TOOL_OUTPUT = "VALIDATED_TOOL_OUTPUT"
    LEASE_EXPIRED_AFTER_EFFECT_COMMIT = "LEASE_EXPIRED_AFTER_EFFECT_COMMIT"
    HANDLER_FAILURE_AFTER_CANCELLATION = "HANDLER_FAILURE_AFTER_CANCELLATION"
    NO_EFFECT_VERIFIED = "NO_EFFECT_VERIFIED"
    PARTIAL_EFFECT_VERIFIED = "PARTIAL_EFFECT_VERIFIED"
    FULL_EFFECT_VERIFIED = "FULL_EFFECT_VERIFIED"
    EXECUTION_FAILED_NO_EFFECT_VERIFIED = "EXECUTION_FAILED_NO_EFFECT_VERIFIED"
    CANCELLATION_OUTCOME_UNKNOWN = "CANCELLATION_OUTCOME_UNKNOWN"
    CONFLICTING_SUCCESS_EVIDENCE = "CONFLICTING_SUCCESS_EVIDENCE"
    SUCCESS_CONFLICTS_WITH_FINAL_NO_EFFECT = "SUCCESS_CONFLICTS_WITH_FINAL_NO_EFFECT"
    NO_EFFECT_CONFLICTS_WITH_SUCCESS = "NO_EFFECT_CONFLICTS_WITH_SUCCESS"
    EVIDENCE_ADDED_TO_OPEN_CONFLICT = "EVIDENCE_ADDED_TO_OPEN_CONFLICT"
    TASK_NOT_FOUND_BY_SOURCE_CALL = "TASK_NOT_FOUND_BY_SOURCE_CALL"
    FILE_NOT_FOUND_AT_SAFE_PATH = "FILE_NOT_FOUND_AT_SAFE_PATH"


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value


class CancellationEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    cancellation_id: UUID
    sequence_no: int = Field(ge=1)
    tool_call_id: UUID | None = None
    event_type: CancellationEventType
    source_type: CancellationEvidenceSource
    evidence_code: CancellationEvidenceCode
    evidence_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    related_evidence_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    superseded_status: str | None = Field(default=None, max_length=32)
    occurred_at: datetime | None = None
    observed_at: datetime

    _validate_timestamps = field_validator("occurred_at", "observed_at")(_require_aware)


class RunCancellation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    requested_by: UUID
    status: CancellationStatus
    reason_code: str = Field(min_length=1, max_length=100)
    received_at: datetime
    requested_at: datetime
    resolved_at: datetime | None = None
    updated_at: datetime
    events: tuple[CancellationEvent, ...] = ()

    _validate_timestamps = field_validator(
        "received_at",
        "requested_at",
        "resolved_at",
        "updated_at",
    )(_require_aware)


class CancellationRequestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: CancellationDecision
    received_at: datetime
    run_status: str
    cancellation: RunCancellation | None = None

    _validate_received_at = field_validator("received_at")(_require_aware)
