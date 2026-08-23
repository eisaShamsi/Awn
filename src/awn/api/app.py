"""FastAPI application factory."""

from fastapi import FastAPI

from awn import __version__
from awn.agent.gateway import build_model_gateway
from awn.api.routes import health, tasks
from awn.application.tasks import InMemoryTaskRepository, TaskService
from awn.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        description="Arabic-first personal agent with permission-aware execution.",
    )
    app.state.settings = resolved_settings
    app.state.model_gateway = build_model_gateway(resolved_settings)
    app.state.task_service = TaskService(InMemoryTaskRepository())

    app.include_router(health.router)
    app.include_router(tasks.router, prefix=resolved_settings.api_prefix)
    return app
