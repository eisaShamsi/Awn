from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from awn.infrastructure.database import Database
from awn.infrastructure.persistence.models import ApprovalRecord, PlanStepRecord


def _setup(client: TestClient) -> tuple[str, str]:
    setup = client.post(
        "/api/v1/setup",
        json={"display_name": "عيسى", "workspace_name": "مساحة عَوْن"},
    ).json()
    workspace_id = setup["workspace"]["id"]
    conversation = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "اختبار الموافقات"},
    ).json()
    return workspace_id, conversation["id"]


def _approval_run(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    base = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}"
    message = client.post(
        f"{base}/messages",
        json={
            "parts": [
                {
                    "type": "text",
                    "text": "أنشئ ملف تقرير داخل مساحة العمل الآمنة",
                }
            ]
        },
    ).json()
    run = client.post(
        f"{base}/runs",
        json={"request_message_id": message["id"], "autonomy_level": 2},
    ).json()
    run_path = f"{base}/runs/{run['id']}"
    current_run = client.get(run_path).json()
    approvals = client.get(f"{run_path}/approvals").json()

    assert current_run["status"] == "awaiting_approval"
    assert len(approvals) == 1
    assert approvals[0]["status"] == "pending"
    assert len(approvals[0]["action_fingerprint"]) == 64
    return current_run, approvals[0]


def test_approval_requires_the_reviewed_fingerprint_and_is_idempotent(
    client: TestClient,
) -> None:
    workspace_id, conversation_id = _setup(client)
    run, approval = _approval_run(client, workspace_id, conversation_id)
    run_path = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs/{run['id']}"
    decision_path = f"{run_path}/approvals/{approval['id']}/decision"

    mismatched = client.post(
        decision_path,
        json={"decision": "approve", "action_fingerprint": "0" * 64},
    )
    still_pending = client.get(f"{run_path}/approvals").json()[0]
    approved = client.post(
        decision_path,
        json={
            "decision": "approve",
            "action_fingerprint": approval["action_fingerprint"],
            "note": "اعتمدت الخطة بعد المراجعة",
        },
    )
    repeated = client.post(
        decision_path,
        json={
            "decision": "approve",
            "action_fingerprint": approval["action_fingerprint"],
        },
    )

    assert mismatched.status_code == 409
    assert still_pending["status"] == "pending"
    assert client.get(run_path).json()["status"] == "ready"
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert repeated.status_code == 200
    assert repeated.json()["id"] == approval["id"]


def test_rejection_cancels_the_run_and_workspace_scope_is_enforced(
    client: TestClient,
) -> None:
    workspace_id, conversation_id = _setup(client)
    run, approval = _approval_run(client, workspace_id, conversation_id)
    other_workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "مساحة منفصلة"},
    ).json()
    run_path = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs/{run['id']}"

    cross_workspace = client.get(
        f"/api/v1/workspaces/{other_workspace['id']}/conversations/{conversation_id}"
        f"/runs/{run['id']}/approvals"
    )
    rejected = client.post(
        f"{run_path}/approvals/{approval['id']}/decision",
        json={
            "decision": "reject",
            "action_fingerprint": approval["action_fingerprint"],
        },
    )

    assert cross_workspace.status_code == 404
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.get(run_path).json()["status"] == "cancelled"


def test_changed_or_expired_actions_cannot_be_approved(
    client: TestClient,
    database: Database,
) -> None:
    workspace_id, conversation_id = _setup(client)
    changed_run, changed_approval = _approval_run(client, workspace_id, conversation_id)
    changed_path = (
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}"
        f"/runs/{changed_run['id']}"
    )

    with database.session_factory.begin() as session:
        step = session.scalar(
            select(PlanStepRecord).where(PlanStepRecord.run_id == UUID(str(changed_run["id"])))
        )
        assert step is not None
        step.title = "خطة تغيرت بعد عرض الموافقة"

    changed = client.post(
        f"{changed_path}/approvals/{changed_approval['id']}/decision",
        json={
            "decision": "approve",
            "action_fingerprint": changed_approval["action_fingerprint"],
        },
    )

    next_conversation = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "اختبار الانتهاء"},
    ).json()
    expired_run, expired_approval = _approval_run(
        client,
        workspace_id,
        next_conversation["id"],
    )
    expired_path = (
        f"/api/v1/workspaces/{workspace_id}/conversations/{next_conversation['id']}"
        f"/runs/{expired_run['id']}"
    )
    with database.session_factory.begin() as session:
        approval_record = session.get(
            ApprovalRecord,
            UUID(str(expired_approval["id"])),
        )
        assert approval_record is not None
        now = datetime.now(UTC)
        approval_record.requested_at = now - timedelta(minutes=2)
        approval_record.expires_at = now - timedelta(minutes=1)

    expired = client.post(
        f"{expired_path}/approvals/{expired_approval['id']}/decision",
        json={
            "decision": "approve",
            "action_fingerprint": expired_approval["action_fingerprint"],
        },
    )

    assert changed.status_code == 409
    assert client.get(f"{changed_path}/approvals").json()[0]["status"] == "invalidated"
    assert client.get(changed_path).json()["status"] == "ready"
    assert expired.status_code == 409
    assert client.get(f"{expired_path}/approvals").json()[0]["status"] == "expired"
    assert client.get(expired_path).json()["status"] == "cancelled"
