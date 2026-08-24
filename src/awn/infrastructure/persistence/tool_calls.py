"""Durable and idempotent persistence for approved tool execution."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.approvals import ApprovalStatus, action_fingerprint
from awn.domain.cancellations import (
    CancellationEventType,
    CancellationEvidenceCode,
    CancellationEvidenceSource,
    CancellationStatus,
)
from awn.domain.runs import PlanStepStatus, Run, RunRisk, RunStatus
from awn.domain.tool_calls import LeasedToolCall, ToolCall, ToolCallStatus
from awn.infrastructure.persistence.cancellations import (
    _append_event,
    _apply_cancellation_truth,
)
from awn.infrastructure.persistence.models import (
    ApprovalRecord,
    PlanStepRecord,
    RunCancellationEventRecord,
    RunCancellationRecord,
    RunRecord,
    ToolCallRecord,
    WorkspaceRecord,
)
from awn.infrastructure.persistence.runs import _aware, _run, _step
from awn.tools.contracts import EffectVerificationStatus, ToolContext
from awn.tools.registry import InvalidToolOutputError, ToolRegistry

logger = logging.getLogger(__name__)


def _tool_call(record: ToolCallRecord) -> ToolCall:
    created_at = _aware(record.created_at)
    updated_at = _aware(record.updated_at)
    assert created_at is not None
    assert updated_at is not None
    return ToolCall(
        id=record.id,
        run_id=record.run_id,
        plan_step_id=record.plan_step_id,
        tool_name=record.tool_name,
        operation=record.operation,
        input=record.input,
        output=record.output,
        status=ToolCallStatus(record.status),
        risk=RunRisk(record.risk),
        idempotency_key=record.idempotency_key,
        error_code=record.error_code,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        available_at=_aware(record.available_at),
        lease_expires_at=_aware(record.lease_expires_at),
        started_at=_aware(record.started_at),
        effect_committed_at=_aware(record.effect_committed_at),
        completed_at=_aware(record.completed_at),
        created_at=created_at,
        updated_at=updated_at,
    )


def _idempotency_key(action_fingerprint_value: str, step: PlanStepRecord) -> str:
    value = (
        f"awn.tool-call.v1:{action_fingerprint_value}:{step.id}:{step.tool_name}.{step.operation}"
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _evidence_fingerprint(value: dict[str, object]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return _canonical_json_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        aware = _aware(value)
        assert aware is not None
        return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _canonical_json_value(value.value)
    if isinstance(value, dict):
        return {str(key): _canonical_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    return value


def _normalized_model_output(model: BaseModel) -> dict[str, object]:
    normalized = _canonical_json_value(model)
    assert isinstance(normalized, dict)
    return normalized


class SqlAlchemyToolCallRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registry: ToolRegistry | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._clock = clock or (lambda: datetime.now(UTC))

    def _observation_time(self, *causal_times: datetime | None) -> datetime:
        values = [self._clock()]
        values.extend(aware for value in causal_times if (aware := _aware(value)) is not None)
        return max(values)

    @staticmethod
    def _scoped_call_statement(
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ):
        return (
            select(ToolCallRecord)
            .join(RunRecord, RunRecord.id == ToolCallRecord.run_id)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
            )
        )

    @staticmethod
    def _steps(session: Session, run_id: UUID) -> tuple[PlanStepRecord, ...]:
        statement = (
            select(PlanStepRecord)
            .where(PlanStepRecord.run_id == run_id)
            .order_by(PlanStepRecord.position, PlanStepRecord.id)
        )
        return tuple(session.scalars(statement))

    def prepare_approved(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        allowed_step_ids: frozenset[UUID],
        *,
        started_at: datetime,
        max_attempts: int,
    ) -> Iterable[ToolCall] | None:
        approval_statement = (
            select(ApprovalRecord)
            .join(RunRecord, RunRecord.id == ApprovalRecord.run_id)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
                RunRecord.id == run_id,
                ApprovalRecord.id == approval_id,
            )
            .with_for_update()
        )
        with self._session_factory.begin() as session:
            approval = session.scalar(approval_statement)
            if approval is None:
                return None
            run_record = session.get(RunRecord, run_id)
            assert run_record is not None

            existing_statement = (
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == run_id)
                .order_by(ToolCallRecord.created_at, ToolCallRecord.id)
            )
            if ApprovalStatus(approval.status) is ApprovalStatus.CONSUMED:
                return tuple(_tool_call(record) for record in session.scalars(existing_statement))
            if ApprovalStatus(approval.status) is not ApprovalStatus.APPROVED:
                return None
            if RunStatus(run_record.status) is not RunStatus.READY:
                return None

            step_records = self._steps(session, run_id)
            current_fingerprint = action_fingerprint(
                _run(run_record),
                [_step(record) for record in step_records],
                operation=approval.operation,
            )
            action_steps = tuple(record for record in step_records if record.tool_name is not None)
            if (
                current_fingerprint != approval.action_fingerprint
                or frozenset(record.id for record in action_steps) != allowed_step_ids
            ):
                approval.status = ApprovalStatus.INVALIDATED.value
                approval.decision_note = "ACTION_CHANGED_BEFORE_EXECUTION"
                approval.decided_at = started_at
                run_record.status = RunStatus.CANCELLED.value
                run_record.completed_at = started_at
                run_record.updated_at = started_at
                return None

            call_records = []
            for step in action_steps:
                assert step.tool_name is not None
                assert step.operation is not None
                assert step.tool_input is not None
                call = ToolCallRecord(
                    id=uuid4(),
                    run_id=run_id,
                    plan_step_id=step.id,
                    tool_name=step.tool_name,
                    operation=step.operation,
                    input=step.tool_input,
                    output=None,
                    status=ToolCallStatus.PENDING.value,
                    risk=step.risk,
                    idempotency_key=_idempotency_key(approval.action_fingerprint, step),
                    error_code=None,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    available_at=started_at,
                    lease_owner=None,
                    lease_expires_at=None,
                    started_at=None,
                    effect_committed_at=None,
                    effect_commit_token=None,
                    effect_commit_worker_id=None,
                    completed_at=None,
                    created_at=started_at,
                    updated_at=started_at,
                )
                call_records.append(call)
                session.add(call)
                step.status = PlanStepStatus.PENDING.value
                step.updated_at = started_at

            approval.status = ApprovalStatus.CONSUMED.value
            executing = _run(run_record).transition_to(
                RunStatus.EXECUTING,
                at=started_at,
            )
            run_record.status = executing.status.value
            run_record.started_at = executing.started_at
            run_record.updated_at = executing.updated_at
            session.flush()
            return tuple(_tool_call(record) for record in call_records)

    def claim_next(
        self,
        worker_id: str,
        *,
        claimed_at: datetime,
        lease_seconds: int,
    ) -> LeasedToolCall | None:
        """Atomically lease one available item, including recovery of an expired lease."""

        pending = and_(
            ToolCallRecord.status == ToolCallStatus.PENDING.value,
            ToolCallRecord.available_at <= claimed_at,
            ToolCallRecord.attempt_count < ToolCallRecord.max_attempts,
        )
        abandoned = and_(
            ToolCallRecord.status == ToolCallStatus.EXECUTING.value,
            ToolCallRecord.lease_expires_at.is_not(None),
            ToolCallRecord.lease_expires_at <= claimed_at,
        )
        run_statement = (
            select(RunRecord)
            .join(ToolCallRecord, RunRecord.id == ToolCallRecord.run_id)
            .join(PlanStepRecord, PlanStepRecord.id == ToolCallRecord.plan_step_id)
            .where(
                RunRecord.status == RunStatus.EXECUTING.value,
                or_(pending, abandoned),
                ~select(RunCancellationRecord.id)
                .where(RunCancellationRecord.run_id == RunRecord.id)
                .exists(),
            )
            .order_by(
                ToolCallRecord.available_at,
                ToolCallRecord.created_at,
                PlanStepRecord.position,
                ToolCallRecord.id,
            )
            .limit(1)
            .with_for_update(skip_locked=True, of=RunRecord)
        )
        with self._session_factory.begin() as session:
            run_record = session.scalar(run_statement)
            if run_record is None:
                return None
            record = session.scalar(
                select(ToolCallRecord)
                .join(PlanStepRecord, PlanStepRecord.id == ToolCallRecord.plan_step_id)
                .where(
                    ToolCallRecord.run_id == run_record.id,
                    or_(pending, abandoned),
                )
                .order_by(
                    ToolCallRecord.available_at,
                    ToolCallRecord.created_at,
                    PlanStepRecord.position,
                    ToolCallRecord.id,
                )
                .limit(1)
                .with_for_update(of=ToolCallRecord)
            )
            if record is None:
                return None
            was_pending = ToolCallStatus(record.status) is ToolCallStatus.PENDING
            if was_pending:
                record.attempt_count += 1
            record.status = ToolCallStatus.EXECUTING.value
            record.lease_owner = worker_id
            record.lease_expires_at = claimed_at + timedelta(seconds=lease_seconds)
            record.started_at = record.started_at or claimed_at
            record.updated_at = claimed_at

            workspace = session.get(WorkspaceRecord, run_record.workspace_id)
            assert workspace is not None
            step = session.scalar(
                select(PlanStepRecord)
                .where(PlanStepRecord.id == record.plan_step_id)
                .with_for_update(of=PlanStepRecord)
            )
            assert step is not None
            step.status = PlanStepStatus.IN_PROGRESS.value
            step.updated_at = claimed_at
            session.flush()
            return LeasedToolCall(
                owner_id=workspace.owner_id,
                run=_run(run_record),
                call=_tool_call(record),
            )

    def _lock_scoped_call(
        self,
        session: Session,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
    ) -> tuple[RunRecord, ToolCallRecord, tuple[ToolCallRecord, ...]] | None:
        run = session.scalar(
            select(RunRecord)
            .join(ToolCallRecord, ToolCallRecord.run_id == RunRecord.id)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
                ToolCallRecord.id == call_id,
            )
            .with_for_update(of=RunRecord)
        )
        if run is None:
            return None
        calls = tuple(
            session.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == run.id)
                .order_by(ToolCallRecord.id)
                .with_for_update(of=ToolCallRecord)
            )
        )
        call = next((item for item in calls if item.id == call_id), None)
        return (run, call, calls) if call is not None else None

    @staticmethod
    def _lock_step(
        session: Session,
        step_id: UUID,
    ) -> PlanStepRecord:
        step = session.scalar(
            select(PlanStepRecord)
            .where(PlanStepRecord.id == step_id)
            .with_for_update(of=PlanStepRecord)
        )
        assert step is not None
        return step

    @staticmethod
    def _lock_steps(session: Session, run_id: UUID) -> tuple[PlanStepRecord, ...]:
        return tuple(
            session.scalars(
                select(PlanStepRecord)
                .where(PlanStepRecord.run_id == run_id)
                .order_by(PlanStepRecord.position, PlanStepRecord.id)
                .with_for_update(of=PlanStepRecord)
            )
        )

    def commit_effect(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        *,
        worker_id: str,
    ) -> UUID | None:
        """Commit the last controllable boundary before invoking a tool handler."""

        with self._session_factory.begin() as session:
            locked = self._lock_scoped_call(
                session,
                owner_id,
                workspace_id,
                conversation_id,
                call_id,
            )
            if locked is None:
                return None
            run, call, _ = locked
            cancellation = session.scalar(
                select(RunCancellationRecord)
                .where(RunCancellationRecord.run_id == run.id)
                .with_for_update(of=RunCancellationRecord)
            )
            cancellation_step = (
                self._lock_step(session, call.plan_step_id) if cancellation is not None else None
            )
            causal_floor = _aware(call.started_at) or _aware(call.updated_at)
            committed_at = self._clock()
            if causal_floor is not None:
                committed_at = max(committed_at, causal_floor)
            if cancellation is not None:
                if (
                    ToolCallStatus(call.status) is ToolCallStatus.EXECUTING
                    and call.effect_committed_at is None
                ):
                    call.status = ToolCallStatus.CANCELLED.value
                    call.error_code = "CANCELLED_BEFORE_EFFECT"
                    call.lease_owner = None
                    call.lease_expires_at = None
                    call.completed_at = committed_at
                    call.updated_at = committed_at
                    assert cancellation_step is not None
                    step = cancellation_step
                    step.status = PlanStepStatus.CANCELLED.value
                    step.updated_at = committed_at
                    _append_event(
                        session,
                        cancellation,
                        CancellationEventType.CALL_CANCELLED_BEFORE_EFFECT,
                        CancellationEvidenceSource.CURRENT_WORKER,
                        CancellationEvidenceCode.CANCELLATION_OBSERVED_AT_EFFECT_GATE,
                        observed_at=committed_at,
                        tool_call_id=call.id,
                    )
                    _apply_cancellation_truth(session, run, cancellation, at=committed_at)
                return None
            if (
                ToolCallStatus(call.status) is not ToolCallStatus.EXECUTING
                or call.lease_owner != worker_id
            ):
                return None
            if call.effect_commit_token is None:
                call.effect_commit_token = uuid4()
                call.effect_commit_worker_id = worker_id
                call.effect_committed_at = committed_at
                call.updated_at = committed_at
            elif call.effect_commit_worker_id != worker_id:
                # An expired lease may be recovered only while no cancellation
                # exists. Transfer the durable authority to the newly leased
                # worker so the previous process can no longer submit evidence.
                call.effect_commit_worker_id = worker_id
                call.updated_at = committed_at
            assert call.effect_commit_token is not None
            return call.effect_commit_token

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Iterable[ToolCall] | None:
        run_statement = (
            select(RunRecord.id)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
                RunRecord.id == run_id,
            )
        )
        call_statement = (
            self._scoped_call_statement(
                owner_id,
                workspace_id,
                conversation_id,
            )
            .where(ToolCallRecord.run_id == run_id)
            .order_by(
                ToolCallRecord.created_at,
                ToolCallRecord.id,
            )
        )
        with self._session_factory() as session:
            if session.scalar(run_statement) is None:
                return None
            return tuple(_tool_call(record) for record in session.scalars(call_statement))

    @staticmethod
    def _cancellation(session: Session, run_id: UUID) -> RunCancellationRecord | None:
        return session.scalar(
            select(RunCancellationRecord)
            .where(RunCancellationRecord.run_id == run_id)
            .with_for_update(of=RunCancellationRecord)
        )

    @staticmethod
    def _latest_evidence_fingerprint(
        session: Session,
        cancellation_id: UUID,
        call_id: UUID,
    ) -> str | None:
        return session.scalar(
            select(RunCancellationEventRecord.evidence_fingerprint)
            .where(
                RunCancellationEventRecord.cancellation_id == cancellation_id,
                RunCancellationEventRecord.tool_call_id == call_id,
                RunCancellationEventRecord.evidence_fingerprint.is_not(None),
                RunCancellationEventRecord.event_type
                != CancellationEventType.EVIDENCE_CONFLICT.value,
            )
            .order_by(RunCancellationEventRecord.sequence_no.desc())
            .limit(1)
        )

    @staticmethod
    def _has_evidence(
        session: Session,
        cancellation_id: UUID,
        call_id: UUID,
        evidence_fingerprint: str,
    ) -> bool:
        return (
            session.scalar(
                select(RunCancellationEventRecord.id).where(
                    RunCancellationEventRecord.cancellation_id == cancellation_id,
                    RunCancellationEventRecord.tool_call_id == call_id,
                    RunCancellationEventRecord.evidence_fingerprint == evidence_fingerprint,
                )
            )
            is not None
        )

    @staticmethod
    def _has_open_evidence_conflict(
        session: Session,
        cancellation_id: UUID,
        call_id: UUID,
    ) -> bool:
        return (
            session.scalar(
                select(RunCancellationEventRecord.id).where(
                    RunCancellationEventRecord.cancellation_id == cancellation_id,
                    RunCancellationEventRecord.tool_call_id == call_id,
                    RunCancellationEventRecord.event_type
                    == CancellationEventType.EVIDENCE_CONFLICT.value,
                )
            )
            is not None
        )

    def _mark_evidence_conflict(
        self,
        session: Session,
        run: RunRecord,
        call: ToolCallRecord,
        step: PlanStepRecord,
        cancellation: RunCancellationRecord,
        *,
        evidence_fingerprint: str,
        evidence_code: CancellationEvidenceCode,
        source: CancellationEvidenceSource,
        observed_at: datetime,
    ) -> None:
        previous = call.status
        related = self._latest_evidence_fingerprint(
            session,
            cancellation.id,
            call.id,
        )
        duplicate = session.scalar(
            select(RunCancellationEventRecord.id).where(
                RunCancellationEventRecord.cancellation_id == cancellation.id,
                RunCancellationEventRecord.tool_call_id == call.id,
                RunCancellationEventRecord.event_type
                == CancellationEventType.EVIDENCE_CONFLICT.value,
                RunCancellationEventRecord.evidence_fingerprint == evidence_fingerprint,
            )
        )
        if duplicate is not None:
            return
        call.status = ToolCallStatus.OUTCOME_UNKNOWN.value
        call.error_code = "EVIDENCE_CONFLICT"
        call.completed_at = None
        call.lease_owner = None
        call.lease_expires_at = None
        call.updated_at = observed_at
        step.status = PlanStepStatus.OUTCOME_UNKNOWN.value
        step.updated_at = observed_at
        _append_event(
            session,
            cancellation,
            CancellationEventType.EVIDENCE_CONFLICT,
            source,
            evidence_code,
            observed_at=observed_at,
            tool_call_id=call.id,
            evidence_fingerprint=evidence_fingerprint,
            related_evidence_fingerprint=related,
            superseded_status=previous,
        )
        _apply_cancellation_truth(session, run, cancellation, at=observed_at)

    def succeed(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        output: dict[str, object],
        *,
        worker_id: str,
        expected_idempotency_key: str,
        expected_input: dict[str, object],
        effect_commit_token: UUID | None = None,
        occurred_at: datetime,
    ) -> Run | None:
        with self._session_factory.begin() as session:
            locked = self._lock_scoped_call(
                session,
                owner_id,
                workspace_id,
                conversation_id,
                call_id,
            )
            if locked is None:
                return None
            run, call, _ = locked
            if (
                call.idempotency_key != expected_idempotency_key
                or _evidence_fingerprint(call.input) != _evidence_fingerprint(expected_input)
                or call.effect_commit_token != effect_commit_token
                or call.effect_commit_worker_id != worker_id
                or call.effect_committed_at is None
            ):
                return None

            if self._registry is None:
                return None
            try:
                validated_output = self._registry.validate_output(
                    call.tool_name,
                    call.operation,
                    output,
                )
            except InvalidToolOutputError:
                return None
            output = _normalized_model_output(validated_output)
            status = ToolCallStatus(call.status)
            fingerprint = _evidence_fingerprint(output)
            cancellation = self._cancellation(session, run.id)
            locked_steps = self._lock_steps(session, run.id)
            steps_by_id = {step.id: step for step in locked_steps}
            observed_at = self._observation_time(
                occurred_at,
                call.effect_committed_at,
                call.updated_at,
                cancellation.requested_at if cancellation is not None else None,
                cancellation.updated_at if cancellation is not None else None,
            )
            if (
                cancellation is not None
                and status is ToolCallStatus.OUTCOME_UNKNOWN
                and self._has_open_evidence_conflict(session, cancellation.id, call.id)
            ):
                if not self._has_evidence(session, cancellation.id, call.id, fingerprint):
                    self._mark_evidence_conflict(
                        session,
                        run,
                        call,
                        steps_by_id[call.plan_step_id],
                        cancellation,
                        evidence_fingerprint=fingerprint,
                        evidence_code=CancellationEvidenceCode.EVIDENCE_ADDED_TO_OPEN_CONFLICT,
                        source=CancellationEvidenceSource.CURRENT_WORKER,
                        observed_at=observed_at,
                    )
                return None
            if status is ToolCallStatus.SUCCEEDED:
                if call.output is not None and _evidence_fingerprint(call.output) == fingerprint:
                    return None
                if cancellation is not None:
                    self._mark_evidence_conflict(
                        session,
                        run,
                        call,
                        steps_by_id[call.plan_step_id],
                        cancellation,
                        evidence_fingerprint=fingerprint,
                        evidence_code=CancellationEvidenceCode.CONFLICTING_SUCCESS_EVIDENCE,
                        source=CancellationEvidenceSource.CURRENT_WORKER,
                        observed_at=observed_at,
                    )
                return None
            if status in {ToolCallStatus.FAILED, ToolCallStatus.CANCELLED}:
                if cancellation is not None:
                    self._mark_evidence_conflict(
                        session,
                        run,
                        call,
                        steps_by_id[call.plan_step_id],
                        cancellation,
                        evidence_fingerprint=fingerprint,
                        evidence_code=(
                            CancellationEvidenceCode.SUCCESS_CONFLICTS_WITH_FINAL_NO_EFFECT
                        ),
                        source=CancellationEvidenceSource.CURRENT_WORKER,
                        observed_at=observed_at,
                    )
                return None
            if status not in {
                ToolCallStatus.EXECUTING,
                ToolCallStatus.PENDING,
                ToolCallStatus.OUTCOME_UNKNOWN,
            }:
                return None
            if cancellation is None and (
                status is not ToolCallStatus.EXECUTING or call.lease_owner != worker_id
            ):
                return None

            was_late = status is not ToolCallStatus.EXECUTING or cancellation is not None
            call.status = ToolCallStatus.SUCCEEDED.value
            call.output = output
            call.error_code = None
            call.completed_at = observed_at
            call.lease_owner = None
            call.lease_expires_at = None
            call.updated_at = observed_at
            step = steps_by_id[call.plan_step_id]
            step.status = PlanStepStatus.SUCCEEDED.value
            step.updated_at = observed_at

            if cancellation is not None:
                _append_event(
                    session,
                    cancellation,
                    CancellationEventType.LATE_EFFECT_EVIDENCE
                    if was_late
                    else CancellationEventType.EFFECT_COMPLETED,
                    CancellationEvidenceSource.CURRENT_WORKER,
                    CancellationEvidenceCode.VALIDATED_TOOL_OUTPUT,
                    observed_at=observed_at,
                    occurred_at=occurred_at,
                    tool_call_id=call.id,
                    evidence_fingerprint=fingerprint,
                )
                _apply_cancellation_truth(session, run, cancellation, at=observed_at)
                session.flush()
                return _run(run)

            session.flush()
            statuses = tuple(
                session.scalars(
                    select(ToolCallRecord.status).where(ToolCallRecord.run_id == call.run_id)
                )
            )
            if statuses and all(item == ToolCallStatus.SUCCEEDED.value for item in statuses):
                for plan_step in locked_steps:
                    if PlanStepStatus(plan_step.status) in {
                        PlanStepStatus.PENDING,
                        PlanStepStatus.IN_PROGRESS,
                    }:
                        plan_step.status = PlanStepStatus.SUCCEEDED.value
                        plan_step.updated_at = observed_at
                succeeded = (
                    _run(run)
                    .transition_to(RunStatus.VERIFYING, at=observed_at)
                    .transition_to(RunStatus.SUCCEEDED, at=observed_at)
                )
                run.status = succeeded.status.value
                run.completed_at = succeeded.completed_at
                run.updated_at = succeeded.updated_at
            session.flush()
            return _run(run)

    def reconcile_no_effect(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        *,
        effect_commit_token: UUID,
        expected_idempotency_key: str,
        expected_input: dict[str, object],
        observed_at: datetime,
    ) -> Run | None:
        """Reconcile verified no-effect evidence without invoking a handler."""

        with self._session_factory.begin() as session:
            locked = self._lock_scoped_call(
                session,
                owner_id,
                workspace_id,
                conversation_id,
                call_id,
            )
            if locked is None:
                return None
            run, call, _ = locked
            if (
                call.idempotency_key != expected_idempotency_key
                or _evidence_fingerprint(call.input) != _evidence_fingerprint(expected_input)
                or call.effect_commit_token != effect_commit_token
            ):
                return None
            cancellation = self._cancellation(session, run.id)
            if cancellation is None:
                return None
            locked_steps = self._lock_steps(session, run.id)
            steps_by_id = {step.id: step for step in locked_steps}
            status = ToolCallStatus(call.status)
            if status is ToolCallStatus.EXECUTING and (
                call.lease_expires_at is None or _aware(call.lease_expires_at) > observed_at
            ):
                return None
            if self._registry is None:
                return None
            definition = self._registry.resolve(call.tool_name, call.operation)
            if definition is None or definition.effect_verifier is None:
                return None
            validated_input = self._registry.validate_input(
                call.tool_name,
                call.operation,
                call.input,
            )
            context = ToolContext(
                owner_id=owner_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                run_id=run.id,
                trace_id=run.trace_id,
                tool_call_id=call.id,
                idempotency_key=call.idempotency_key,
            )
            verification = definition.effect_verifier(context, validated_input)
            if verification.status is not EffectVerificationStatus.VERIFIED_NO_EFFECT:
                return None
            recorded_at = self._observation_time(
                observed_at,
                call.effect_committed_at,
                call.updated_at,
                cancellation.requested_at,
                cancellation.updated_at,
            )
            evidence_code = verification.evidence_code
            fingerprint = _evidence_fingerprint(
                {
                    "call_id": str(call.id),
                    "kind": "verified_no_effect",
                    "code": evidence_code.value,
                }
            )
            if status is ToolCallStatus.OUTCOME_UNKNOWN and self._has_open_evidence_conflict(
                session, cancellation.id, call.id
            ):
                if not self._has_evidence(session, cancellation.id, call.id, fingerprint):
                    self._mark_evidence_conflict(
                        session,
                        run,
                        call,
                        steps_by_id[call.plan_step_id],
                        cancellation,
                        evidence_fingerprint=fingerprint,
                        evidence_code=CancellationEvidenceCode.EVIDENCE_ADDED_TO_OPEN_CONFLICT,
                        source=CancellationEvidenceSource.RECONCILIATION_WORKER,
                        observed_at=recorded_at,
                    )
                return _run(run)
            if status is ToolCallStatus.SUCCEEDED:
                self._mark_evidence_conflict(
                    session,
                    run,
                    call,
                    steps_by_id[call.plan_step_id],
                    cancellation,
                    evidence_fingerprint=fingerprint,
                    evidence_code=CancellationEvidenceCode.NO_EFFECT_CONFLICTS_WITH_SUCCESS,
                    source=CancellationEvidenceSource.RECONCILIATION_WORKER,
                    observed_at=recorded_at,
                )
                return _run(run)
            if status in {ToolCallStatus.CANCELLED, ToolCallStatus.FAILED}:
                return _run(run)
            call.status = ToolCallStatus.CANCELLED.value
            call.error_code = "NO_EFFECT_VERIFIED"
            call.completed_at = recorded_at
            call.lease_owner = None
            call.lease_expires_at = None
            call.updated_at = recorded_at
            step = steps_by_id[call.plan_step_id]
            step.status = PlanStepStatus.CANCELLED.value
            step.updated_at = recorded_at
            _append_event(
                session,
                cancellation,
                CancellationEventType.LATE_EFFECT_EVIDENCE,
                CancellationEvidenceSource.RECONCILIATION_WORKER,
                evidence_code,
                observed_at=recorded_at,
                tool_call_id=call.id,
                evidence_fingerprint=fingerprint,
            )
            _apply_cancellation_truth(session, run, cancellation, at=recorded_at)
            return _run(run)

    def reconcile_expired_cancellations(self, *, observed_at: datetime) -> int:
        """Reconcile expired post-boundary calls without re-running their handlers.

        A resource verifier may prove a durable effect and return its validated
        output. Resource absence remains unknown unless a tool-specific verifier
        can provide exclusive, durable no-effect evidence.
        """

        candidate_ids: tuple[UUID, ...]
        with self._session_factory() as session:
            candidate_ids = tuple(
                session.scalars(
                    select(RunRecord.id)
                    .join(
                        RunCancellationRecord,
                        RunCancellationRecord.run_id == RunRecord.id,
                    )
                    .join(ToolCallRecord, ToolCallRecord.run_id == RunRecord.id)
                    .where(
                        RunCancellationRecord.status.in_(
                            (
                                CancellationStatus.ACCEPTED.value,
                                CancellationStatus.UNCERTAIN.value,
                            )
                        ),
                        ToolCallRecord.effect_committed_at.is_not(None),
                        or_(
                            and_(
                                ToolCallRecord.status == ToolCallStatus.EXECUTING.value,
                                ToolCallRecord.lease_expires_at.is_not(None),
                                ToolCallRecord.lease_expires_at <= observed_at,
                            ),
                            ToolCallRecord.status == ToolCallStatus.OUTCOME_UNKNOWN.value,
                        ),
                    )
                    .distinct()
                )
            )

        reconciled = 0
        for run_id in candidate_ids:
            with self._session_factory.begin() as session:
                run = session.scalar(
                    select(RunRecord).where(RunRecord.id == run_id).with_for_update(of=RunRecord)
                )
                if run is None:
                    continue
                calls = tuple(
                    session.scalars(
                        select(ToolCallRecord)
                        .where(ToolCallRecord.run_id == run_id)
                        .order_by(ToolCallRecord.id)
                        .with_for_update(of=ToolCallRecord)
                    )
                )
                cancellation = self._cancellation(session, run_id)
                if cancellation is None:
                    continue
                locked_steps = self._lock_steps(session, run_id)
                steps_by_id = {step.id: step for step in locked_steps}
                for call in calls:
                    call_status = ToolCallStatus(call.status)
                    expired_execution = (
                        call_status is ToolCallStatus.EXECUTING
                        and call.effect_committed_at is not None
                        and call.lease_expires_at is not None
                        and _aware(call.lease_expires_at) <= observed_at
                    )
                    if not expired_execution and call_status is not ToolCallStatus.OUTCOME_UNKNOWN:
                        continue
                    if call.effect_committed_at is None or self._registry is None:
                        continue

                    definition = self._registry.resolve(call.tool_name, call.operation)
                    verification = None
                    if definition is not None and definition.effect_verifier is not None:
                        try:
                            validated_input = self._registry.validate_input(
                                call.tool_name,
                                call.operation,
                                call.input,
                            )
                            owner_id = session.scalar(
                                select(WorkspaceRecord.owner_id).where(
                                    WorkspaceRecord.id == run.workspace_id
                                )
                            )
                            assert owner_id is not None
                            context = ToolContext(
                                owner_id=owner_id,
                                workspace_id=run.workspace_id,
                                conversation_id=run.conversation_id,
                                run_id=run.id,
                                trace_id=run.trace_id,
                                tool_call_id=call.id,
                                idempotency_key=call.idempotency_key,
                            )
                            verification = definition.effect_verifier(
                                context,
                                validated_input,
                            )
                        except Exception as error:
                            logger.warning(
                                "Effect verification for call %s failed (%s)",
                                call.id,
                                type(error).__name__,
                            )

                    recorded_at = self._observation_time(
                        observed_at,
                        call.effect_committed_at,
                        call.updated_at,
                        cancellation.requested_at,
                        cancellation.updated_at,
                    )
                    if (
                        verification is not None
                        and verification.status is EffectVerificationStatus.EFFECT_PRESENT
                        and verification.output is not None
                    ):
                        try:
                            validated_output = self._registry.validate_output(
                                call.tool_name,
                                call.operation,
                                verification.output.model_dump(mode="json"),
                            )
                        except InvalidToolOutputError:
                            verification = None
                        else:
                            output = _normalized_model_output(validated_output)
                            fingerprint = _evidence_fingerprint(output)
                            if self._has_open_evidence_conflict(
                                session,
                                cancellation.id,
                                call.id,
                            ):
                                if not self._has_evidence(
                                    session,
                                    cancellation.id,
                                    call.id,
                                    fingerprint,
                                ):
                                    self._mark_evidence_conflict(
                                        session,
                                        run,
                                        call,
                                        steps_by_id[call.plan_step_id],
                                        cancellation,
                                        evidence_fingerprint=fingerprint,
                                        evidence_code=(
                                            CancellationEvidenceCode.EVIDENCE_ADDED_TO_OPEN_CONFLICT
                                        ),
                                        source=(CancellationEvidenceSource.RECONCILIATION_WORKER),
                                        observed_at=recorded_at,
                                    )
                                    reconciled += 1
                                continue
                            call.status = ToolCallStatus.SUCCEEDED.value
                            call.output = output
                            call.error_code = None
                            call.completed_at = recorded_at
                            call.lease_owner = None
                            call.lease_expires_at = None
                            call.updated_at = recorded_at
                            step = steps_by_id[call.plan_step_id]
                            step.status = PlanStepStatus.SUCCEEDED.value
                            step.updated_at = recorded_at
                            _append_event(
                                session,
                                cancellation,
                                CancellationEventType.LATE_EFFECT_EVIDENCE,
                                CancellationEvidenceSource.RECONCILIATION_WORKER,
                                verification.evidence_code,
                                observed_at=recorded_at,
                                tool_call_id=call.id,
                                evidence_fingerprint=fingerprint,
                            )
                            reconciled += 1
                            _apply_cancellation_truth(
                                session,
                                run,
                                cancellation,
                                at=recorded_at,
                            )
                            continue

                    if (
                        verification is not None
                        and verification.status is EffectVerificationStatus.VERIFIED_NO_EFFECT
                    ):
                        fingerprint = _evidence_fingerprint(
                            {
                                "call_id": str(call.id),
                                "kind": "verified_no_effect",
                                "code": verification.evidence_code.value,
                            }
                        )
                        if self._has_open_evidence_conflict(
                            session,
                            cancellation.id,
                            call.id,
                        ):
                            if not self._has_evidence(
                                session,
                                cancellation.id,
                                call.id,
                                fingerprint,
                            ):
                                self._mark_evidence_conflict(
                                    session,
                                    run,
                                    call,
                                    steps_by_id[call.plan_step_id],
                                    cancellation,
                                    evidence_fingerprint=fingerprint,
                                    evidence_code=(
                                        CancellationEvidenceCode.EVIDENCE_ADDED_TO_OPEN_CONFLICT
                                    ),
                                    source=CancellationEvidenceSource.RECONCILIATION_WORKER,
                                    observed_at=recorded_at,
                                )
                                reconciled += 1
                            continue
                        call.status = ToolCallStatus.CANCELLED.value
                        call.error_code = "NO_EFFECT_VERIFIED"
                        call.completed_at = recorded_at
                        call.lease_owner = None
                        call.lease_expires_at = None
                        call.updated_at = recorded_at
                        step = steps_by_id[call.plan_step_id]
                        step.status = PlanStepStatus.CANCELLED.value
                        step.updated_at = recorded_at
                        _append_event(
                            session,
                            cancellation,
                            CancellationEventType.LATE_EFFECT_EVIDENCE,
                            CancellationEvidenceSource.RECONCILIATION_WORKER,
                            verification.evidence_code,
                            observed_at=recorded_at,
                            tool_call_id=call.id,
                            evidence_fingerprint=fingerprint,
                        )
                        reconciled += 1
                        _apply_cancellation_truth(
                            session,
                            run,
                            cancellation,
                            at=recorded_at,
                        )
                        continue

                    if expired_execution:
                        call.status = ToolCallStatus.OUTCOME_UNKNOWN.value
                        call.error_code = "LEASE_EXPIRED_AFTER_EFFECT_COMMIT"
                        call.completed_at = None
                        call.lease_owner = None
                        call.lease_expires_at = None
                        call.updated_at = recorded_at
                        step = steps_by_id[call.plan_step_id]
                        step.status = PlanStepStatus.OUTCOME_UNKNOWN.value
                        step.updated_at = recorded_at
                        _append_event(
                            session,
                            cancellation,
                            CancellationEventType.OUTCOME_UNKNOWN,
                            CancellationEvidenceSource.RECONCILIATION_WORKER,
                            CancellationEvidenceCode.LEASE_EXPIRED_AFTER_EFFECT_COMMIT,
                            observed_at=recorded_at,
                            tool_call_id=call.id,
                        )
                        reconciled += 1
                        _apply_cancellation_truth(
                            session,
                            run,
                            cancellation,
                            at=recorded_at,
                        )
        return reconciled

    def record_failure(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        error_code: str,
        *,
        worker_id: str,
        failed_at: datetime,
        retry_at: datetime | None,
        effect_commit_token: UUID | None = None,
    ) -> tuple[Run, bool] | None:
        with self._session_factory.begin() as session:
            locked = self._lock_scoped_call(
                session,
                owner_id,
                workspace_id,
                conversation_id,
                call_id,
            )
            if locked is None:
                return None
            run, call, all_calls = locked
            if ToolCallStatus(call.status) is ToolCallStatus.SUCCEEDED:
                return None
            if (
                ToolCallStatus(call.status) is not ToolCallStatus.EXECUTING
                or call.lease_owner != worker_id
            ):
                return None
            if call.effect_commit_token is not None and (
                effect_commit_token != call.effect_commit_token
                or call.effect_commit_worker_id != worker_id
            ):
                return None

            cancellation = self._cancellation(session, run.id)
            locked_steps = self._lock_steps(session, run.id)
            steps_by_id = {step.id: step for step in locked_steps}
            observed_at = self._observation_time(
                failed_at,
                call.effect_committed_at,
                *(item.updated_at for item in all_calls),
                cancellation.requested_at if cancellation is not None else None,
                cancellation.updated_at if cancellation is not None else None,
            )
            if cancellation is not None:
                if call.effect_committed_at is None:
                    return None
                call.status = ToolCallStatus.OUTCOME_UNKNOWN.value
                call.error_code = error_code
                call.completed_at = None
                call.lease_owner = None
                call.lease_expires_at = None
                call.updated_at = observed_at
                step = steps_by_id[call.plan_step_id]
                step.status = PlanStepStatus.OUTCOME_UNKNOWN.value
                step.updated_at = observed_at
                _append_event(
                    session,
                    cancellation,
                    CancellationEventType.OUTCOME_UNKNOWN,
                    CancellationEvidenceSource.CURRENT_WORKER,
                    CancellationEvidenceCode.HANDLER_FAILURE_AFTER_CANCELLATION,
                    observed_at=observed_at,
                    occurred_at=failed_at,
                    tool_call_id=call.id,
                )
                _apply_cancellation_truth(session, run, cancellation, at=observed_at)
                return _run(run), False

            if retry_at is not None and call.attempt_count < call.max_attempts:
                call.status = ToolCallStatus.PENDING.value
                call.error_code = error_code
                call.available_at = max(retry_at, observed_at)
                call.lease_owner = None
                call.lease_expires_at = None
                call.updated_at = observed_at
                step = steps_by_id[call.plan_step_id]
                step.status = PlanStepStatus.PENDING.value
                step.updated_at = observed_at
                return _run(run), True

            for other_call in all_calls:
                if other_call.id == call.id:
                    other_call.status = ToolCallStatus.FAILED.value
                    other_call.error_code = error_code
                    other_call.lease_owner = None
                    other_call.lease_expires_at = None
                elif ToolCallStatus(other_call.status) in {
                    ToolCallStatus.PENDING,
                    ToolCallStatus.EXECUTING,
                }:
                    other_call.status = ToolCallStatus.CANCELLED.value
                    other_call.lease_owner = None
                    other_call.lease_expires_at = None
                if ToolCallStatus(other_call.status) in {
                    ToolCallStatus.FAILED,
                    ToolCallStatus.CANCELLED,
                }:
                    other_call.completed_at = observed_at
                    other_call.updated_at = observed_at

            for step in locked_steps:
                if step.id == call.plan_step_id:
                    step.status = PlanStepStatus.FAILED.value
                elif PlanStepStatus(step.status) in {
                    PlanStepStatus.PENDING,
                    PlanStepStatus.IN_PROGRESS,
                }:
                    step.status = PlanStepStatus.CANCELLED.value
                step.updated_at = observed_at

            run.status = RunStatus.FAILED.value
            run.error_code = error_code
            run.completed_at = observed_at
            run.updated_at = observed_at
            return _run(run), False
