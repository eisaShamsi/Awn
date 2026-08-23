"""SQLAlchemy persistence for workspace-scoped conversations and messages."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessagePart,
    MessageRole,
)
from awn.infrastructure.persistence.models import (
    ConversationRecord,
    MessageRecord,
    WorkspaceRecord,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _conversation(record: ConversationRecord) -> Conversation:
    return Conversation(
        id=record.id,
        workspace_id=record.workspace_id,
        title=record.title,
        status=ConversationStatus(record.status),
        summary=record.summary,
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
    )


def _message(record: MessageRecord) -> Message:
    return Message(
        id=record.id,
        conversation_id=record.conversation_id,
        role=MessageRole(record.role),
        parts=tuple(MessagePart.model_validate(part) for part in record.content),
        created_at=_aware(record.created_at),
    )


class SqlAlchemyConversationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _scoped_conversation_statement(
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ):
        return (
            select(ConversationRecord)
            .join(WorkspaceRecord)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                ConversationRecord.workspace_id == workspace_id,
                ConversationRecord.id == conversation_id,
            )
        )

    def add_conversation(
        self,
        owner_id: UUID,
        conversation: Conversation,
    ) -> Conversation | None:
        workspace_statement = select(WorkspaceRecord.id).where(
            WorkspaceRecord.id == conversation.workspace_id,
            WorkspaceRecord.owner_id == owner_id,
        )
        with self._session_factory.begin() as session:
            if session.scalar(workspace_statement) is None:
                return None
            record = ConversationRecord(
                id=conversation.id,
                workspace_id=conversation.workspace_id,
                title=conversation.title,
                status=conversation.status.value,
                summary=conversation.summary,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
            session.add(record)
            session.flush()
            return _conversation(record)

    def get_conversation(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> Conversation | None:
        statement = self._scoped_conversation_statement(
            owner_id,
            workspace_id,
            conversation_id,
        )
        with self._session_factory() as session:
            record = session.scalar(statement)
            return _conversation(record) if record is not None else None

    def list_conversations(
        self,
        owner_id: UUID,
        workspace_id: UUID,
    ) -> Iterable[Conversation] | None:
        workspace_statement = select(WorkspaceRecord.id).where(
            WorkspaceRecord.id == workspace_id,
            WorkspaceRecord.owner_id == owner_id,
        )
        statement = (
            select(ConversationRecord)
            .join(WorkspaceRecord)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                ConversationRecord.workspace_id == workspace_id,
            )
            .order_by(ConversationRecord.updated_at.desc(), ConversationRecord.id)
        )
        with self._session_factory() as session:
            if session.scalar(workspace_statement) is None:
                return None
            return tuple(_conversation(record) for record in session.scalars(statement))

    def add_message(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        message: Message,
    ) -> Message | None:
        statement = self._scoped_conversation_statement(
            owner_id,
            workspace_id,
            message.conversation_id,
        )
        with self._session_factory.begin() as session:
            conversation = session.scalar(statement)
            if conversation is None:
                return None
            record = MessageRecord(
                id=message.id,
                conversation_id=message.conversation_id,
                role=message.role.value,
                content=[part.model_dump(mode="json", exclude_none=True) for part in message.parts],
                created_at=message.created_at,
            )
            conversation.updated_at = message.created_at
            session.add(record)
            session.flush()
            return _message(record)

    def get_message(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
    ) -> Message | None:
        statement = (
            select(MessageRecord)
            .join(ConversationRecord)
            .join(WorkspaceRecord)
            .where(
                WorkspaceRecord.owner_id == owner_id,
                ConversationRecord.workspace_id == workspace_id,
                ConversationRecord.id == conversation_id,
                MessageRecord.conversation_id == conversation_id,
                MessageRecord.id == message_id,
            )
        )
        with self._session_factory() as session:
            record = session.scalar(statement)
            return _message(record) if record is not None else None

    def list_messages(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> Iterable[Message] | None:
        conversation_statement = self._scoped_conversation_statement(
            owner_id,
            workspace_id,
            conversation_id,
        ).with_only_columns(ConversationRecord.id)
        message_statement = (
            select(MessageRecord)
            .where(MessageRecord.conversation_id == conversation_id)
            .order_by(MessageRecord.created_at, MessageRecord.id)
        )
        with self._session_factory() as session:
            if session.scalar(conversation_statement) is None:
                return None
            return tuple(_message(record) for record in session.scalars(message_statement))
