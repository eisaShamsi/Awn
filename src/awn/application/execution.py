"""Policy-checked execution of durable, idempotent tool calls."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from awn.application.approvals import ApprovalService
from awn.application.conversations import ConversationService
from awn.application.identity import IdentityRepository
from awn.application.runs import RunService
from awn.domain.approvals import ApprovalStatus
from awn.domain.runs import PlanStep, Run, RunStatus
from awn.domain.tasks import Task
from awn.domain.tool_calls import ToolCall, ToolCallStatus
from awn.policy.engine import ActionRequest, AutonomyLevel, PolicyEngine, PolicyOutcome
from awn.tools.contracts import ToolContext
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
    ) -> Iterable[ToolCall] | None: ...

    def list(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Iterable[ToolCall] | None: ...

    def succeed(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        output: dict[str, object],
        *,
        completed_at: datetime,
    ) -> Run | None: ...

    def fail(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        error_code: str,
        *,
        completed_at: datetime,
    ) -> Run | None: ...


class ExecutionService:
    def __init__(
        self,
        repository: ToolCallRepository,
        identity_repository: IdentityRepository,
        runs: RunService,
        approvals: ApprovalService,
        conversations: ConversationService,
        registry: ToolRegistry,
        policy: PolicyEngine,
    ) -> None:
        self._repository = repository
        self._identity_repository = identity_repository
        self._runs = runs
        self._approvals = approvals
        self._conversations = conversations
        self._registry = registry
        self._policy = policy

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
        )
        if calls is None:
            return self._runs.get(workspace_id, conversation_id, run_id)

        current_run: Run | None = run
        for call in calls:
            if call.status is ToolCallStatus.SUCCEEDED:
                continue
            current_run = self._execute_call(owner_id, run, call)
            if current_run is None or current_run.status is RunStatus.FAILED:
                break
        return current_run

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

    def _execute_call(self, owner_id: UUID, run: Run, call: ToolCall) -> Run | None:
        context = ToolContext(
            owner_id=owner_id,
            workspace_id=run.workspace_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            trace_id=run.trace_id,
            tool_call_id=call.id,
            idempotency_key=call.idempotency_key,
        )
        try:
            output = self._registry.execute(
                call.tool_name,
                call.operation,
                call.input,
                context,
            )
            output_data = output.model_dump(mode="json")
            completed = self._repository.succeed(
                owner_id,
                run.workspace_id,
                run.conversation_id,
                call.id,
                output_data,
                completed_at=datetime.now(UTC),
            )
            self._add_success_message(run, output)
            return completed
        except Exception as error:
            logger.warning(
                "Tool call %s failed (%s)",
                call.id,
                type(error).__name__,
            )
            failed = self._repository.fail(
                owner_id,
                run.workspace_id,
                run.conversation_id,
                call.id,
                "TOOL_EXECUTION_FAILED",
                completed_at=datetime.now(UTC),
            )
            self._conversations.add_assistant_message(
                run.workspace_id,
                run.conversation_id,
                "تعذر تنفيذ الأداة المصرح بها. حُفظ الفشل دون ادعاء نجاح أو تكرار الأثر.",
            )
            return failed

    def _add_success_message(self, run: Run, output: object) -> None:
        if isinstance(output, Task):
            message = f"تم إنشاء المهمة «{output.title}» داخل مساحة العمل بنجاح."
        else:
            message = "تم تنفيذ الأداة المصرح بها والتحقق من نتيجتها."
        self._conversations.add_assistant_message(
            run.workspace_id,
            run.conversation_id,
            message,
        )
