"""Workspace-scoped approval use cases."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from awn.application.identity import IdentityRepository
from awn.domain.approvals import (
    ApprovalDecisionCommand,
    ApprovalDecisionResult,
    ApprovalRequest,
    ApprovalStatus,
    action_fingerprint,
)
from awn.domain.runs import PlanStep, Run, RunRisk

APPROVAL_OPERATION = "plan.execute"
DEFAULT_APPROVAL_TTL = timedelta(minutes=30)

_RISK_ORDER = {
    RunRisk.LOW: 0,
    RunRisk.MEDIUM: 1,
    RunRisk.HIGH: 2,
    RunRisk.CRITICAL: 3,
}


class ApprovalRepository(Protocol):
    def add_for_plan(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        approval: ApprovalRequest,
    ) -> ApprovalRequest | None: ...

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Iterable[ApprovalRequest] | None: ...

    def decide(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        command: ApprovalDecisionCommand,
        *,
        decided_at: datetime,
    ) -> ApprovalDecisionResult | None: ...


class ApprovalService:
    def __init__(
        self,
        repository: ApprovalRepository,
        identity_repository: IdentityRepository,
    ) -> None:
        self._repository = repository
        self._identity_repository = identity_repository

    def _owner_id(self) -> UUID | None:
        state = self._identity_repository.current()
        return state.user.id if state is not None else None

    def request_for_plan(
        self,
        run: Run,
        steps: tuple[PlanStep, ...] | list[PlanStep],
    ) -> ApprovalRequest | None:
        protected_steps = tuple(step for step in steps if step.requires_approval)
        if not protected_steps:
            return None

        owner_id = self._owner_id()
        if owner_id is None:
            return None

        now = datetime.now(UTC)
        risk = max((step.risk for step in protected_steps), key=_RISK_ORDER.__getitem__)
        approval = ApprovalRequest(
            id=uuid4(),
            run_id=run.id,
            operation=APPROVAL_OPERATION,
            summary=(
                f"السماح بتنفيذ {len(protected_steps)} من خطوات الخطة "
                "التي لها أثر أو حساسية تستلزم الموافقة."
            ),
            risk=risk,
            action_fingerprint=action_fingerprint(
                run,
                steps,
                operation=APPROVAL_OPERATION,
            ),
            status=ApprovalStatus.PENDING,
            requested_at=now,
            expires_at=now + DEFAULT_APPROVAL_TTL,
        )
        return self._repository.add_for_plan(
            owner_id,
            run.workspace_id,
            run.conversation_id,
            approval,
        )

    def list(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> list[ApprovalRequest] | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        approvals = self._repository.list(
            owner_id,
            workspace_id,
            conversation_id,
            run_id,
        )
        return list(approvals) if approvals is not None else None

    def decide(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        command: ApprovalDecisionCommand,
    ) -> ApprovalDecisionResult | None:
        owner_id = self._owner_id()
        if owner_id is None:
            return None
        return self._repository.decide(
            owner_id,
            workspace_id,
            conversation_id,
            run_id,
            approval_id,
            command,
            decided_at=datetime.now(UTC),
        )

    def get(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRequest | None:
        approvals = self.list(workspace_id, conversation_id, run_id)
        if approvals is None:
            return None
        return next(
            (approval for approval in approvals if approval.id == approval_id),
            None,
        )
