"""Workspace-scoped conversation and message API."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from awn.api.dependencies import ConversationServiceDependency
from awn.domain.conversations import (
    Conversation,
    ConversationCreate,
    Message,
    UserMessageCreate,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/conversations",
    tags=["conversations"],
)


@router.post("", response_model=Conversation, status_code=status.HTTP_201_CREATED)
def create_conversation(
    workspace_id: UUID,
    command: ConversationCreate,
    service: ConversationServiceDependency,
) -> Conversation:
    conversation = service.create(workspace_id, command)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return conversation


@router.get("", response_model=list[Conversation])
def list_conversations(
    workspace_id: UUID,
    service: ConversationServiceDependency,
) -> list[Conversation]:
    conversations = service.list(workspace_id)
    if conversations is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return conversations


@router.get("/{conversation_id}", response_model=Conversation)
def get_conversation(
    workspace_id: UUID,
    conversation_id: UUID,
    service: ConversationServiceDependency,
) -> Conversation:
    conversation = service.get(workspace_id, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conversation


@router.post(
    "/{conversation_id}/messages",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
)
def add_user_message(
    workspace_id: UUID,
    conversation_id: UUID,
    command: UserMessageCreate,
    service: ConversationServiceDependency,
) -> Message:
    message = service.add_user_message(workspace_id, conversation_id, command)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return message


@router.get("/{conversation_id}/messages", response_model=list[Message])
def list_messages(
    workspace_id: UUID,
    conversation_id: UUID,
    service: ConversationServiceDependency,
) -> list[Message]:
    messages = service.list_messages(workspace_id, conversation_id)
    if messages is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return messages
