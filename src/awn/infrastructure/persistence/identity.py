"""SQLAlchemy persistence for the local user and workspaces."""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from awn.domain.identity import SetupState, User, Workspace, WorkspaceStatus
from awn.infrastructure.persistence.models import UserRecord, WorkspaceRecord


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _user(record: UserRecord) -> User:
    return User(
        id=record.id,
        display_name=record.display_name,
        locale=record.locale,
        timezone=record.timezone,
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
    )


def _workspace(record: WorkspaceRecord) -> Workspace:
    return Workspace(
        id=record.id,
        owner_id=record.owner_id,
        name=record.name,
        status=WorkspaceStatus(record.status),
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
    )


def _user_record(user: User) -> UserRecord:
    return UserRecord(
        id=user.id,
        display_name=user.display_name,
        locale=user.locale,
        timezone=user.timezone,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _workspace_record(workspace: Workspace) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=workspace.id,
        owner_id=workspace.owner_id,
        name=workspace.name,
        status=workspace.status.value,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


class SqlAlchemyIdentityRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _first_user(session: Session) -> UserRecord | None:
        statement = select(UserRecord).order_by(UserRecord.created_at, UserRecord.id).limit(1)
        return session.scalar(statement)

    @staticmethod
    def _first_workspace(session: Session, owner_id: UUID) -> WorkspaceRecord | None:
        statement = (
            select(WorkspaceRecord)
            .where(WorkspaceRecord.owner_id == owner_id)
            .order_by(WorkspaceRecord.created_at, WorkspaceRecord.id)
            .limit(1)
        )
        return session.scalar(statement)

    def bootstrap(self, user: User, workspace: Workspace) -> SetupState:
        with self._session_factory.begin() as session:
            user_record = self._first_user(session)
            created = user_record is None
            if user_record is None:
                user_record = _user_record(user)
                session.add(user_record)
                session.flush()

            workspace_record = self._first_workspace(session, user_record.id)
            if workspace_record is None:
                workspace_record = _workspace_record(
                    workspace.model_copy(update={"owner_id": user_record.id})
                )
                session.add(workspace_record)
                session.flush()
                created = True

            return SetupState(
                user=_user(user_record),
                workspace=_workspace(workspace_record),
                created=created,
            )

    def current(self) -> SetupState | None:
        with self._session_factory() as session:
            user_record = self._first_user(session)
            if user_record is None:
                return None
            workspace_record = self._first_workspace(session, user_record.id)
            if workspace_record is None:
                return None
            return SetupState(
                user=_user(user_record),
                workspace=_workspace(workspace_record),
                created=False,
            )

    def add_workspace(self, workspace: Workspace) -> Workspace | None:
        with self._session_factory.begin() as session:
            if session.get(UserRecord, workspace.owner_id) is None:
                return None
            record = _workspace_record(workspace)
            session.add(record)
            session.flush()
            return _workspace(record)

    def get_workspace(self, owner_id: UUID, workspace_id: UUID) -> Workspace | None:
        statement = select(WorkspaceRecord).where(
            WorkspaceRecord.id == workspace_id,
            WorkspaceRecord.owner_id == owner_id,
        )
        with self._session_factory() as session:
            record = session.scalar(statement)
            return _workspace(record) if record is not None else None

    def list_workspaces(self, owner_id: UUID) -> Iterable[Workspace]:
        statement = (
            select(WorkspaceRecord)
            .where(WorkspaceRecord.owner_id == owner_id)
            .order_by(WorkspaceRecord.created_at, WorkspaceRecord.id)
        )
        with self._session_factory() as session:
            return tuple(_workspace(record) for record in session.scalars(statement))
