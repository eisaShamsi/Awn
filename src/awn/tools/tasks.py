"""Internal task-management tool definitions."""

from awn.application.tasks import TaskService
from awn.domain.cancellations import CancellationEvidenceCode
from awn.domain.tasks import Task, TaskCreate
from awn.policy.engine import RiskLevel
from awn.tools.contracts import (
    EffectVerification,
    EffectVerificationStatus,
    ToolContext,
    ToolDefinition,
)


def build_task_create_tool(service: TaskService) -> ToolDefinition[TaskCreate, Task]:
    def create_task(context: ToolContext, command: TaskCreate) -> Task:
        task = service.create(
            context.workspace_id,
            command,
            source_tool_call_id=context.tool_call_id,
        )
        if task is None:
            raise LookupError("workspace is not available to the active user")
        return task

    def verify_task(context: ToolContext, _: TaskCreate) -> EffectVerification:
        task = service.get_by_source_tool_call(context.workspace_id, context.tool_call_id)
        return EffectVerification(
            EffectVerificationStatus.EFFECT_PRESENT
            if task is not None
            else EffectVerificationStatus.UNKNOWN,
            CancellationEvidenceCode.VALIDATED_TOOL_OUTPUT
            if task is not None
            else CancellationEvidenceCode.TASK_NOT_FOUND_BY_SOURCE_CALL,
            output=task,
        )

    return ToolDefinition(
        name="tasks",
        operation="create",
        summary="إنشاء مهمة داخل مساحة عمل عَوْن الحالية.",
        input_model=TaskCreate,
        output_model=Task,
        risk=RiskLevel.LOW,
        side_effect=True,
        external=False,
        reversible=True,
        required_scopes=("tasks.write",),
        timeout_seconds=5,
        supports_idempotency=True,
        handler=create_task,
        effect_verifier=verify_task,
    )
