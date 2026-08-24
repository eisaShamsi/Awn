"""Use cases for owner-scoped run cancellation."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from awn.application.identity import IdentityRepository
from awn.domain.cancellations import CancellationRequestResult, RunCancellation


class CancellationRepository(Protocol):
    def request(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        *,
        received_at: datetime,
    ) -> CancellationRequestResult | None: ...

    def get(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> RunCancellation | None: ...


class CancellationService:
    def __init__(
        self,
        repository: CancellationRepository,
        identity_repository: IdentityRepository,
    ) -> None:
        self._repository = repository
        self._identity_repository = identity_repository

    def _owner_id(self) -> UUID | None:
        state = self._identity_repository.current()
        return state.user.id if state is not None else None

    def request(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> CancellationRequestResult | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.request(
            owner_id,
            workspace_id,
            conversation_id,
            run_id,
            received_at=datetime.now(UTC),
        )

    def get(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> RunCancellation | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.get(owner_id, workspace_id, conversation_id, run_id)
