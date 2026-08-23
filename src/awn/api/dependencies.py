"""FastAPI dependency accessors."""

from typing import Annotated

from fastapi import Depends, Request

from awn.application.tasks import TaskService
from awn.config import Settings
from awn.infrastructure.database import Database


def get_task_service(request: Request) -> TaskService:
    return request.app.state.task_service


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


TaskServiceDependency = Annotated[TaskService, Depends(get_task_service)]
SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
DatabaseDependency = Annotated[Database, Depends(get_database)]
