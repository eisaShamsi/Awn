"""Conversation and structured-message domain objects."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessagePartType(StrEnum):
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ARTIFACT = "artifact"


class MessagePart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: MessagePartType
    text: str | None = Field(default=None, max_length=100_000)
    data: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def require_content_for_part_type(self) -> Self:
        if self.type is MessagePartType.TEXT:
            if self.text is None or not self.text.strip():
                raise ValueError("text parts require non-blank text")
        elif self.data is None:
            raise ValueError(f"{self.type.value} parts require data")
        return self


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=300)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        return title or None


class UserMessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: tuple[MessagePart, ...] = Field(min_length=1)

    @field_validator("parts")
    @classmethod
    def user_parts_must_be_text(
        cls,
        value: tuple[MessagePart, ...],
    ) -> tuple[MessagePart, ...]:
        if any(part.type is not MessagePartType.TEXT for part in value):
            raise ValueError("user messages currently accept text parts only")
        return value


class Conversation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    workspace_id: UUID
    title: str | None = Field(default=None, max_length=300)
    status: ConversationStatus = ConversationStatus.ACTIVE
    summary: str | None = Field(default=None, max_length=20_000)
    created_at: datetime
    updated_at: datetime

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = value.strip()
        return title or None


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    conversation_id: UUID
    role: MessageRole
    parts: tuple[MessagePart, ...] = Field(min_length=1)
    created_at: datetime
