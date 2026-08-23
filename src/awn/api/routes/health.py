"""Liveness endpoint without external dependency checks."""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from awn import __version__
from awn.api.dependencies import DatabaseDependency, SettingsDependency

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    name: str
    version: str
    environment: str
    model_provider: str


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"
    database: str


@router.get("/health", response_model=HealthResponse)
def health(settings: SettingsDependency) -> HealthResponse:
    return HealthResponse(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment,
        model_provider=settings.model_provider,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Database unavailable"}},
)
def readiness(database: DatabaseDependency) -> ReadinessResponse:
    try:
        database.ping()
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable",
        ) from error
    return ReadinessResponse(database=database.dialect_name)
