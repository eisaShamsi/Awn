"""Provider-neutral model gateway."""

from __future__ import annotations

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
                        "title": "إعداد الناتج المقترح",
                        "risk": "low",
                        "requires_approval": False,
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
