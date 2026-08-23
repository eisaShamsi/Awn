"""Transactional persistence for scoped approval requests."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from awn.application.approvals import APPROVAL_OPERATION
from awn.domain.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalDecisionOutcome,
    ApprovalDecisionResult,
    ApprovalRequest,
    ApprovalStatus,
    action_fingerprint,
)
from awn.domain.runs import RunStatus
from awn.infrastructure.persistence.models import (
    ApprovalRecord,
    PlanStepRecord,
    RunRecord,
    WorkspaceRecord,
)
from awn.infrastructure.persistence.runs import _aware, _run, _step


def _approval(record: ApprovalRecord) -> ApprovalRequest:
    requested_at = _aware(record.requested_at)
    expires_at = _aware(record.expires_at)
    assert requested_at is not None
    assert expires_at is not None
    return ApprovalRequest(
        id=record.id,
        run_id=record.run_id,
        operation=record.operation,
        summary=record.summary,
        risk=record.risk,
        action_fingerprint=record.action_fingerprint,
        status=ApprovalStatus(record.status),
        decision_note=record.decision_note,
        requested_at=requested_at,
        expires_at=expires_at,
        decided_at=_aware(record.decided_at),
    )


class SqlAlchemyApprovalRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _scoped_statement(
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ):
        return (
            select(ApprovalRecord)
            .join(RunRecord, RunRecord.id == ApprovalRecord.run_id)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
                RunRecord.id == run_id,
            )
        )

    @staticmethod
    def _current_steps(session: Session, run_id: UUID):
        statement = (
            select(PlanStepRecord)
            .where(PlanStepRecord.run_id == run_id)
            .order_by(PlanStepRecord.position, PlanStepRecord.id)
        )
        return tuple(_step(record) for record in session.scalars(statement))

    def add_for_plan(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        approval: ApprovalRequest,
    ) -> ApprovalRequest | None:
        run_statement = (
            select(RunRecord)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
                RunRecord.id == approval.run_id,
            )
            .with_for_update()
        )
        with self._session_factory.begin() as session:
            run_record = session.scalar(run_statement)
            if run_record is None:
                return None

            existing = session.scalar(
                select(ApprovalRecord).where(
                    ApprovalRecord.run_id == approval.run_id,
                    ApprovalRecord.action_fingerprint == approval.action_fingerprint,
                )
            )
            if existing is not None:
                return _approval(existing)

            if RunStatus(run_record.status) is not RunStatus.READY:
                return None
            current_steps = self._current_steps(session, approval.run_id)
            current_fingerprint = action_fingerprint(
                _run(run_record),
                current_steps,
                operation=APPROVAL_OPERATION,
            )
            if current_fingerprint != approval.action_fingerprint:
                return None

            record = ApprovalRecord(
                id=approval.id,
                run_id=approval.run_id,
                operation=approval.operation,
                summary=approval.summary,
                risk=approval.risk.value,
                action_fingerprint=approval.action_fingerprint,
                status=approval.status.value,
                decision_note=approval.decision_note,
                requested_at=approval.requested_at,
                expires_at=approval.expires_at,
                decided_at=approval.decided_at,
            )
            session.add(record)
            run_record.status = RunStatus.AWAITING_APPROVAL.value
            run_record.updated_at = approval.requested_at
            session.flush()
            return _approval(record)

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Iterable[ApprovalRequest] | None:
        approval_statement = self._scoped_statement(
            owner_id,
            workspace_id,
            conversation_id,
            run_id,
        ).order_by(ApprovalRecord.requested_at.desc(), ApprovalRecord.id)
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
        with self._session_factory() as session:
            if session.scalar(run_statement) is None:
                return None
            return tuple(_approval(record) for record in session.scalars(approval_statement))

    def decide(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        command: ApprovalDecisionCommand,
        *,
        decided_at: datetime,
    ) -> ApprovalDecisionResult | None:
        statement = (
            self._scoped_statement(
                owner_id,
                workspace_id,
                conversation_id,
                run_id,
            )
            .where(ApprovalRecord.id == approval_id)
            .with_for_update()
        )

        with self._session_factory.begin() as session:
            record = session.scalar(statement)
            if record is None:
                return None
            run_record = session.get(RunRecord, run_id)
            assert run_record is not None

            status = ApprovalStatus(record.status)
            if status is not ApprovalStatus.PENDING:
                repeated_status = (
                    ApprovalStatus.APPROVED
                    if command.decision is ApprovalDecision.APPROVE
                    else ApprovalStatus.REJECTED
                )
                outcome = (
                    ApprovalDecisionOutcome.ALREADY_RESOLVED
                    if status is repeated_status
                    and command.action_fingerprint == record.action_fingerprint
                    else ApprovalDecisionOutcome.CONFLICT
                )
                return ApprovalDecisionResult(
                    outcome=outcome,
                    approval=_approval(record),
                    run=_run(run_record),
                )

            if command.action_fingerprint != record.action_fingerprint:
                return ApprovalDecisionResult(
                    outcome=ApprovalDecisionOutcome.FINGERPRINT_MISMATCH,
                    approval=_approval(record),
                    run=_run(run_record),
                )

            current_steps = self._current_steps(session, run_id)
            current_fingerprint = action_fingerprint(
                _run(run_record),
                current_steps,
                operation=record.operation,
            )
            if current_fingerprint != record.action_fingerprint:
                self._invalidate(record, run_record, decided_at, "PLAN_CHANGED")
                session.flush()
                return ApprovalDecisionResult(
                    outcome=ApprovalDecisionOutcome.PLAN_CHANGED,
                    approval=_approval(record),
                    run=_run(run_record),
                )

            expires_at = _aware(record.expires_at)
            assert expires_at is not None
            if decided_at >= expires_at:
                record.status = ApprovalStatus.EXPIRED.value
                record.decision_note = "APPROVAL_EXPIRED"
                record.decided_at = decided_at
                self._cancel_run(run_record, decided_at)
                session.flush()
                return ApprovalDecisionResult(
                    outcome=ApprovalDecisionOutcome.EXPIRED,
                    approval=_approval(record),
                    run=_run(run_record),
                )

            if RunStatus(run_record.status) is not RunStatus.AWAITING_APPROVAL:
                self._invalidate(record, run_record, decided_at, "RUN_STATE_CHANGED")
                session.flush()
                return ApprovalDecisionResult(
                    outcome=ApprovalDecisionOutcome.CONFLICT,
                    approval=_approval(record),
                    run=_run(run_record),
                )

            record.status = (
                ApprovalStatus.APPROVED.value
                if command.decision is ApprovalDecision.APPROVE
                else ApprovalStatus.REJECTED.value
            )
            record.decision_note = command.note
            record.decided_at = decided_at
            if command.decision is ApprovalDecision.APPROVE:
                run_record.status = RunStatus.READY.value
                run_record.updated_at = decided_at
            else:
                self._cancel_run(run_record, decided_at)
            session.flush()
            return ApprovalDecisionResult(
                outcome=ApprovalDecisionOutcome.RESOLVED,
                approval=_approval(record),
                run=_run(run_record),
            )

    @staticmethod
    def _cancel_run(run_record: RunRecord, decided_at: datetime) -> None:
        if RunStatus(run_record.status) is RunStatus.AWAITING_APPROVAL:
            run_record.status = RunStatus.CANCELLED.value
            run_record.completed_at = decided_at
            run_record.updated_at = decided_at

    @staticmethod
    def _invalidate(
        record: ApprovalRecord,
        run_record: RunRecord,
        decided_at: datetime,
        reason: str,
    ) -> None:
        record.status = ApprovalStatus.INVALIDATED.value
        record.decision_note = reason
        record.decided_at = decided_at
        if RunStatus(run_record.status) is RunStatus.AWAITING_APPROVAL:
            run_record.status = RunStatus.READY.value
            run_record.updated_at = decided_at
