import pytest

from awn.policy.engine import (
    ActionRequest,
    AutonomyLevel,
    PolicyEngine,
    PolicyOutcome,
    RiskLevel,
)


@pytest.mark.parametrize(
    ("action", "autonomy", "approved", "expected"),
    [
        (
            ActionRequest(operation="github.read_issue", risk=RiskLevel.LOW, external=True),
            AutonomyLevel.ADVISORY,
            False,
            PolicyOutcome.ALLOW,
        ),
        (
            ActionRequest(
                operation="github.create_issue",
                risk=RiskLevel.MEDIUM,
                external=True,
                side_effect=True,
            ),
            AutonomyLevel.APPROVAL_REQUIRED,
            False,
            PolicyOutcome.REQUIRE_APPROVAL,
        ),
        (
            ActionRequest(
                operation="github.create_issue",
                risk=RiskLevel.MEDIUM,
                external=True,
                side_effect=True,
            ),
            AutonomyLevel.APPROVAL_REQUIRED,
            True,
            PolicyOutcome.ALLOW,
        ),
        (
            ActionRequest(
                operation="github.create_issue",
                risk=RiskLevel.MEDIUM,
                external=True,
                side_effect=True,
                delegation_matches=True,
            ),
            AutonomyLevel.DELEGATED,
            False,
            PolicyOutcome.ALLOW,
        ),
        (
            ActionRequest(
                operation="github.force_push",
                risk=RiskLevel.PROHIBITED,
                external=True,
                side_effect=True,
            ),
            AutonomyLevel.DELEGATED,
            True,
            PolicyOutcome.DENY,
        ),
        (
            ActionRequest(
                operation="github.delete_repository",
                risk=RiskLevel.HIGH,
                external=True,
                side_effect=True,
                delegation_matches=True,
            ),
            AutonomyLevel.DELEGATED,
            False,
            PolicyOutcome.REQUIRE_APPROVAL,
        ),
    ],
)
def test_policy_matrix(
    action: ActionRequest,
    autonomy: AutonomyLevel,
    approved: bool,
    expected: PolicyOutcome,
) -> None:
    decision = PolicyEngine().decide(
        action,
        autonomy=autonomy,
        has_matching_approval=approved,
    )

    assert decision.outcome is expected
