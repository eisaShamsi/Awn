"""Approval observation and decision API."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from awn.api.dependencies import ApprovalServiceDependency
from awn.domain.approvals import (
    ApprovalDecisionCommand,
    ApprovalDecisionOutcome,
    ApprovalRequest,
)

router = APIRouter(
    prefix=("/workspaces/{workspace_id}/conversations/{conversation_id}/runs/{run_id}/approvals"),
    tags=["approvals"],
)


@router.get("", response_model=list[ApprovalRequest])
def list_approvals(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    service: ApprovalServiceDependency,
) -> list[ApprovalRequest]:
    approvals = service.list(workspace_id, conversation_id, run_id)
    if approvals is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return approvals


@router.post("/{approval_id}/decision", response_model=ApprovalRequest)
def decide_approval(
    workspace_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    approval_id: UUID,
    command: ApprovalDecisionCommand,
    service: ApprovalServiceDependency,
) -> ApprovalRequest:
    result = service.decide(
        workspace_id,
        conversation_id,
        run_id,
        approval_id,
        command,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval not found",
        )

    conflicts = {
        ApprovalDecisionOutcome.CONFLICT: "Approval is no longer pending",
        ApprovalDecisionOutcome.FINGERPRINT_MISMATCH: (
            "Approval fingerprint does not match the reviewed action"
        ),
        ApprovalDecisionOutcome.PLAN_CHANGED: (
            "The plan changed after approval was requested; review a new request"
        ),
        ApprovalDecisionOutcome.EXPIRED: "Approval expired before the decision",
    }
    detail = conflicts.get(result.outcome)
    if detail is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return result.approval
