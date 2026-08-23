"""User identity and workspace domain objects."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class SetupCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    workspace_name: str = Field(default="مساحة عَوْن", min_length=1, max_length=200)
    locale: str = Field(default="ar", min_length=2, max_length=16)
    timezone: str = Field(default="Asia/Dubai", min_length=1, max_length=64)

    @field_validator("display_name", "workspace_name", "locale", "timezone")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be blank")
        return name


class User(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    locale: str = Field(default="ar", min_length=2, max_length=16)
    timezone: str = Field(default="Asia/Dubai", min_length=1, max_length=64)
    created_at: datetime
    updated_at: datetime

    @field_validator("display_name", "locale", "timezone")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class Workspace(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    owner_id: UUID
    name: str = Field(min_length=1, max_length=200)
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    created_at: datetime
    updated_at: datetime

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be blank")
        return name


class SetupState(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: User
    workspace: Workspace
    created: bool
