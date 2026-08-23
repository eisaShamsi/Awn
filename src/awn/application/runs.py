"""Workspace-scoped run creation and observation use cases."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from awn.domain.identity import SetupState
from awn.domain.runs import PlanStep, Run, RunCreate, RunRisk, RunStatus


class CurrentIdentityRepository(Protocol):
    def current(self) -> SetupState | None: ...


class RunRepository(Protocol):
    def add(self, owner_id: UUID, run: Run) -> Run | None: ...

    def get(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Run | None: ...

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
    ) -> Iterable[Run] | None: ...

    def list_steps(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Iterable[PlanStep] | None: ...

    def save(
        self,
        owner_id: UUID,
        run: Run,
        steps: Iterable[PlanStep] | None = None,
    ) -> Run | None: ...


class RunService:
    def __init__(
        self,
        repository: RunRepository,
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
        conversation_id: UUID,
        command: RunCreate,
    ) -> Run | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None

        now = datetime.now(UTC)
        run = Run(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            request_message_id=command.request_message_id,
            trace_id=uuid4(),
            status=RunStatus.RECEIVED,
            risk=RunRisk.LOW,
            autonomy_level=command.autonomy_level,
            created_at=now,
            updated_at=now,
        )
        return self._repository.add(owner_id, run)

    def get(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Run | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.get(owner_id, workspace_id, conversation_id, run_id)

    def list(self, workspace_id: UUID, conversation_id: UUID) -> list[Run] | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        runs = self._repository.list(owner_id, workspace_id, conversation_id)
        return list(runs) if runs is not None else None

    def list_steps(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> list[PlanStep] | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        steps = self._repository.list_steps(
            owner_id,
            workspace_id,
            conversation_id,
            run_id,
        )
        return list(steps) if steps is not None else None

    def save(
        self,
        run: Run,
        steps: Iterable[PlanStep] | None = None,
    ) -> Run | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.save(owner_id, run, steps)
