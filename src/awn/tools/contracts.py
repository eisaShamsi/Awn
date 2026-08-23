"""Provider-neutral contracts for executable tools."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from awn.policy.engine import RiskLevel


@dataclass(frozen=True, slots=True)
class ToolContext:
    owner_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    run_id: UUID
    trace_id: UUID
    tool_call_id: UUID
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ToolDefinition[ToolInput: BaseModel, ToolOutput: BaseModel]:
    name: str
    operation: str
    summary: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    risk: RiskLevel
    side_effect: bool
    external: bool
    reversible: bool
    required_scopes: tuple[str, ...]
    timeout_seconds: int
    supports_idempotency: bool
    handler: Callable[[ToolContext, ToolInput], ToolOutput]

    @property
    def identifier(self) -> str:
        return f"{self.name}.{self.operation}"
