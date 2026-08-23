"""Workspace-scoped run observation API."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from awn.api.dependencies import OrchestratorServiceDependency, RunServiceDependency
from awn.domain.runs import PlanStep, Run, RunCreate

router = APIRouter(
    prefix="/workspaces/{workspace_id}/conversations/{conversation_id}/runs",
    tags=["runs"],
)


@router.post("", response_model=Run, status_code=status.HTTP_201_CREATED)
def create_run(
    workspace_id: UUID,
    conversation_id: UUID,
    command: RunCreate,
    background_tasks: BackgroundTasks,
    service: RunServiceDependency,
    orchestrator: OrchestratorServiceDependency,
) -> Run:
    run = service.create(workspace_id, conversation_id, command)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation or request message not found",
        )
    background_tasks.add_task(
        orchestrator.plan,
        workspace_id,
        conversation_id,
        run.id,
    )
    return run


@router.get("", response_model=list[Run])
def list_runs(
    workspace_id: UUID,
    conversation_id: UUID,
    service: RunServiceDependency,
) -> list[Run]:
    runs = service.list(workspace_id, conversation_id)
    if runs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return runs


@router.get("/{run_id}", response_model=Run)
def get_run(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    service: RunServiceDependency,
) -> Run:
    run = service.get(workspace_id, conversation_id, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.get("/{run_id}/steps", response_model=list[PlanStep])
def list_run_steps(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    service: RunServiceDependency,
) -> list[PlanStep]:
    steps = service.list_steps(workspace_id, conversation_id, run_id)
    if steps is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return steps
