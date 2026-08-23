"""Initial task-management API."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from awn.api.dependencies import TaskServiceDependency
from awn.domain.tasks import Task, TaskCreate, TaskUpdate

router = APIRouter(prefix="/workspaces/{workspace_id}/tasks", tags=["tasks"])


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(
    workspace_id: UUID,
    command: TaskCreate,
    service: TaskServiceDependency,
) -> Task:
    task = service.create(workspace_id, command)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return task


@router.get("", response_model=list[Task])
def list_tasks(workspace_id: UUID, service: TaskServiceDependency) -> list[Task]:
    tasks = service.list(workspace_id)
    if tasks is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return tasks


@router.get("/{task_id}", response_model=Task)
def get_task(workspace_id: UUID, task_id: UUID, service: TaskServiceDependency) -> Task:
    task = service.get(workspace_id, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=Task)
def update_task(
    workspace_id: UUID,
    task_id: UUID,
    command: TaskUpdate,
    service: TaskServiceDependency,
) -> Task:
    task = service.update(workspace_id, task_id, command)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task
