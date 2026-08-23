"""Liveness endpoint without external dependency checks."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from awn import __version__
from awn.api.dependencies import SettingsDependency

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    name: str
    version: str
    environment: str
    model_provider: str


@router.get("/health", response_model=HealthResponse)
def health(settings: SettingsDependency) -> HealthResponse:
    return HealthResponse(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment,
        model_provider=settings.model_provider,
    )
