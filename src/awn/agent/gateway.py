"""Provider-neutral model gateway."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from awn.config import Settings


class ModelRequest(BaseModel):
    """The minimal provider-neutral request used by the first Awn kernel."""

    instructions: str = Field(min_length=1)
    input: str = Field(min_length=1)
    max_output_tokens: int = Field(default=800, ge=16, le=8_000)


class ModelResponse(BaseModel):
    """Normalized model response returned to the application layer."""

    text: str
    provider: str
    model: str
    response_id: str | None = None


@dataclass(frozen=True, slots=True)
class StructuredModelResponse[StructuredOutput: BaseModel]:
    output: StructuredOutput
    provider: str
    model: str
    response_id: str | None = None


class ModelGatewayError(RuntimeError):
    """Raised when a provider does not return a usable validated response."""


class ModelGateway(Protocol):
    """Port implemented by model providers."""

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def complete_structured[StructuredOutput: BaseModel](
        self,
        request: ModelRequest,
        output_type: type[StructuredOutput],
    ) -> StructuredModelResponse[StructuredOutput]: ...


class FakeModelGateway:
    """Deterministic local gateway used without external credentials."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=f"[fake] {request.input}",
            provider="fake",
            model="deterministic",
        )

    async def complete_structured[StructuredOutput: BaseModel](
        self,
        request: ModelRequest,
        output_type: type[StructuredOutput],
    ) -> StructuredModelResponse[StructuredOutput]:
        current_request = request.input.rsplit("الطلب الحالي:\n", maxsplit=1)[-1].strip()
        excerpt = current_request[:180]
        normalized_request = current_request.casefold()
        task_prefixes = (
            "أنشئ لي مهمة",
            "انشئ لي مهمة",
            "أنشئ مهمة",
            "انشئ مهمة",
            "أضف مهمة",
            "اضف مهمة",
            "create task",
            "add task",
        )
        task_prefix = next(
            (prefix for prefix in task_prefixes if normalized_request.startswith(prefix)),
            None,
        )
        task_title = (
            current_request[len(task_prefix) :].strip(" :،.-") if task_prefix is not None else ""
        )
        is_task_request = bool(task_prefix and task_title)
        file_match = re.fullmatch(
            r"(?:(?:أنشئ|انشئ|اكتب)\s+ملف|(?:create|write)\s+file)\s+"
            r"(.+?)\s+(?:بالمحتوى|with\s+content)\s*[:：]?\s*(.*)",
            current_request,
            flags=re.DOTALL | re.IGNORECASE,
        )
        file_path = file_match.group(1).strip(" \t\r\n\"'«»") if file_match else ""
        file_content = file_match.group(2) if file_match else ""
        is_file_request = bool(file_match and file_path)
        approval_keywords = (
            "أنشئ مهمة",
            "انشئ مهمة",
            "أضف مهمة",
            "اضف مهمة",
            "أنشئ ملف",
            "اكتب ملف",
            "أرسل",
            "انشر",
            "احذف",
            "create file",
            "write file",
            "send",
            "publish",
            "delete",
            "create task",
            "add task",
        )
        high_risk_keywords = ("أرسل", "انشر", "احذف", "send", "publish", "delete")
        needs_approval = any(keyword in normalized_request for keyword in approval_keywords)
        effect_risk = (
            "high"
            if any(keyword in normalized_request for keyword in high_risk_keywords)
            else "medium"
        )
        action: dict[str, object] | None = None
        if is_task_request:
            action = {
                "tool_name": "tasks",
                "operation": "create",
                "arguments": {"title": task_title[:200]},
            }
        elif is_file_request:
            action = {
                "tool_name": "files",
                "operation": "create",
                "arguments": {"path": file_path, "content": file_content},
            }

        if len(current_request) < 10:
            data: dict[str, object] = {
                "kind": "clarification",
                "message": "ما النتيجة المحددة التي تريد من عَوْن إعدادها أو إنجازها؟",
                "steps": [],
            }
        elif "؟" in current_request or "?" in current_request:
            data = {
                "kind": "answer",
                "message": f"استلمت سؤالك في الوضع المحلي التجريبي: {excerpt}",
                "steps": [],
            }
        else:
            data = {
                "kind": "plan",
                "message": "أعددت خطة أولية قابلة للمراجعة. لم يُنفذ أي إجراء بعد.",
                "steps": [
                    {
                        "title": "فهم الطلب وتحديد النتيجة المطلوبة",
                        "risk": "low",
                        "requires_approval": False,
                    },
                    {
                        "title": (
                            "إنشاء المهمة داخل مساحة العمل"
                            if is_task_request
                            else (
                                "إنشاء الملف داخل مساحة العمل الآمنة"
                                if is_file_request
                                else "إعداد الناتج المقترح"
                            )
                        ),
                        "risk": effect_risk if needs_approval else "low",
                        "requires_approval": needs_approval,
                        **({"action": action} if action is not None else {}),
                    },
                    {
                        "title": "مراجعة الناتج والتحقق من اكتماله",
                        "risk": "low",
                        "requires_approval": False,
                    },
                ],
            }

        return StructuredModelResponse(
            output=output_type.model_validate(data),
            provider="fake",
            model="deterministic",
        )


class OpenAIModelGateway:
    """OpenAI Responses API adapter.

    Tool execution is deliberately absent here. A model may propose a tool call in a
    later iteration, but the application policy engine must authorize every effect.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await self._client.responses.create(
            model=self._model,
            instructions=request.instructions,
            input=request.input,
            max_output_tokens=request.max_output_tokens,
            store=False,
        )
        return ModelResponse(
            text=response.output_text,
            provider="openai",
            model=response.model,
            response_id=response.id,
        )

    async def complete_structured[StructuredOutput: BaseModel](
        self,
        request: ModelRequest,
        output_type: type[StructuredOutput],
    ) -> StructuredModelResponse[StructuredOutput]:
        response = await self._client.responses.parse(
            model=self._model,
            instructions=request.instructions,
            input=request.input,
            max_output_tokens=request.max_output_tokens,
            text_format=output_type,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ModelGatewayError("model returned no validated structured output")
        return StructuredModelResponse(
            output=parsed,
            provider="openai",
            model=response.model,
            response_id=response.id,
        )


def build_model_gateway(settings: Settings) -> ModelGateway:
    """Construct the configured gateway without exposing credentials to callers."""

    if settings.model_provider == "fake":
        return FakeModelGateway()

    assert settings.openai_api_key is not None
    assert settings.openai_model is not None
    return OpenAIModelGateway(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
    )
