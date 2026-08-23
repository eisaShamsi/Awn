"""SQLAlchemy persistence for workspace-scoped agent runs."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.runs import PlanStep, PlanStepStatus, Run, RunRisk, RunStatus
from awn.infrastructure.persistence.models import (
    ConversationRecord,
    MessageRecord,
    PlanStepRecord,
    RunRecord,
    WorkspaceRecord,
)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _run(record: RunRecord) -> Run:
    created_at = _aware(record.created_at)
    updated_at = _aware(record.updated_at)
    assert created_at is not None
    assert updated_at is not None
    return Run(
        id=record.id,
        workspace_id=record.workspace_id,
        conversation_id=record.conversation_id,
        request_message_id=record.request_message_id,
        trace_id=record.trace_id,
        status=RunStatus(record.status),
        risk=RunRisk(record.risk),
        autonomy_level=record.autonomy_level,
        error_code=record.error_code,
        started_at=_aware(record.started_at),
        completed_at=_aware(record.completed_at),
        created_at=created_at,
        updated_at=updated_at,
    )


def _step(record: PlanStepRecord) -> PlanStep:
    created_at = _aware(record.created_at)
    updated_at = _aware(record.updated_at)
    assert created_at is not None
    assert updated_at is not None
    return PlanStep(
        id=record.id,
        run_id=record.run_id,
        position=record.position,
        title=record.title,
        status=PlanStepStatus(record.status),
        risk=RunRisk(record.risk),
        requires_approval=record.requires_approval,
        tool_name=record.tool_name,
        operation=record.operation,
        tool_input=record.tool_input,
        created_at=created_at,
        updated_at=updated_at,
    )


class SqlAlchemyRunRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _scoped_run_statement(
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ):
        return (
            select(RunRecord)
            .join(WorkspaceRecord, WorkspaceRecord.id == RunRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                RunRecord.workspace_id == workspace_id,
                RunRecord.conversation_id == conversation_id,
            )
        )

    def add(self, owner_id: UUID, run: Run) -> Run | None:
        conversation_statement = (
            select(ConversationRecord.id)
            .join(WorkspaceRecord)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                ConversationRecord.workspace_id == run.workspace_id,
                ConversationRecord.id == run.conversation_id,
            )
        )
        message_statement = select(MessageRecord.id).where(
            MessageRecord.id == run.request_message_id,
            MessageRecord.conversation_id == run.conversation_id,
        )
        with self._session_factory.begin() as session:
            if session.scalar(conversation_statement) is None:
                return None
            if session.scalar(message_statement) is None:
                return None
            record = RunRecord(
                id=run.id,
                workspace_id=run.workspace_id,
                conversation_id=run.conversation_id,
                request_message_id=run.request_message_id,
                trace_id=run.trace_id,
                status=run.status.value,
                risk=run.risk.value,
                autonomy_level=run.autonomy_level,
                error_code=run.error_code,
                started_at=run.started_at,
                completed_at=run.completed_at,
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
            session.add(record)
            session.flush()
            return _run(record)

    def get(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Run | None:
        statement = self._scoped_run_statement(
            owner_id,
            workspace_id,
            conversation_id,
        ).where(RunRecord.id == run_id)
        with self._session_factory() as session:
            record = session.scalar(statement)
            return _run(record) if record is not None else None

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> Iterable[Run] | None:
        statement = self._scoped_run_statement(
            owner_id,
            workspace_id,
            conversation_id,
        ).order_by(RunRecord.created_at.desc(), RunRecord.id)
        conversation_statement = (
            select(ConversationRecord.id)
            .join(WorkspaceRecord)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                ConversationRecord.workspace_id == workspace_id,
                ConversationRecord.id == conversation_id,
            )
        )
        with self._session_factory() as session:
            if session.scalar(conversation_statement) is None:
                return None
            return tuple(_run(record) for record in session.scalars(statement))

    def list_steps(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Iterable[PlanStep] | None:
        run_statement = self._scoped_run_statement(
            owner_id,
            workspace_id,
            conversation_id,
        ).where(RunRecord.id == run_id)
        step_statement = (
            select(PlanStepRecord)
            .where(PlanStepRecord.run_id == run_id)
            .order_by(PlanStepRecord.position, PlanStepRecord.id)
        )
        with self._session_factory() as session:
            if session.scalar(run_statement) is None:
                return None
            return tuple(_step(record) for record in session.scalars(step_statement))

    def save(
        self,
        owner_id: UUID,
        run: Run,
        steps: Iterable[PlanStep] | None = None,
    ) -> Run | None:
        statement = self._scoped_run_statement(
            owner_id,
            run.workspace_id,
            run.conversation_id,
        ).where(RunRecord.id == run.id)
        persisted_steps = tuple(steps) if steps is not None else None
        if persisted_steps is not None and any(step.run_id != run.id for step in persisted_steps):
            raise ValueError("all plan steps must belong to the saved run")

        with self._session_factory.begin() as session:
            record = session.scalar(statement)
            if record is None:
                return None

            record.status = run.status.value
            record.risk = run.risk.value
            record.error_code = run.error_code
            record.started_at = run.started_at
            record.completed_at = run.completed_at
            record.updated_at = run.updated_at

            if persisted_steps is not None:
                session.execute(delete(PlanStepRecord).where(PlanStepRecord.run_id == run.id))
                session.add_all(
                    PlanStepRecord(
                        id=step.id,
                        run_id=step.run_id,
                        position=step.position,
                        title=step.title,
                        status=step.status.value,
                        risk=step.risk.value,
                        requires_approval=step.requires_approval,
                        tool_name=step.tool_name,
                        operation=step.operation,
                        tool_input=step.tool_input,
                        created_at=step.created_at,
                        updated_at=step.updated_at,
                    )
                    for step in persisted_steps
                )

            session.flush()
            return _run(record)
