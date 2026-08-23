"""Policy-checked enqueueing of approved, durable tool calls."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from awn.application.approvals import ApprovalService
from awn.application.identity import IdentityRepository
from awn.application.runs import RunService
from awn.domain.approvals import ApprovalStatus
from awn.domain.runs import PlanStep, Run
from awn.domain.tool_calls import ToolCall
from awn.policy.engine import ActionRequest, AutonomyLevel, PolicyEngine, PolicyOutcome
from awn.tools.registry import ToolRegistry, ToolRegistryError

logger = logging.getLogger(__name__)


class ToolCallRepository(Protocol):
    def prepare_approved(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        allowed_step_ids: frozenset[UUID],
        *,
        started_at: datetime,
        max_attempts: int,
    ) -> Iterable[ToolCall] | None: ...

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Iterable[ToolCall] | None: ...


class ExecutionService:
    def __init__(
        self,
        repository: ToolCallRepository,
        identity_repository: IdentityRepository,
        runs: RunService,
        approvals: ApprovalService,
        registry: ToolRegistry,
        policy: PolicyEngine,
        *,
        max_attempts: int,
    ) -> None:
        self._repository = repository
        self._identity_repository = identity_repository
        self._runs = runs
        self._approvals = approvals
        self._registry = registry
        self._policy = policy
        self._max_attempts = max_attempts

    def _owner_id(self) -> UUID | None:
        state = self._identity_repository.current()
        return state.user.id if state is not None else None

    def list(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> list[ToolCall] | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        calls = self._repository.list(owner_id, workspace_id, conversation_id, run_id)
        return list(calls) if calls is not None else None

    def execute_approved(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
    ) -> Run | None:
        owner_id = self._owner_id()
        run = self._runs.get(workspace_id, conversation_id, run_id)
        steps = self._runs.list_steps(workspace_id, conversation_id, run_id)
        approval = self._approvals.get(
            workspace_id,
            conversation_id,
            run_id,
            approval_id,
        )
        if owner_id is None or run is None or steps is None or approval is None:
            return None
        if approval.status not in {ApprovalStatus.APPROVED, ApprovalStatus.CONSUMED}:
            return run

        action_steps = tuple(step for step in steps if step.tool_name is not None)
        if not action_steps:
            return run
        if not self._all_actions_allowed(run, action_steps):
            logger.warning("Policy refused an approved action for run %s", run.id)
            return run

        calls = self._repository.prepare_approved(
            owner_id,
            workspace_id,
            conversation_id,
            run_id,
            approval_id,
            frozenset(step.id for step in action_steps),
            started_at=datetime.now(UTC),
            max_attempts=self._max_attempts,
        )
        return self._runs.get(workspace_id, conversation_id, run_id) if calls is not None else run

    def _all_actions_allowed(self, run: Run, steps: tuple[PlanStep, ...]) -> bool:
        for step in steps:
            assert step.tool_name is not None
            assert step.operation is not None
            assert step.tool_input is not None
            definition = self._registry.resolve(step.tool_name, step.operation)
            if definition is None:
                return False
            try:
                self._registry.validate_input(
                    step.tool_name,
                    step.operation,
                    step.tool_input,
                )
            except ToolRegistryError:
                return False
            decision = self._policy.decide(
                ActionRequest(
                    operation=definition.identifier,
                    risk=definition.risk,
                    side_effect=definition.side_effect,
                    external=definition.external,
                ),
                autonomy=AutonomyLevel(run.autonomy_level),
                has_matching_approval=True,
            )
            if decision.outcome is not PolicyOutcome.ALLOW:
                return False
        return True
