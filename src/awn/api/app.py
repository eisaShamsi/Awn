"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from awn import __version__
from awn.agent.gateway import build_model_gateway
from awn.api.routes import conversations, health, identity, runs, tasks
from awn.application.conversations import ConversationService
from awn.application.identity import IdentityService
from awn.application.runs import RunService
from awn.application.tasks import TaskService
from awn.config import Settings, get_settings
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.conversations import SqlAlchemyConversationRepository
from awn.infrastructure.persistence.identity import SqlAlchemyIdentityRepository
from awn.infrastructure.persistence.runs import SqlAlchemyRunRepository
from awn.infrastructure.persistence.tasks import SqlAlchemyTaskRepository


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
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
    app.state.model_gateway = build_model_gateway(resolved_settings)
    identity_repository = SqlAlchemyIdentityRepository(resolved_database.session_factory)
    app.state.identity_service = IdentityService(identity_repository)
    app.state.conversation_service = ConversationService(
        SqlAlchemyConversationRepository(resolved_database.session_factory),
        identity_repository,
    )
    app.state.run_service = RunService(
        SqlAlchemyRunRepository(resolved_database.session_factory),
        identity_repository,
    )
    app.state.task_service = TaskService(
        SqlAlchemyTaskRepository(resolved_database.session_factory)
    )

    app.include_router(health.router)
    app.include_router(identity.router, prefix=resolved_settings.api_prefix)
    app.include_router(conversations.router, prefix=resolved_settings.api_prefix)
    app.include_router(runs.router, prefix=resolved_settings.api_prefix)
    app.include_router(tasks.router, prefix=resolved_settings.api_prefix)
    return app
