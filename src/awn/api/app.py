"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from awn import __version__
from awn.agent.gateway import build_model_gateway
from awn.api.routes import health, tasks
from awn.application.tasks import TaskService
from awn.config import Settings, get_settings
from awn.infrastructure.database import Database
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
    app.state.task_service = TaskService(
        SqlAlchemyTaskRepository(resolved_database.session_factory)
    )

    app.include_router(health.router)
    app.include_router(tasks.router, prefix=resolved_settings.api_prefix)
    return app
