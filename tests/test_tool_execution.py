from fastapi.testclient import TestClient

from awn.api.app import create_app
from awn.config import Settings
from awn.infrastructure.database import Database


def _setup(client: TestClient) -> tuple[str, str]:
    setup = client.post(
        "/api/v1/setup",
        json={"display_name": "مدير عَوْن", "workspace_name": "مساحة التنفيذ"},
    ).json()
    workspace_id = setup["workspace"]["id"]
    conversation = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "تنفيذ المهام"},
    ).json()
    return workspace_id, conversation["id"]


def _request_task(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
    *,
    autonomy_level: int,
) -> tuple[str, str]:
    base = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}"
    message = client.post(
        f"{base}/messages",
        json={"parts": [{"type": "text", "text": "أنشئ مهمة لمراجعة تقرير عَوْن"}]},
    ).json()
    run = client.post(
        f"{base}/runs",
        json={"request_message_id": message["id"], "autonomy_level": autonomy_level},
    ).json()
    return base, run["id"]


def test_approved_task_tool_executes_once_and_is_auditable(client: TestClient) -> None:
    workspace_id, conversation_id = _setup(client)
    base, run_id = _request_task(
        client,
        workspace_id,
        conversation_id,
        autonomy_level=2,
    )
    run_path = f"{base}/runs/{run_id}"
    steps = client.get(f"{run_path}/steps").json()
    approval = client.get(f"{run_path}/approvals").json()[0]

    action_step = next(step for step in steps if step["tool_name"] == "tasks")
    assert action_step["operation"] == "create"
    assert action_step["tool_input"] == {"title": "لمراجعة تقرير عَوْن", "priority": "normal"}
    assert client.get(run_path).json()["status"] == "awaiting_approval"

    decision_path = f"{run_path}/approvals/{approval['id']}/decision"
    decision = {
        "decision": "approve",
        "action_fingerprint": approval["action_fingerprint"],
    }
    approved = client.post(decision_path, json=decision)

    assert approved.status_code == 200
    assert client.get(run_path).json()["status"] == "succeeded"
    assert client.get(f"{run_path}/approvals").json()[0]["status"] == "consumed"

    calls = client.get(f"{run_path}/tool-calls").json()
    tasks_path = f"/api/v1/workspaces/{workspace_id}/tasks"
    tasks = client.get(tasks_path).json()
    assert len(calls) == 1
    assert calls[0]["status"] == "succeeded"
    assert calls[0]["tool_name"] == "tasks"
    assert calls[0]["operation"] == "create"
    assert calls[0]["output"]["id"] == tasks[0]["id"]
    assert [task["title"] for task in tasks] == ["لمراجعة تقرير عَوْن"]

    repeated = client.post(decision_path, json=decision)
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "consumed"
    assert len(client.get(f"{run_path}/tool-calls").json()) == 1
    assert len(client.get(tasks_path).json()) == 1

    messages = client.get(f"{base}/messages").json()
    assert any(
        "تم إنشاء المهمة «لمراجعة تقرير عَوْن»" in part.get("text", "")
        for message in messages
        for part in message["parts"]
    )


def test_advisory_mode_denies_task_side_effect(client: TestClient) -> None:
    workspace_id, conversation_id = _setup(client)
    base, run_id = _request_task(
        client,
        workspace_id,
        conversation_id,
        autonomy_level=0,
    )
    run_path = f"{base}/runs/{run_id}"

    run = client.get(run_path).json()
    assert run["status"] == "denied"
    assert run["error_code"] == "POLICY_DENIED"
    assert client.get(f"{run_path}/approvals").json() == []
    assert client.get(f"{run_path}/tool-calls").json() == []
    assert client.get(f"/api/v1/workspaces/{workspace_id}/tasks").json() == []


def test_consumed_approval_and_tool_result_survive_app_restart(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'restart.db').as_posix()}"
    first_database = Database(database_url)
    first_database.create_schema()
    first_app = create_app(
        Settings(
            environment="test",
            model_provider="fake",
            workspace_files_root=tmp_path / "first-workspaces",
        ),
        database=first_database,
    )

    with TestClient(first_app) as first_client:
        workspace_id, conversation_id = _setup(first_client)
        base, run_id = _request_task(
            first_client,
            workspace_id,
            conversation_id,
            autonomy_level=2,
        )
        run_path = f"{base}/runs/{run_id}"
        approval = first_client.get(f"{run_path}/approvals").json()[0]
        decision_path = f"{run_path}/approvals/{approval['id']}/decision"
        decision = {
            "decision": "approve",
            "action_fingerprint": approval["action_fingerprint"],
        }
        assert first_client.post(decision_path, json=decision).status_code == 200

    second_database = Database(database_url)
    second_app = create_app(
        Settings(
            environment="test",
            model_provider="fake",
            workspace_files_root=tmp_path / "second-workspaces",
        ),
        database=second_database,
    )
    with TestClient(second_app) as second_client:
        assert second_client.get(run_path).json()["status"] == "succeeded"
        assert second_client.get(f"{run_path}/approvals").json()[0]["status"] == "consumed"
        assert second_client.get(f"{run_path}/tool-calls").json()[0]["status"] == "succeeded"
        assert second_client.post(decision_path, json=decision).status_code == 200
        tasks = second_client.get(f"/api/v1/workspaces/{workspace_id}/tasks").json()
        assert [task["title"] for task in tasks] == ["لمراجعة تقرير عَوْن"]
