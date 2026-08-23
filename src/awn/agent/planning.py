"""Structured contract between Awn's orchestrator and model gateways."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from awn.domain.runs import RunRisk


class OrchestrationKind(StrEnum):
    ANSWER = "answer"
    PLAN = "plan"
    CLARIFICATION = "clarification"


class ProposedToolAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=100)
    operation: str = Field(min_length=1, max_length=100)
    arguments: dict[str, object]


class ProposedPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=300)
    risk: RunRisk = RunRisk.LOW
    requires_approval: bool = False
    action: ProposedToolAction | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("title must not be blank")
        return title


class OrchestrationDecision(BaseModel):
    """A validated answer, plan, or clarification proposed by a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: OrchestrationKind
    message: str = Field(min_length=1, max_length=20_000)
    steps: tuple[ProposedPlanStep, ...] = Field(default=(), max_length=12)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank")
        return message

    @model_validator(mode="after")
    def steps_match_kind(self) -> Self:
        if self.kind is OrchestrationKind.PLAN and not self.steps:
            raise ValueError("plan decisions require at least one step")
        if self.kind is not OrchestrationKind.PLAN and self.steps:
            raise ValueError("only plan decisions may include steps")
        return self
