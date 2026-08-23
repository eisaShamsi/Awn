"""Single-user setup and workspace use cases."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from awn.domain.identity import (
    SetupCommand,
    SetupState,
    User,
    Workspace,
    WorkspaceCreate,
    WorkspaceStatus,
)


class IdentityRepository(Protocol):
    def bootstrap(self, user: User, workspace: Workspace) -> SetupState: ...

    def current(self) -> SetupState | None: ...

    def add_workspace(self, workspace: Workspace) -> Workspace | None: ...

    def get_workspace(self, owner_id: UUID, workspace_id: UUID) -> Workspace | None: ...

    def list_workspaces(self, owner_id: UUID) -> Iterable[Workspace]: ...


class IdentityService:
    def __init__(self, repository: IdentityRepository) -> None:
        self._repository = repository

    def bootstrap(self, command: SetupCommand) -> SetupState:
        now = datetime.now(UTC)
        user = User(
            id=uuid4(),
            display_name=command.display_name,
            locale=command.locale,
            timezone=command.timezone,
            created_at=now,
            updated_at=now,
        )
        workspace = Workspace(
            id=uuid4(),
            owner_id=user.id,
            name=command.workspace_name,
            status=WorkspaceStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        return self._repository.bootstrap(user, workspace)

    def current(self) -> SetupState | None:
        return self._repository.current()

    def create_workspace(self, command: WorkspaceCreate) -> Workspace | None:
        state = self._repository.current()
        if state is None:
            return None

        now = datetime.now(UTC)
        workspace = Workspace(
            id=uuid4(),
            owner_id=state.user.id,
            name=command.name,
            status=WorkspaceStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add_workspace(workspace)

    def get_workspace(self, workspace_id: UUID) -> Workspace | None:
        state = self._repository.current()
        if state is None:
            return None
        return self._repository.get_workspace(state.user.id, workspace_id)

    def list_workspaces(self) -> list[Workspace]:
        state = self._repository.current()
        if state is None:
            return []
        return list(self._repository.list_workspaces(state.user.id))
