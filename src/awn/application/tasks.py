"""Task use cases and the replaceable repository port."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from awn.domain.tasks import Task, TaskCreate, TaskStatus, TaskUpdate


class TaskRepository(Protocol):
    def add(self, task: Task) -> Task: ...

    def get(self, task_id: UUID) -> Task | None: ...

    def list(self) -> Iterable[Task]: ...

    def replace(self, task: Task) -> Task: ...


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository

    def create(self, command: TaskCreate) -> Task:
        now = datetime.now(UTC)
        task = Task(
            id=uuid4(),
            title=command.title,
            description=command.description,
            status=TaskStatus.PENDING,
            priority=command.priority,
            due_at=command.due_at,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add(task)

    def get(self, task_id: UUID) -> Task | None:
        return self._repository.get(task_id)

    def list(self) -> list[Task]:
        return sorted(self._repository.list(), key=lambda task: task.created_at)

    def update(self, task_id: UUID, command: TaskUpdate) -> Task | None:
        task = self._repository.get(task_id)
        if task is None:
            return None

        changes = command.model_dump(exclude_unset=True)
        if not changes:
            return task
        changes["updated_at"] = datetime.now(UTC)
        updated = task.model_copy(update=changes)
        return self._repository.replace(updated)
