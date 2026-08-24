"""Leased background worker for durable tool execution."""

from __future__ import annotations

import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel

from awn.application.conversations import ConversationService
from awn.domain.files import FileCreateResult
from awn.domain.runs import Run
from awn.domain.tasks import Task
from awn.domain.tool_calls import LeasedToolCall
from awn.tools.contracts import ToolContext
from awn.tools.registry import RetryableToolError, ToolRegistry

logger = logging.getLogger(__name__)


class WorkerRepository(Protocol):
    def reconcile_expired_cancellations(self, *, observed_at: datetime) -> int: ...

    def claim_next(
        self,
        worker_id: str,
        *,
        claimed_at: datetime,
        lease_seconds: int,
    ) -> LeasedToolCall | None: ...

    def commit_effect(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        *,
        worker_id: str,
    ) -> UUID | None: ...

    def succeed(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        output: dict[str, object],
        *,
        worker_id: str,
        expected_idempotency_key: str,
        expected_input: dict[str, object],
        effect_commit_token: UUID | None = None,
        occurred_at: datetime,
    ) -> Run | None: ...

    def record_failure(
        self,
        owner_id: UUID,
        workspace_id: UUID,
        conversation_id: UUID,
        call_id: UUID,
        error_code: str,
        *,
        worker_id: str,
        failed_at: datetime,
        retry_at: datetime | None,
        effect_commit_token: UUID | None = None,
    ) -> tuple[Run, bool] | None: ...


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"


class WorkerService:
    """Claims one job at a time; database leases make recovery safe across processes."""

    def __init__(
        self,
        repository: WorkerRepository,
        conversations: ConversationService,
        registry: ToolRegistry,
        *,
        lease_seconds: int,
        worker_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._conversations = conversations
        self._registry = registry
        self._lease_seconds = lease_seconds
        self._worker_id = worker_id or _default_worker_id()

    def run_once(self, *, now: datetime | None = None) -> bool:
        claimed_at = now or datetime.now(UTC)
        reconciled = self._repository.reconcile_expired_cancellations(
            observed_at=claimed_at,
        )
        leased = self._repository.claim_next(
            self._worker_id,
            claimed_at=claimed_at,
            lease_seconds=self._lease_seconds,
        )
        if leased is None:
            return reconciled > 0

        call = leased.call
        definition = self._registry.resolve(call.tool_name, call.operation)
        if definition is None or not definition.supports_idempotency:
            self._terminal_failure(
                leased,
                datetime.now(UTC),
                "UNSAFE_OR_UNKNOWN_TOOL",
            )
            return True

        context = ToolContext(
            owner_id=leased.owner_id,
            workspace_id=leased.run.workspace_id,
            conversation_id=leased.run.conversation_id,
            run_id=leased.run.id,
            trace_id=leased.run.trace_id,
            tool_call_id=call.id,
            idempotency_key=call.idempotency_key,
        )
        effect_commit_token: UUID | None = None
        try:
            validated_input = self._registry.validate_input(
                call.tool_name,
                call.operation,
                call.input,
            )
            effect_commit_token = self._repository.commit_effect(
                leased.owner_id,
                leased.run.workspace_id,
                leased.run.conversation_id,
                call.id,
                worker_id=self._worker_id,
            )
            if effect_commit_token is None:
                return True
            output = self._registry.execute_validated(
                call.tool_name,
                call.operation,
                validated_input,
                context,
            )
            completed = self._repository.succeed(
                leased.owner_id,
                leased.run.workspace_id,
                leased.run.conversation_id,
                call.id,
                output.model_dump(mode="json"),
                worker_id=self._worker_id,
                expected_idempotency_key=call.idempotency_key,
                expected_input=call.input,
                effect_commit_token=effect_commit_token,
                occurred_at=datetime.now(UTC),
            )
            if completed is not None:
                self._add_success_message(leased.run, output)
        except (RetryableToolError, TimeoutError, ConnectionError) as error:
            self._retry_failure(
                leased,
                error,
                datetime.now(UTC),
                effect_commit_token,
            )
        except Exception as error:
            logger.warning("Tool call %s failed (%s)", call.id, type(error).__name__)
            self._terminal_failure(
                leased,
                datetime.now(UTC),
                "TOOL_EXECUTION_FAILED",
                effect_commit_token,
            )
        return True

    def run_until_idle(self, *, max_items: int = 100) -> int:
        processed = 0
        while processed < max_items and self.run_once():
            processed += 1
        return processed

    def _retry_failure(
        self,
        leased: LeasedToolCall,
        error: Exception,
        failed_at: datetime,
        effect_commit_token: UUID | None,
    ) -> None:
        delay_seconds = min(60, 2 ** max(0, leased.call.attempt_count - 1))
        result = self._repository.record_failure(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            type(error).__name__.upper(),
            worker_id=self._worker_id,
            failed_at=failed_at,
            retry_at=failed_at + timedelta(seconds=delay_seconds),
            effect_commit_token=effect_commit_token,
        )
        if result is not None and not result[1]:
            self._add_failure_message(leased.run)

    def _terminal_failure(
        self,
        leased: LeasedToolCall,
        failed_at: datetime,
        error_code: str,
        effect_commit_token: UUID | None = None,
    ) -> None:
        result = self._repository.record_failure(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            error_code,
            worker_id=self._worker_id,
            failed_at=failed_at,
            retry_at=None,
            effect_commit_token=effect_commit_token,
        )
        if result is not None:
            self._add_failure_message(leased.run)

    def _add_success_message(self, run: Run, output: BaseModel) -> None:
        if isinstance(output, Task):
            message = f"تم إنشاء المهمة «{output.title}» داخل مساحة العمل بنجاح."
        elif isinstance(output, FileCreateResult):
            message = f"تم إنشاء الملف «{output.path}» داخل مساحة العمل الآمنة بنجاح."
        else:
            message = "تم تنفيذ الأداة المصرح بها والتحقق من نتيجتها."
        self._conversations.add_assistant_message(
            run.workspace_id,
            run.conversation_id,
            message,
        )

    def _add_failure_message(self, run: Run) -> None:
        self._conversations.add_assistant_message(
            run.workspace_id,
            run.conversation_id,
            "تعذر تنفيذ الأداة المصرح بها. حُفظ الفشل دون ادعاء نجاح أو تكرار الأثر.",
        )
