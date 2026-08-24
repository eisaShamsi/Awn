"""PostgreSQL/SQLAlchemy implementation of the task repository port."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.tasks import Task, TaskPriority, TaskStatus
from awn.infrastructure.persistence.models import TaskRecord, WorkspaceRecord


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _to_domain(record: TaskRecord) -> Task:
    created_at = _aware(record.created_at)
    updated_at = _aware(record.updated_at)
    assert created_at is not None
    assert updated_at is not None
    return Task(
        id=record.id,
        workspace_id=record.workspace_id,
        title=record.title,
        description=record.description,
        status=TaskStatus(record.status),
        priority=TaskPriority(record.priority),
        due_at=_aware(record.due_at),
        created_at=created_at,
        updated_at=updated_at,
    )


def _to_record(task: Task, *, source_tool_call_id: UUID | None = None) -> TaskRecord:
    return TaskRecord(
        id=task.id,
        workspace_id=task.workspace_id,
        source_tool_call_id=source_tool_call_id,
        title=task.title,
        description=task.description,
        status=task.status.value,
        priority=task.priority.value,
        due_at=task.due_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


class SqlAlchemyTaskRepository:
    """Persist each application operation in its own short transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(
        self,
        owner_id: UUID,
        task: Task,
        *,
        source_tool_call_id: UUID | None = None,
    ) -> Task | None:
        record = _to_record(task, source_tool_call_id=source_tool_call_id)
        workspace_statement = select(WorkspaceRecord.id).where(
            WorkspaceRecord.id == task.workspace_id,
            WorkspaceRecord.owner_id == owner_id,
        )
        with self._session_factory.begin() as session:
            if session.scalar(workspace_statement) is None:
                return None
            if source_tool_call_id is not None:
                existing = session.scalar(
                    select(TaskRecord).where(TaskRecord.source_tool_call_id == source_tool_call_id)
                )
                if existing is not None:
                    return _to_domain(existing)
            session.add(record)
            session.flush()
        return _to_domain(record)

    def get(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        task_id: UUID,
    ) -> Task | None:
        statement = (
            select(TaskRecord)
            .join(WorkspaceRecord, WorkspaceRecord.id == TaskRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                TaskRecord.workspace_id == workspace_id,
                TaskRecord.id == task_id,
            )
        )
        with self._session_factory() as session:
            record = session.scalar(statement)
            return _to_domain(record) if record is not None else None

    def get_by_source_tool_call(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        source_tool_call_id: UUID,
    ) -> Task | None:
        statement = (
            select(TaskRecord)
            .join(WorkspaceRecord, WorkspaceRecord.id == TaskRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                TaskRecord.workspace_id == workspace_id,
                TaskRecord.source_tool_call_id == source_tool_call_id,
            )
        )
        with self._session_factory() as session:
            record = session.scalar(statement)
            return _to_domain(record) if record is not None else None

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
    ) -> Iterable[Task] | None:
        workspace_statement = select(WorkspaceRecord.id).where(
            WorkspaceRecord.id == workspace_id,
            WorkspaceRecord.owner_id == owner_id,
        )
        statement = (
            select(TaskRecord)
            .where(TaskRecord.workspace_id == workspace_id)
            .order_by(TaskRecord.created_at, TaskRecord.id)
        )
        with self._session_factory() as session:
            if session.scalar(workspace_statement) is None:
                return None
            records = session.scalars(statement).all()
            return tuple(_to_domain(record) for record in records)

    def replace(self, owner_id: UUID, task: Task) -> Task | None:
        statement = (
            select(TaskRecord)
            .join(WorkspaceRecord, WorkspaceRecord.id == TaskRecord.workspace_id)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                TaskRecord.workspace_id == task.workspace_id,
                TaskRecord.id == task.id,
            )
        )
        with self._session_factory.begin() as session:
            record = session.scalar(statement)
            if record is None:
                return None
            record.title = task.title
            record.description = task.description
            record.status = task.status.value
            record.priority = task.priority.value
            record.due_at = task.due_at
            record.updated_at = task.updated_at
            session.flush()
        return _to_domain(record)
