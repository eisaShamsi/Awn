from fastapi.testclient import TestClient


def _workspace(client: TestClient) -> str:
    response = client.post(
        "/api/v1/setup",
        json={"display_name": "مستخدم المهام", "workspace_name": "مساحة المهام"},
    )
    assert response.status_code == 200
    return response.json()["workspace"]["id"]


def test_create_list_and_complete_task(client: TestClient) -> None:
    workspace_id = _workspace(client)
    path = f"/api/v1/workspaces/{workspace_id}/tasks"
    created = client.post(
        path,
        json={"title": "  مراجعة تقرير عَوْن  ", "priority": "high"},
    )

    assert created.status_code == 201
    task = created.json()
    assert task["workspace_id"] == workspace_id
    assert task["title"] == "مراجعة تقرير عَوْن"
    assert task["status"] == "pending"
    assert task["priority"] == "high"

    listed = client.get(path)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [task["id"]]

    completed = client.patch(
        f"{path}/{task['id']}",
        json={"status": "completed"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_rejects_blank_title_and_naive_due_date(client: TestClient) -> None:
    workspace_id = _workspace(client)
    path = f"/api/v1/workspaces/{workspace_id}/tasks"
    blank = client.post(path, json={"title": "   "})
    naive_date = client.post(
        path,
        json={"title": "موعد", "due_at": "2026-08-24T09:00:00"},
    )

    assert blank.status_code == 422
    assert naive_date.status_code == 422


def test_due_date_can_be_cleared(client: TestClient) -> None:
    workspace_id = _workspace(client)
    path = f"/api/v1/workspaces/{workspace_id}/tasks"
    created = client.post(
        path,
        json={"title": "موعد", "due_at": "2026-08-24T09:00:00+04:00"},
    ).json()

    response = client.patch(
        f"{path}/{created['id']}",
        json={"due_at": None},
    )

    assert response.status_code == 200
    assert response.json()["due_at"] is None


def test_rejects_null_status(client: TestClient) -> None:
    workspace_id = _workspace(client)
    path = f"/api/v1/workspaces/{workspace_id}/tasks"
    created = client.post(path, json={"title": "مهمة"}).json()

    response = client.patch(
        f"{path}/{created['id']}",
        json={"status": None},
    )

    assert response.status_code == 422


def test_missing_or_cross_workspace_task_returns_not_found(client: TestClient) -> None:
    workspace_id = _workspace(client)
    path = f"/api/v1/workspaces/{workspace_id}/tasks"
    task = client.post(path, json={"title": "مهمة معزولة"}).json()
    other_workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "مساحة أخرى"},
    ).json()

    missing = client.get(f"{path}/00000000-0000-0000-0000-000000000000")
    crossed = client.get(f"/api/v1/workspaces/{other_workspace['id']}/tasks/{task['id']}")

    assert missing.status_code == 404
    assert crossed.status_code == 404
