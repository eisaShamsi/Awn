"""Initial task-management API."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from awn.api.dependencies import TaskServiceDependency
from awn.domain.tasks import Task, TaskCreate, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(command: TaskCreate, service: TaskServiceDependency) -> Task:
    return service.create(command)


@router.get("", response_model=list[Task])
def list_tasks(service: TaskServiceDependency) -> list[Task]:
    return service.list()


@router.get("/{task_id}", response_model=Task)
def get_task(task_id: UUID, service: TaskServiceDependency) -> Task:
    task = service.get(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=Task)
def update_task(task_id: UUID, command: TaskUpdate, service: TaskServiceDependency) -> Task:
    task = service.update(task_id, command)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task
