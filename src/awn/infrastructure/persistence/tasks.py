"""PostgreSQL/SQLAlchemy implementation of the task repository port."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.tasks import Task, TaskPriority, TaskStatus
from awn.infrastructure.persistence.models import TaskRecord


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
        title=record.title,
        description=record.description,
        status=TaskStatus(record.status),
        priority=TaskPriority(record.priority),
        due_at=_aware(record.due_at),
        created_at=created_at,
        updated_at=updated_at,
    )


def _to_record(task: Task) -> TaskRecord:
    return TaskRecord(
        id=task.id,
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

    def add(self, task: Task) -> Task:
        record = _to_record(task)
        with self._session_factory.begin() as session:
            session.add(record)
            session.flush()
        return _to_domain(record)

    def get(self, task_id: UUID) -> Task | None:
        with self._session_factory() as session:
            record = session.get(TaskRecord, task_id)
            return _to_domain(record) if record is not None else None

    def list(self) -> Iterable[Task]:
        statement = select(TaskRecord).order_by(TaskRecord.created_at, TaskRecord.id)
        with self._session_factory() as session:
            records = session.scalars(statement).all()
            return tuple(_to_domain(record) for record in records)

    def replace(self, task: Task) -> Task:
        with self._session_factory.begin() as session:
            record = session.get(TaskRecord, task.id)
            if record is None:
                raise LookupError(f"Task {task.id} no longer exists")
            record.title = task.title
            record.description = task.description
            record.status = task.status.value
            record.priority = task.priority.value
            record.due_at = task.due_at
            record.updated_at = task.updated_at
            session.flush()
        return _to_domain(record)
