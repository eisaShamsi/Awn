"""Turn a saved user request into an answer, clarification, or persisted plan."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from uuid import UUID, uuid4

from awn.agent.gateway import ModelGateway, ModelRequest
from awn.agent.planning import OrchestrationDecision, OrchestrationKind
from awn.application.conversations import ConversationService
from awn.application.runs import RunService
from awn.domain.conversations import Message, MessageRole
from awn.domain.runs import PlanStep, Run, RunRisk, RunStatus

logger = logging.getLogger(__name__)

ORCHESTRATOR_INSTRUCTIONS = """أنت منسق عَوْن، مساعد شخصي عربي الهوية.
صنّف الطلب إلى واحد من ثلاثة أنواع: answer أو plan أو clarification.
- استخدم answer عندما تكفي إجابة نصية ولا يوجد عمل مطلوب تنفيذه.
- استخدم plan عندما يطلب المستخدم إنشاء ناتج أو إنجاز عمل، وقدّم خطوات قليلة واضحة.
- استخدم clarification فقط عندما ينقص تفصيل سيغير النتيجة جوهريًا ولا يوجد افتراض آمن.
لا تدّع تنفيذ أي خطوة أو أداة. هذه المرحلة تخطيط فقط.
صنّف مخاطر الخطوات بصدق، واجعل requires_approval صحيحًا لأي أثر خارجي أو حساس.
أجب بلغة طلب المستخدم وبأسلوب موجز وواضح."""

_RISK_ORDER = {
    RunRisk.LOW: 0,
    RunRisk.MEDIUM: 1,
    RunRisk.HIGH: 2,
    RunRisk.CRITICAL: 3,
}


def _message_text(message: Message) -> str:
    return "\n".join(part.text for part in message.parts if part.text).strip()


def _build_context(messages: Iterable[Message], current: Message) -> str:
    recent = list(messages)[-12:]
    lines: list[str] = []
    for message in recent:
        role = "المستخدم" if message.role is MessageRole.USER else "عَوْن"
        text = _message_text(message)
        if text:
            lines.append(f"{role}: {text[:4_000]}")
    history = "\n".join(lines)[-20_000:]
    return f"سياق المحادثة القريب:\n{history}\n\nالطلب الحالي:\n{_message_text(current)}"


class OrchestratorService:
    def __init__(
        self,
        runs: RunService,
        conversations: ConversationService,
        gateway: ModelGateway,
    ) -> None:
        self._runs = runs
        self._conversations = conversations
        self._gateway = gateway

    async def plan(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> Run | None:
        run = self._runs.get(workspace_id, conversation_id, run_id)
        if run is None or run.status is not RunStatus.RECEIVED:
            return run

        planning = self._runs.save(run.transition_to(RunStatus.PLANNING))
        if planning is None or planning.request_message_id is None:
            return planning

        request_message = self._conversations.get_message(
            workspace_id,
            conversation_id,
            planning.request_message_id,
        )
        messages = self._conversations.list_messages(workspace_id, conversation_id)
        if request_message is None or messages is None:
            return self._fail(planning, "REQUEST_CONTEXT_MISSING")

        try:
            response = await self._gateway.complete_structured(
                ModelRequest(
                    instructions=ORCHESTRATOR_INSTRUCTIONS,
                    input=_build_context(messages, request_message),
                    max_output_tokens=1_200,
                ),
                OrchestrationDecision,
            )
            return self._apply_decision(planning, response.output)
        except Exception as error:
            logger.warning(
                "Planning failed for run %s (%s)",
                planning.id,
                type(error).__name__,
            )
            return self._fail(planning, "MODEL_GATEWAY_ERROR")

    def _apply_decision(
        self,
        planning: Run,
        decision: OrchestrationDecision,
    ) -> Run | None:
        assistant_message = self._conversations.add_assistant_message(
            planning.workspace_id,
            planning.conversation_id,
            decision.message,
        )
        if assistant_message is None:
            return self._fail(planning, "ASSISTANT_MESSAGE_SAVE_FAILED", add_message=False)

        if decision.kind is OrchestrationKind.CLARIFICATION:
            return self._runs.save(planning.transition_to(RunStatus.NEEDS_CLARIFICATION))

        if decision.kind is OrchestrationKind.ANSWER:
            completed = (
                planning.transition_to(RunStatus.READY)
                .transition_to(RunStatus.EXECUTING)
                .transition_to(RunStatus.VERIFYING)
                .transition_to(RunStatus.SUCCEEDED)
            )
            return self._runs.save(completed)

        steps = tuple(
            PlanStep(
                id=uuid4(),
                run_id=planning.id,
                position=position,
                title=proposed.title,
                risk=proposed.risk,
                requires_approval=proposed.requires_approval,
                created_at=planning.updated_at,
                updated_at=planning.updated_at,
            )
            for position, proposed in enumerate(decision.steps)
        )
        risk = max((step.risk for step in steps), key=_RISK_ORDER.__getitem__)
        ready = planning.model_copy(update={"risk": risk}).transition_to(RunStatus.READY)
        return self._runs.save(ready, steps)

    def _fail(
        self,
        planning: Run,
        error_code: str,
        *,
        add_message: bool = True,
    ) -> Run | None:
        if add_message:
            failure_message = (
                "تعذر على عَوْن إعداد الإجابة أو الخطة الآن. "
                "أعد المحاولة بعد التحقق من إعداد مزود النموذج."
            )
            self._conversations.add_assistant_message(
                planning.workspace_id,
                planning.conversation_id,
                failure_message,
            )
        failed = planning.model_copy(update={"error_code": error_code}).transition_to(
            RunStatus.FAILED
        )
        return self._runs.save(failed)
