from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from awn.infrastructure.filesystem import SafeWorkspaceFiles, UnsafeWorkspacePathError


def _setup(client: TestClient) -> tuple[str, str]:
    setup = client.post(
        "/api/v1/setup",
        json={"display_name": "مدير عَوْن", "workspace_name": "مساحة الملفات"},
    ).json()
    workspace_id = setup["workspace"]["id"]
    conversation = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "إنشاء الملفات"},
    ).json()
    return workspace_id, conversation["id"]


def test_approved_file_is_created_only_inside_its_workspace(client: TestClient) -> None:
    workspace_id, conversation_id = _setup(client)
    base = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}"
    message = client.post(
        f"{base}/messages",
        json={
            "parts": [
                {
                    "type": "text",
                    "text": "أنشئ ملف تقارير/ملخص.txt بالمحتوى: مرحبًا من عَوْن",
                }
            ]
        },
    ).json()
    run = client.post(
        f"{base}/runs",
        json={"request_message_id": message["id"], "autonomy_level": 2},
    ).json()
    run_path = f"{base}/runs/{run['id']}"
    approval = client.get(f"{run_path}/approvals").json()[0]

    approved = client.post(
        f"{run_path}/approvals/{approval['id']}/decision",
        json={
            "decision": "approve",
            "action_fingerprint": approval["action_fingerprint"],
        },
    )

    target = Path(client.app.state.workspace_files.root) / workspace_id / "تقارير" / "ملخص.txt"
    calls = client.get(f"{run_path}/tool-calls").json()
    assert approved.status_code == 200
    assert client.get(run_path).json()["status"] == "succeeded"
    assert target.read_text(encoding="utf-8") == "مرحبًا من عَوْن"
    assert len(calls) == 1
    assert calls[0]["status"] == "succeeded"
    assert calls[0]["attempt_count"] == 1
    assert calls[0]["output"]["path"] == "تقارير/ملخص.txt"
    assert calls[0]["output"]["created"] is True
    assert len(calls[0]["output"]["sha256"]) == 64


def test_filesystem_boundary_rejects_parent_escape(tmp_path) -> None:
    store = SafeWorkspaceFiles(tmp_path / "safe-root")

    with pytest.raises(UnsafeWorkspacePathError):
        store.create_text(
            uuid4(),
            "../outside.txt",
            "سر",
            tool_call_id=uuid4(),
        )


def test_file_creation_is_idempotent_but_never_overwrites_different_content(tmp_path) -> None:
    store = SafeWorkspaceFiles(tmp_path / "safe-root")
    workspace_id = uuid4()

    created = store.create_text(
        workspace_id,
        "notes/result.txt",
        "ناتج ثابت",
        tool_call_id=uuid4(),
    )
    resumed = store.create_text(
        workspace_id,
        "notes/result.txt",
        "ناتج ثابت",
        tool_call_id=uuid4(),
    )

    assert created.created is True
    assert resumed.created is False
    assert resumed.sha256 == created.sha256
    with pytest.raises(FileExistsError):
        store.create_text(
            workspace_id,
            "notes/result.txt",
            "محتوى مختلف",
            tool_call_id=uuid4(),
        )
