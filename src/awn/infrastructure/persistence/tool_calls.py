"""Durable and idempotent persistence for approved tool execution."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.approvals import ApprovalStatus, action_fingerprint
from awn.domain.runs import PlanStepStatus, Run, RunRisk, RunStatus
from awn.domain.tool_calls import ToolCall, ToolCallStatus
from awn.infrastructure.persistence.models import (
    ApprovalRecord,
    PlanStepRecord,
    RunRecord,
    ToolCallRecord,
    WorkspaceRecord,
)
from awn.infrastructure.persistence.runs import _aware, _run, _step


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
        started_at=_aware(record.started_at),
        completed_at=_aware(record.completed_at),
        created_at=created_at,
        updated_at=updated_at,
    )


def _idempotency_key(action_fingerprint_value: str, step: PlanStepRecord) -> str:
    value = (
        f"awn.tool-call.v1:{action_fingerprint_value}:{step.id}:{step.tool_name}.{step.operation}"
    )
    return hashlib.sha256(value.encode()).hexdigest()


class SqlAlchemyToolCallRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

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
                    status=ToolCallStatus.EXECUTING.value,
                    risk=step.risk,
                    idempotency_key=_idempotency_key(approval.action_fingerprint, step),
                    error_code=None,
                    started_at=started_at,
                    completed_at=None,
                    created_at=started_at,
                    updated_at=started_at,
                )
                call_records.append(call)
                session.add(call)
                step.status = PlanStepStatus.IN_PROGRESS.value
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

    def succeed(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        output: dict[str, object],
        *,
        completed_at: datetime,
    ) -> Run | None:
        statement = (
            self._scoped_call_statement(
                owner_id,
                workspace_id,
                conversation_id,
            )
            .where(ToolCallRecord.id == call_id)
            .with_for_update()
        )
        with self._session_factory.begin() as session:
            call = session.scalar(statement)
            if call is None:
                return None
            run_record = session.get(RunRecord, call.run_id)
            assert run_record is not None
            if ToolCallStatus(call.status) is ToolCallStatus.SUCCEEDED:
                return _run(run_record)

            call.status = ToolCallStatus.SUCCEEDED.value
            call.output = output
            call.error_code = None
            call.completed_at = completed_at
            call.updated_at = completed_at
            step = session.get(PlanStepRecord, call.plan_step_id)
            assert step is not None
            step.status = PlanStepStatus.SUCCEEDED.value
            step.updated_at = completed_at
            session.flush()

            statuses = tuple(
                session.scalars(
                    select(ToolCallRecord.status).where(ToolCallRecord.run_id == call.run_id)
                )
            )
            if statuses and all(status == ToolCallStatus.SUCCEEDED.value for status in statuses):
                for plan_step in self._steps(session, call.run_id):
                    if PlanStepStatus(plan_step.status) in {
                        PlanStepStatus.PENDING,
                        PlanStepStatus.IN_PROGRESS,
                    }:
                        plan_step.status = PlanStepStatus.SUCCEEDED.value
                        plan_step.updated_at = completed_at
                succeeded = (
                    _run(run_record)
                    .transition_to(RunStatus.VERIFYING, at=completed_at)
                    .transition_to(RunStatus.SUCCEEDED, at=completed_at)
                )
                run_record.status = succeeded.status.value
                run_record.completed_at = succeeded.completed_at
                run_record.updated_at = succeeded.updated_at
            session.flush()
            return _run(run_record)

    def fail(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        error_code: str,
        *,
        completed_at: datetime,
    ) -> Run | None:
        statement = (
            self._scoped_call_statement(
                owner_id,
                workspace_id,
                conversation_id,
            )
            .where(ToolCallRecord.id == call_id)
            .with_for_update()
        )
        with self._session_factory.begin() as session:
            call = session.scalar(statement)
            if call is None:
                return None
            run_record = session.get(RunRecord, call.run_id)
            assert run_record is not None
            if ToolCallStatus(call.status) is ToolCallStatus.SUCCEEDED:
                return _run(run_record)

            for other_call in session.scalars(
                select(ToolCallRecord).where(ToolCallRecord.run_id == call.run_id)
            ):
                if other_call.id == call.id:
                    other_call.status = ToolCallStatus.FAILED.value
                    other_call.error_code = error_code
                elif ToolCallStatus(other_call.status) in {
                    ToolCallStatus.PENDING,
                    ToolCallStatus.EXECUTING,
                }:
                    other_call.status = ToolCallStatus.CANCELLED.value
                if ToolCallStatus(other_call.status) in {
                    ToolCallStatus.FAILED,
                    ToolCallStatus.CANCELLED,
                }:
                    other_call.completed_at = completed_at
                    other_call.updated_at = completed_at

            for step in self._steps(session, call.run_id):
                if step.id == call.plan_step_id:
                    step.status = PlanStepStatus.FAILED.value
                elif PlanStepStatus(step.status) in {
                    PlanStepStatus.PENDING,
                    PlanStepStatus.IN_PROGRESS,
                }:
                    step.status = PlanStepStatus.CANCELLED.value
                step.updated_at = completed_at

            run_record.status = RunStatus.FAILED.value
            run_record.error_code = error_code
            run_record.completed_at = completed_at
            run_record.updated_at = completed_at
            session.flush()
            return _run(run_record)
