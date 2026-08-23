"""Local-user setup and workspace API."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from awn.api.dependencies import IdentityServiceDependency
from awn.domain.identity import SetupCommand, SetupState, Workspace, WorkspaceCreate

router = APIRouter(tags=["identity"])


@router.post("/setup", response_model=SetupState)
def bootstrap(command: SetupCommand, service: IdentityServiceDependency) -> SetupState:
    return service.bootstrap(command)


@router.get("/setup", response_model=SetupState)
def get_setup(service: IdentityServiceDependency) -> SetupState:
    state = service.current()
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setup not found")
    return state


@router.post("/workspaces", response_model=Workspace, status_code=status.HTTP_201_CREATED)
def create_workspace(
    command: WorkspaceCreate,
    service: IdentityServiceDependency,
) -> Workspace:
    workspace = service.create_workspace(command)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete setup before creating a workspace",
        )
    return workspace


@router.get("/workspaces", response_model=list[Workspace])
def list_workspaces(service: IdentityServiceDependency) -> list[Workspace]:
    return service.list_workspaces()


@router.get("/workspaces/{workspace_id}", response_model=Workspace)
def get_workspace(workspace_id: UUID, service: IdentityServiceDependency) -> Workspace:
    workspace = service.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace
