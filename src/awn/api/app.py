"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from awn import __version__
from awn.agent.gateway import ModelGateway, build_model_gateway
from awn.api.routes import approvals, conversations, health, identity, runs, tasks
from awn.application.approvals import ApprovalService
from awn.application.conversations import ConversationService
from awn.application.execution import ExecutionService
from awn.application.identity import IdentityService
from awn.application.orchestrator import OrchestratorService
from awn.application.runs import RunService
from awn.application.tasks import TaskService
from awn.application.worker import WorkerService
from awn.config import Settings, get_settings
from awn.infrastructure.database import Database
from awn.infrastructure.filesystem import SafeWorkspaceFiles
from awn.infrastructure.persistence.approvals import SqlAlchemyApprovalRepository
from awn.infrastructure.persistence.conversations import SqlAlchemyConversationRepository
from awn.infrastructure.persistence.identity import SqlAlchemyIdentityRepository
from awn.infrastructure.persistence.runs import SqlAlchemyRunRepository
from awn.infrastructure.persistence.tasks import SqlAlchemyTaskRepository
from awn.infrastructure.persistence.tool_calls import SqlAlchemyToolCallRepository
from awn.policy.engine import PolicyEngine
from awn.tools.files import build_file_create_tool
from awn.tools.registry import ToolRegistry
from awn.tools.tasks import build_task_create_tool


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    model_gateway: ModelGateway | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_database = database or Database(resolved_settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        resolved_database.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        description="Arabic-first personal agent with permission-aware execution.",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.database = resolved_database
    resolved_gateway = model_gateway or build_model_gateway(resolved_settings)
    app.state.model_gateway = resolved_gateway
    identity_repository = SqlAlchemyIdentityRepository(resolved_database.session_factory)
    app.state.identity_service = IdentityService(identity_repository)
    conversation_repository = SqlAlchemyConversationRepository(resolved_database.session_factory)
    app.state.conversation_service = ConversationService(
        conversation_repository,
        identity_repository,
    )
    run_repository = SqlAlchemyRunRepository(resolved_database.session_factory)
    app.state.run_service = RunService(
        run_repository,
        identity_repository,
    )
    app.state.approval_service = ApprovalService(
        SqlAlchemyApprovalRepository(resolved_database.session_factory),
        identity_repository,
    )
    app.state.task_service = TaskService(
        SqlAlchemyTaskRepository(resolved_database.session_factory),
        identity_repository,
    )
    app.state.policy_engine = PolicyEngine()
    app.state.workspace_files = SafeWorkspaceFiles(resolved_settings.workspace_files_root)
    app.state.tool_registry = ToolRegistry(
        [
            build_task_create_tool(app.state.task_service),
            build_file_create_tool(app.state.workspace_files),
        ]
    )
    app.state.orchestrator_service = OrchestratorService(
        app.state.run_service,
        app.state.conversation_service,
        resolved_gateway,
        app.state.approval_service,
        app.state.tool_registry,
        app.state.policy_engine,
    )
    tool_call_repository = SqlAlchemyToolCallRepository(resolved_database.session_factory)
    app.state.execution_service = ExecutionService(
        tool_call_repository,
        identity_repository,
        app.state.run_service,
        app.state.approval_service,
        app.state.tool_registry,
        app.state.policy_engine,
        max_attempts=resolved_settings.worker_max_attempts,
    )
    app.state.worker_service = WorkerService(
        tool_call_repository,
        app.state.conversation_service,
        app.state.tool_registry,
        lease_seconds=resolved_settings.worker_lease_seconds,
    )

    app.include_router(health.router)
    app.include_router(identity.router, prefix=resolved_settings.api_prefix)
    app.include_router(conversations.router, prefix=resolved_settings.api_prefix)
    app.include_router(runs.router, prefix=resolved_settings.api_prefix)
    app.include_router(approvals.router, prefix=resolved_settings.api_prefix)
    app.include_router(tasks.router, prefix=resolved_settings.api_prefix)
    return app
