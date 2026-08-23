from uuid import uuid4

from fastapi.testclient import TestClient


def _setup(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/setup",
        json={
            "display_name": "  عيسى  ",
            "workspace_name": "  مساحة عَوْن  ",
            "locale": "ar",
            "timezone": "Asia/Dubai",
        },
    )
    assert response.status_code == 200
    return response.json()


def _conversation(client: TestClient, workspace_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "  بناء لوحة التحكم  "},
    )
    assert response.status_code == 201
    return response.json()


def _message(
    client: TestClient,
    workspace_id: str,
    conversation_id: str,
    text: str = "ابدأ التنفيذ",
) -> dict[str, object]:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
        json={"parts": [{"type": "text", "text": text}]},
    )
    assert response.status_code == 201
    return response.json()


def test_setup_is_idempotent_and_creates_the_initial_workspace(client: TestClient) -> None:
    assert client.get("/api/v1/setup").status_code == 404
    assert client.post("/api/v1/workspaces", json={"name": "قبل التهيئة"}).status_code == 409

    first = _setup(client)
    second = client.post(
        "/api/v1/setup",
        json={"display_name": "اسم آخر", "workspace_name": "مساحة أخرى"},
    )
    current = client.get("/api/v1/setup")

    assert first["created"] is True
    assert first["user"]["display_name"] == "عيسى"
    assert first["workspace"]["name"] == "مساحة عَوْن"
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["user"]["id"] == first["user"]["id"]
    assert current.status_code == 200
    assert current.json()["created"] is False


def test_workspace_conversation_message_and_run_flow(client: TestClient) -> None:
    setup = _setup(client)
    workspace_id = setup["workspace"]["id"]

    extra_workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "مشروع تقني"},
    )
    workspaces = client.get("/api/v1/workspaces")
    conversation = _conversation(client, workspace_id)
    conversation_id = conversation["id"]
    message = _message(client, workspace_id, conversation_id)

    spoofed_risk = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs",
        json={
            "request_message_id": message["id"],
            "risk": "medium",
            "autonomy_level": 2,
        },
    )
    created_run = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs",
        json={"request_message_id": message["id"], "autonomy_level": 2},
    )

    assert extra_workspace.status_code == 201
    assert workspaces.status_code == 200
    assert len(workspaces.json()) == 2
    assert conversation["title"] == "بناء لوحة التحكم"
    assert message["role"] == "user"
    assert message["parts"] == [{"type": "text", "text": "ابدأ التنفيذ", "data": None}]
    assert spoofed_risk.status_code == 422
    assert created_run.status_code == 201
    run = created_run.json()
    assert run["status"] == "received"
    assert run["risk"] == "low"
    assert run["autonomy_level"] == 2

    runs = client.get(f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs")
    fetched = client.get(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs/{run['id']}"
    )
    steps = client.get(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs/{run['id']}/steps"
    )
    messages = client.get(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages"
    )

    assert [item["id"] for item in runs.json()] == [run["id"]]
    assert fetched.json()["trace_id"] == run["trace_id"]
    assert fetched.json()["status"] == "ready"
    assert [item["position"] for item in steps.json()] == [0, 1, 2]
    assert [item["role"] for item in messages.json()] == ["user", "assistant"]
    assert messages.json()[0]["id"] == message["id"]


def test_fake_orchestrator_answers_or_requests_clarification(client: TestClient) -> None:
    setup = _setup(client)
    workspace_id = setup["workspace"]["id"]

    answer_conversation = _conversation(client, workspace_id)
    answer_message = _message(
        client,
        workspace_id,
        answer_conversation["id"],
        "هل تستطيع شرح الخطة الحالية؟",
    )
    answer_run = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations/{answer_conversation['id']}/runs",
        json={"request_message_id": answer_message["id"]},
    ).json()
    answer_state = client.get(
        f"/api/v1/workspaces/{workspace_id}/conversations/{answer_conversation['id']}"
        f"/runs/{answer_run['id']}"
    )
    answer_messages = client.get(
        f"/api/v1/workspaces/{workspace_id}/conversations/{answer_conversation['id']}/messages"
    )

    clarification_conversation = _conversation(client, workspace_id)
    clarification_message = _message(
        client,
        workspace_id,
        clarification_conversation["id"],
        "ساعدني",
    )
    clarification_run = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations/{clarification_conversation['id']}/runs",
        json={"request_message_id": clarification_message["id"]},
    ).json()
    clarification_state = client.get(
        f"/api/v1/workspaces/{workspace_id}/conversations/"
        f"{clarification_conversation['id']}/runs/{clarification_run['id']}"
    )
    clarification_messages = client.get(
        f"/api/v1/workspaces/{workspace_id}/conversations/"
        f"{clarification_conversation['id']}/messages"
    )

    assert answer_state.json()["status"] == "succeeded"
    assert answer_state.json()["completed_at"] is not None
    assert [message["role"] for message in answer_messages.json()] == ["user", "assistant"]
    assert clarification_state.json()["status"] == "needs_clarification"
    assert "النتيجة المحددة" in clarification_messages.json()[-1]["parts"][0]["text"]


def test_workspace_scope_and_request_message_are_enforced(client: TestClient) -> None:
    setup = _setup(client)
    first_workspace_id = setup["workspace"]["id"]
    second_workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "مساحة منفصلة"},
    ).json()
    second_workspace_id = second_workspace["id"]

    first_conversation = _conversation(client, first_workspace_id)
    second_conversation = _conversation(client, second_workspace_id)
    first_message = _message(client, first_workspace_id, first_conversation["id"])

    cross_workspace = client.get(
        f"/api/v1/workspaces/{second_workspace_id}/conversations/{first_conversation['id']}"
    )
    cross_conversation_message = client.post(
        f"/api/v1/workspaces/{second_workspace_id}/conversations/{second_conversation['id']}/runs",
        json={"request_message_id": first_message["id"]},
    )
    unknown_workspace = client.get(f"/api/v1/workspaces/{uuid4()}/conversations")

    assert cross_workspace.status_code == 404
    assert cross_conversation_message.status_code == 404
    assert unknown_workspace.status_code == 404


def test_message_contract_rejects_role_spoofing_and_empty_parts(client: TestClient) -> None:
    setup = _setup(client)
    workspace_id = setup["workspace"]["id"]
    conversation_id = _conversation(client, workspace_id)["id"]
    path = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages"

    spoofed = client.post(
        path,
        json={
            "role": "assistant",
            "parts": [{"type": "text", "text": "رسالة مزيفة"}],
        },
    )
    empty = client.post(path, json={"parts": []})
    spoofed_tool_part = client.post(
        path,
        json={"parts": [{"type": "tool_result", "data": {"success": True}}]},
    )

    assert spoofed.status_code == 422
    assert empty.status_code == 422
    assert spoofed_tool_part.status_code == 422
