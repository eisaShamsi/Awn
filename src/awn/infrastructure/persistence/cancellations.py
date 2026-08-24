"""Scoped persistence and deterministic reconciliation for run cancellation."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.cancellations import (
    CancellationDecision,
    CancellationEvent,
    CancellationEventType,
    CancellationEvidenceCode,
    CancellationEvidenceSource,
    CancellationRequestResult,
    CancellationStatus,
    RunCancellation,
)
from awn.domain.runs import TERMINAL_RUN_STATUSES, PlanStepStatus, RunStatus
from awn.domain.tool_calls import ToolCallStatus
from awn.infrastructure.persistence.models import (
    PlanStepRecord,
    RunCancellationEventRecord,
    RunCancellationRecord,
    RunRecord,
    ToolCallRecord,
    WorkspaceRecord,
)
from awn.infrastructure.persistence.runs import _aware


def _event(record: RunCancellationEventRecord) -> CancellationEvent:
    observed_at = _aware(record.observed_at)
    assert observed_at is not None
    return CancellationEvent(
        id=record.id,
        cancellation_id=record.cancellation_id,
        sequence_no=record.sequence_no,
        tool_call_id=record.tool_call_id,
        event_type=CancellationEventType(record.event_type),
        source_type=CancellationEvidenceSource(record.source_type),
        evidence_code=CancellationEvidenceCode(record.evidence_code),
        evidence_fingerprint=record.evidence_fingerprint,
        related_evidence_fingerprint=record.related_evidence_fingerprint,
        superseded_status=record.superseded_status,
        occurred_at=_aware(record.occurred_at),
        observed_at=observed_at,
    )


def _cancellation(record: RunCancellationRecord) -> RunCancellation:
    received_at = _aware(record.received_at)
    requested_at = _aware(record.requested_at)
    updated_at = _aware(record.updated_at)
    assert received_at is not None
    assert requested_at is not None
    assert updated_at is not None
    return RunCancellation(
        id=record.id,
        run_id=record.run_id,
        requested_by=record.requested_by,
        status=CancellationStatus(record.status),
        reason_code=record.reason_code,
        received_at=received_at,
        requested_at=requested_at,
        resolved_at=_aware(record.resolved_at),
        updated_at=updated_at,
        events=tuple(_event(item) for item in record.events),
    )


def _append_event(
    session: Session,
    cancellation: RunCancellationRecord,
    event_type: CancellationEventType,
    source_type: CancellationEvidenceSource,
    evidence_code: CancellationEvidenceCode,
    *,
    observed_at: datetime,
    tool_call_id: UUID | None = None,
    evidence_fingerprint: str | None = None,
    related_evidence_fingerprint: str | None = None,
    superseded_status: str | None = None,
    occurred_at: datetime | None = None,
) -> RunCancellationEventRecord:
    session.flush()
    sequence_no = session.scalar(
        select(func.coalesce(func.max(RunCancellationEventRecord.sequence_no), 0) + 1).where(
            RunCancellationEventRecord.cancellation_id == cancellation.id
        )
    )
    assert sequence_no is not None
    record = RunCancellationEventRecord(
        id=uuid4(),
        cancellation_id=cancellation.id,
        sequence_no=sequence_no,
        tool_call_id=tool_call_id,
        event_type=event_type.value,
        source_type=source_type.value,
        evidence_code=evidence_code.value,
        evidence_fingerprint=evidence_fingerprint,
        related_evidence_fingerprint=related_evidence_fingerprint,
        superseded_status=superseded_status,
        occurred_at=occurred_at,
        observed_at=observed_at,
    )
    session.add(record)
    return record


def _set_run_status(run: RunRecord, status: RunStatus, at: datetime) -> None:
    run.status = status.value
    run.completed_at = at if status in TERMINAL_RUN_STATUSES else None
    run.updated_at = at


def _apply_cancellation_truth(
    session: Session,
    run: RunRecord,
    cancellation: RunCancellationRecord,
    *,
    at: datetime,
) -> None:
    session.flush()
    calls = tuple(
        session.scalars(
            select(ToolCallRecord)
            .where(ToolCallRecord.run_id == run.id)
            .order_by(ToolCallRecord.id)
        )
    )
    statuses = tuple(ToolCallStatus(call.status) for call in calls)
    previous = CancellationStatus(cancellation.status)

    if any(status is ToolCallStatus.OUTCOME_UNKNOWN for status in statuses):
        next_cancellation = CancellationStatus.UNCERTAIN
        next_run = RunStatus.CANCELLATION_UNCERTAIN
    elif any(
        status is ToolCallStatus.EXECUTING and call.effect_committed_at is not None
        for call, status in zip(calls, statuses, strict=True)
    ):
        next_cancellation = CancellationStatus.ACCEPTED
        next_run = RunStatus.CANCELLATION_REQUESTED
    else:
        succeeded = sum(status is ToolCallStatus.SUCCEEDED for status in statuses)
        failed = sum(status is ToolCallStatus.FAILED for status in statuses)
        if statuses and succeeded == len(statuses):
            next_cancellation = CancellationStatus.COMPLETED
            next_run = RunStatus.SUCCEEDED
        elif succeeded:
            next_cancellation = CancellationStatus.PARTIALLY_SUCCEEDED
            next_run = RunStatus.PARTIALLY_SUCCEEDED
        elif failed:
            next_cancellation = CancellationStatus.EXECUTION_FAILED
            next_run = RunStatus.FAILED
        else:
            next_cancellation = CancellationStatus.CANCELLED
            next_run = RunStatus.CANCELLED

    cancellation.status = next_cancellation.value
    cancellation.resolved_at = (
        None
        if next_cancellation in {CancellationStatus.ACCEPTED, CancellationStatus.UNCERTAIN}
        else at
    )
    cancellation.updated_at = at
    _set_run_status(run, next_run, at)

    if next_cancellation is previous:
        return
    event_map = {
        CancellationStatus.UNCERTAIN: (
            CancellationEventType.OUTCOME_UNKNOWN,
            CancellationEvidenceCode.CANCELLATION_OUTCOME_UNKNOWN,
        ),
        CancellationStatus.CANCELLED: (
            CancellationEventType.CANCELLED_NO_EFFECT,
            CancellationEvidenceCode.NO_EFFECT_VERIFIED,
        ),
        CancellationStatus.PARTIALLY_SUCCEEDED: (
            CancellationEventType.PARTIAL_EFFECT,
            CancellationEvidenceCode.PARTIAL_EFFECT_VERIFIED,
        ),
        CancellationStatus.COMPLETED: (
            CancellationEventType.EFFECT_COMPLETED,
            CancellationEvidenceCode.FULL_EFFECT_VERIFIED,
        ),
        CancellationStatus.EXECUTION_FAILED: (
            CancellationEventType.EXECUTION_FAILED,
            CancellationEvidenceCode.EXECUTION_FAILED_NO_EFFECT_VERIFIED,
        ),
    }
    event = event_map.get(next_cancellation)
    if event is not None:
        _append_event(
            session,
            cancellation,
            event[0],
            CancellationEvidenceSource.DATABASE_VERIFICATION,
            event[1],
            observed_at=at,
        )


class SqlAlchemyCancellationRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _scoped_run_statement(
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ):
        return (
            select(RunRecord)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
                RunRecord.id == run_id,
            )
        )

    def request(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        *,
        received_at: datetime,
    ) -> CancellationRequestResult | None:
        with self._session_factory.begin() as session:
            run = session.scalar(
                self._scoped_run_statement(
                    owner_id,
                    workspace_id,
                    conversation_id,
                    run_id,
                ).with_for_update(of=RunRecord)
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
            existing = session.scalar(
                select(RunCancellationRecord)
                .where(RunCancellationRecord.run_id == run.id)
                .with_for_update(of=RunCancellationRecord)
            )
            if existing is not None:
                session.refresh(existing, attribute_names=["events"])
                return CancellationRequestResult(
                    decision=CancellationDecision.ALREADY_REQUESTED,
                    received_at=received_at,
                    run_status=run.status,
                    cancellation=_cancellation(existing),
                )

            run_status = RunStatus(run.status)
            if run_status in TERMINAL_RUN_STATUSES:
                return CancellationRequestResult(
                    decision=CancellationDecision.TOO_LATE,
                    received_at=received_at,
                    run_status=run.status,
                )
            if run_status is not RunStatus.EXECUTING:
                return CancellationRequestResult(
                    decision=CancellationDecision.NOT_CANCELLABLE,
                    received_at=received_at,
                    run_status=run.status,
                )

            steps = tuple(
                session.scalars(
                    select(PlanStepRecord)
                    .where(PlanStepRecord.run_id == run.id)
                    .order_by(PlanStepRecord.position, PlanStepRecord.id)
                    .with_for_update(of=PlanStepRecord)
                )
            )
            steps_by_id = {step.id: step for step in steps}
            causal_floor = max(
                timestamp
                for timestamp in (
                    received_at,
                    *(_aware(call.effect_committed_at) for call in calls),
                )
                if timestamp is not None
            )
            requested_at = max(self._clock(), causal_floor)
            cancellation = RunCancellationRecord(
                id=uuid4(),
                run_id=run.id,
                requested_by=owner_id,
                status=CancellationStatus.ACCEPTED.value,
                reason_code="OWNER_REQUEST",
                received_at=received_at,
                requested_at=requested_at,
                resolved_at=None,
                updated_at=requested_at,
            )
            session.add(cancellation)
            session.flush()
            _append_event(
                session,
                cancellation,
                CancellationEventType.REQUEST_ACCEPTED,
                CancellationEvidenceSource.OWNER_ACTION,
                CancellationEvidenceCode.OWNER_REQUEST_COMMITTED,
                observed_at=requested_at,
                occurred_at=requested_at,
            )

            for call in calls:
                status = ToolCallStatus(call.status)
                if (status is ToolCallStatus.PENDING and call.effect_committed_at is None) or (
                    status is ToolCallStatus.EXECUTING and call.effect_committed_at is None
                ):
                    call.status = ToolCallStatus.CANCELLED.value
                    call.error_code = "CANCELLED_BEFORE_EFFECT"
                    call.lease_owner = None
                    call.lease_expires_at = None
                    call.completed_at = requested_at
                    call.updated_at = requested_at
                    step = steps_by_id.get(call.plan_step_id)
                    assert step is not None
                    step.status = PlanStepStatus.CANCELLED.value
                    step.updated_at = requested_at
                    _append_event(
                        session,
                        cancellation,
                        CancellationEventType.CALL_CANCELLED_BEFORE_EFFECT,
                        CancellationEvidenceSource.CANCELLATION_API,
                        CancellationEvidenceCode.CANCEL_WON_EFFECT_RACE,
                        observed_at=requested_at,
                        tool_call_id=call.id,
                    )
                elif status is ToolCallStatus.EXECUTING:
                    _append_event(
                        session,
                        cancellation,
                        CancellationEventType.EFFECT_COMMITTED,
                        CancellationEvidenceSource.CANCELLATION_API,
                        CancellationEvidenceCode.EFFECT_COMMIT_WON_CANCELLATION_RACE,
                        observed_at=requested_at,
                        tool_call_id=call.id,
                        occurred_at=_aware(call.effect_committed_at),
                    )
                elif status is ToolCallStatus.PENDING:
                    call.status = ToolCallStatus.OUTCOME_UNKNOWN.value
                    call.error_code = "CANCELLATION_EFFECT_OUTCOME_UNKNOWN"
                    call.lease_owner = None
                    call.lease_expires_at = None
                    call.completed_at = None
                    call.updated_at = requested_at
                    step = steps_by_id.get(call.plan_step_id)
                    assert step is not None
                    step.status = PlanStepStatus.OUTCOME_UNKNOWN.value
                    step.updated_at = requested_at
                    _append_event(
                        session,
                        cancellation,
                        CancellationEventType.OUTCOME_UNKNOWN,
                        CancellationEvidenceSource.CANCELLATION_API,
                        CancellationEvidenceCode.EFFECT_COMMITTED_BEFORE_CANCELLATION,
                        observed_at=requested_at,
                        tool_call_id=call.id,
                        occurred_at=_aware(call.effect_committed_at),
                    )

            for step in steps:
                if PlanStepStatus(step.status) in {
                    PlanStepStatus.PENDING,
                    PlanStepStatus.IN_PROGRESS,
                }:
                    step.status = PlanStepStatus.CANCELLED.value
                    step.updated_at = requested_at

            _apply_cancellation_truth(
                session,
                run,
                cancellation,
                at=requested_at,
            )
            session.flush()
            session.refresh(cancellation, attribute_names=["events"])
            return CancellationRequestResult(
                decision=CancellationDecision.ACCEPTED,
                received_at=received_at,
                run_status=run.status,
                cancellation=_cancellation(cancellation),
            )

    def get(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> RunCancellation | None:
        statement = (
            select(RunCancellationRecord)
            .join(RunRecord, RunRecord.id == RunCancellationRecord.run_id)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
                RunRecord.id == run_id,
            )
        )
        with self._session_factory() as session:
            record = session.scalar(statement)
            if record is None:
                return None
            _ = record.events
            return _cancellation(record)
