"""Workspace-scoped conversation and message use cases."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from awn.domain.conversations import (
    Conversation,
    ConversationCreate,
    ConversationStatus,
    Message,
    MessageRole,
    UserMessageCreate,
)
from awn.domain.identity import SetupState


class CurrentIdentityRepository(Protocol):
    def current(self) -> SetupState | None: ...


class ConversationRepository(Protocol):
    def add_conversation(
        self,
        owner_id: UUID,
        conversation: Conversation,
    ) -> Conversation | None: ...

    def get_conversation(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None: ...

    def list_conversations(
        self,
        owner_id: UUID,
        workspace_id: UUID,
    ) -> Iterable[Conversation] | None: ...

    def add_message(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        message: Message,
    ) -> Message | None: ...

    def list_messages(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> Iterable[Message] | None: ...


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        identity_repository: CurrentIdentityRepository,
    ) -> None:
        self._repository = repository
        self._identity_repository = identity_repository

    def _owner_id(self) -> UUID | None:
        state = self._identity_repository.current()
        return state.user.id if state is not None else None

    def create(
        self,
        workspace_id: UUID,
        command: ConversationCreate,
    ) -> Conversation | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None

        now = datetime.now(UTC)
        conversation = Conversation(
            id=uuid4(),
            workspace_id=workspace_id,
            title=command.title,
            status=ConversationStatus.ACTIVE,
            summary=None,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add_conversation(owner_id, conversation)

    def get(self, workspace_id: UUID, conversation_id: UUID) -> Conversation | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.get_conversation(owner_id, workspace_id, conversation_id)

    def list(self, workspace_id: UUID) -> list[Conversation] | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        conversations = self._repository.list_conversations(owner_id, workspace_id)
        return list(conversations) if conversations is not None else None

    def add_user_message(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        command: UserMessageCreate,
    ) -> Message | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None

        message = Message(
            id=uuid4(),
            conversation_id=conversation_id,
            role=MessageRole.USER,
            parts=command.parts,
            created_at=datetime.now(UTC),
        )
        return self._repository.add_message(owner_id, workspace_id, message)

    def list_messages(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> list[Message] | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        messages = self._repository.list_messages(
            owner_id,
            workspace_id,
            conversation_id,
        )
        return list(messages) if messages is not None else None
