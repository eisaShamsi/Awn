"""FastAPI dependency accessors."""

from typing import Annotated

from fastapi import Depends, Request

from awn.application.approvals import ApprovalService
from awn.application.cancellations import CancellationService
from awn.application.conversations import ConversationService
from awn.application.execution import ExecutionService
from awn.application.identity import IdentityService
from awn.application.orchestrator import OrchestratorService
from awn.application.runs import RunService
from awn.application.tasks import TaskService
from awn.application.worker import WorkerService
from awn.config import Settings
from awn.infrastructure.database import Database


def get_task_service(request: Request) -> TaskService:
    return request.app.state.task_service


def get_identity_service(request: Request) -> IdentityService:
    return request.app.state.identity_service


def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service


def get_orchestrator_service(request: Request) -> OrchestratorService:
    return request.app.state.orchestrator_service


def get_approval_service(request: Request) -> ApprovalService:
    return request.app.state.approval_service


def get_execution_service(request: Request) -> ExecutionService:
    return request.app.state.execution_service


def get_cancellation_service(request: Request) -> CancellationService:
    return request.app.state.cancellation_service


def get_worker_service(request: Request) -> WorkerService:
    return request.app.state.worker_service


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


TaskServiceDependency = Annotated[TaskService, Depends(get_task_service)]
IdentityServiceDependency = Annotated[IdentityService, Depends(get_identity_service)]
ConversationServiceDependency = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]
RunServiceDependency = Annotated[RunService, Depends(get_run_service)]
OrchestratorServiceDependency = Annotated[
    OrchestratorService,
    Depends(get_orchestrator_service),
]
ApprovalServiceDependency = Annotated[ApprovalService, Depends(get_approval_service)]
ExecutionServiceDependency = Annotated[ExecutionService, Depends(get_execution_service)]
CancellationServiceDependency = Annotated[
    CancellationService,
    Depends(get_cancellation_service),
]
WorkerServiceDependency = Annotated[WorkerService, Depends(get_worker_service)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
DatabaseDependency = Annotated[Database, Depends(get_database)]
