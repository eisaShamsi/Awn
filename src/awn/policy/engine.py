"""Deterministic policy decisions kept outside the language model."""

from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field


class AutonomyLevel(IntEnum):
    ADVISORY = 0
    DRAFT = 1
    APPROVAL_REQUIRED = 2
    DELEGATED = 3


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"
    REQUIRE_CLARIFICATION = "require_clarification"


class ActionRequest(BaseModel):
    operation: str = Field(min_length=1)
    risk: RiskLevel
    side_effect: bool = False
    external: bool = False
    delegation_matches: bool = False
    context_complete: bool = True


class PolicyDecision(BaseModel):
    outcome: PolicyOutcome
    reason: str


class PolicyEngine:
    """Apply the v0.1 autonomy and risk matrix."""

    def decide(
        self,
        action: ActionRequest,
        *,
        autonomy: AutonomyLevel,
        has_matching_approval: bool = False,
    ) -> PolicyDecision:
        if not action.context_complete:
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_CLARIFICATION,
                reason="The action is missing context required for a safe decision.",
            )

        if action.risk is RiskLevel.PROHIBITED:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="The operation is prohibited by policy.",
            )

        if action.risk is RiskLevel.HIGH:
            if has_matching_approval:
                return PolicyDecision(
                    outcome=PolicyOutcome.ALLOW,
                    reason="A matching explicit approval authorizes this high-risk operation.",
                )
            return PolicyDecision(
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reason="High-risk operations always require explicit approval.",
            )

        if not action.side_effect:
            return PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                reason="Read-only operations are allowed within the active workspace.",
            )

        if has_matching_approval:
            return PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                reason="A matching explicit approval authorizes this operation.",
            )

        if autonomy is AutonomyLevel.ADVISORY:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason="Advisory mode cannot perform side effects.",
            )

        if autonomy is AutonomyLevel.DELEGATED and action.delegation_matches:
            return PolicyDecision(
                outcome=PolicyOutcome.ALLOW,
                reason="The operation is inside the active delegation policy.",
            )

        return PolicyDecision(
            outcome=PolicyOutcome.REQUIRE_APPROVAL,
            reason="This side effect needs an explicit approval or matching delegation.",
        )
