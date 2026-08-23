"""Provider-neutral model gateway."""

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


class ModelGateway(Protocol):
    """Port implemented by model providers."""

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class FakeModelGateway:
    """Deterministic local gateway used without external credentials."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=f"[fake] {request.input}",
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
