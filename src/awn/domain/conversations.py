"""Conversation and structured-message domain objects."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


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
    model_config = ConfigDict(frozen=True)

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


class Conversation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    workspace_id: UUID
    title: str | None = Field(default=None, max_length=300)
    status: ConversationStatus = ConversationStatus.ACTIVE
    summary: str | None = Field(default=None, max_length=20_000)
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    conversation_id: UUID
    role: MessageRole
    parts: tuple[MessagePart, ...] = Field(min_length=1)
    created_at: datetime
