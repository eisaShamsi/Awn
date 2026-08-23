"""Task use cases and the replaceable repository port."""

from collections.abc import Iterable
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from awn.domain.tasks import Task, TaskCreate, TaskStatus, TaskUpdate


class TaskRepository(Protocol):
    def add(self, task: Task) -> Task: ...

    def get(self, task_id: UUID) -> Task | None: ...

    def list(self) -> Iterable[Task]: ...

    def replace(self, task: Task) -> Task: ...


class InMemoryTaskRepository:
    """Development repository; PostgreSQL replaces it in the persistence milestone."""

    def __init__(self) -> None:
        self._tasks: dict[UUID, Task] = {}
        self._lock = RLock()

    def add(self, task: Task) -> Task:
        with self._lock:
            self._tasks[task.id] = task
        return task

    def get(self, task_id: UUID) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> Iterable[Task]:
        with self._lock:
            return tuple(self._tasks.values())

    def replace(self, task: Task) -> Task:
        with self._lock:
            self._tasks[task.id] = task
        return task


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

        changes = command.model_dump(exclude_unset=True, exclude_none=True)
        if not changes:
            return task
        changes["updated_at"] = datetime.now(UTC)
        updated = task.model_copy(update=changes)
        return self._repository.replace(updated)
