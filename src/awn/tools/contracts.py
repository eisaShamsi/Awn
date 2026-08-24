"""Provider-neutral contracts for executable tools."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from awn.domain.cancellations import CancellationEvidenceCode
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


class EffectVerificationStatus(StrEnum):
    VERIFIED_NO_EFFECT = "verified_no_effect"
    EFFECT_PRESENT = "effect_present"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EffectVerification:
    status: EffectVerificationStatus
    evidence_code: CancellationEvidenceCode
    output: BaseModel | None = None


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
    effect_verifier: Callable[[ToolContext, ToolInput], EffectVerification] | None = None

    @property
    def identifier(self) -> str:
        return f"{self.name}.{self.operation}"
