"""Task use cases and the replaceable repository port."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from awn.application.identity import IdentityRepository
from awn.domain.tasks import Task, TaskCreate, TaskStatus, TaskUpdate


class TaskRepository(Protocol):
    def add(
        self,
        owner_id: UUID,
        task: Task,
        *,
        source_tool_call_id: UUID | None = None,
    ) -> Task | None: ...

    def get(self, owner_id: UUID, workspace_id: UUID, task_id: UUID) -> Task | None: ...

    def get_by_source_tool_call(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        source_tool_call_id: UUID,
    ) -> Task | None: ...

    def list(self, owner_id: UUID, workspace_id: UUID) -> Iterable[Task] | None: ...

    def replace(self, owner_id: UUID, task: Task) -> Task | None: ...


class TaskService:
    def __init__(
        self,
        repository: TaskRepository,
        identity_repository: IdentityRepository,
    ) -> None:
        self._repository = repository
        self._identity_repository = identity_repository

    def _owner_id(self) -> UUID | None:
        state = self._identity_repository.current()
        return state.user.id if state is not None else None

    def create(
        self,
        workspace_id: UUID,
        command: TaskCreate,
        *,
        source_tool_call_id: UUID | None = None,
    ) -> Task | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        now = datetime.now(UTC)
        task = Task(
            id=uuid4(),
            workspace_id=workspace_id,
            title=command.title,
            description=command.description,
            status=TaskStatus.PENDING,
            priority=command.priority,
            due_at=command.due_at,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add(
            owner_id,
            task,
            source_tool_call_id=source_tool_call_id,
        )

    def get(self, workspace_id: UUID, task_id: UUID) -> Task | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.get(owner_id, workspace_id, task_id)

    def get_by_source_tool_call(
        self,
        workspace_id: UUID,
        source_tool_call_id: UUID,
    ) -> Task | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.get_by_source_tool_call(
            owner_id,
            workspace_id,
            source_tool_call_id,
        )

    def list(self, workspace_id: UUID) -> list[Task] | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        tasks = self._repository.list(owner_id, workspace_id)
        if tasks is None:
            return None
        return sorted(tasks, key=lambda task: task.created_at)

    def update(
        self,
        workspace_id: UUID,
        task_id: UUID,
        command: TaskUpdate,
    ) -> Task | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        task = self._repository.get(owner_id, workspace_id, task_id)
        if task is None:
            return None

        changes = command.model_dump(exclude_unset=True)
        if not changes:
            return task
        changes["updated_at"] = datetime.now(UTC)
        updated = task.model_copy(update=changes)
        return self._repository.replace(owner_id, updated)
