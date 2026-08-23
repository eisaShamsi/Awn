from fastapi.testclient import TestClient

from awn.api.app import create_app
from awn.config import Settings


def make_client() -> TestClient:
    return TestClient(create_app(Settings(environment="test", model_provider="fake")))


def test_create_list_and_complete_task() -> None:
    client = make_client()

    created = client.post(
        "/api/v1/tasks",
        json={"title": "  مراجعة تقرير عَوْن  ", "priority": "high"},
    )

    assert created.status_code == 201
    task = created.json()
    assert task["title"] == "مراجعة تقرير عَوْن"
    assert task["status"] == "pending"
    assert task["priority"] == "high"

    listed = client.get("/api/v1/tasks")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [task["id"]]

    completed = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"status": "completed"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_rejects_blank_title_and_naive_due_date() -> None:
    client = make_client()

    blank = client.post("/api/v1/tasks", json={"title": "   "})
    naive_date = client.post(
        "/api/v1/tasks",
        json={"title": "موعد", "due_at": "2026-08-24T09:00:00"},
    )

    assert blank.status_code == 422
    assert naive_date.status_code == 422


def test_missing_task_returns_not_found() -> None:
    client = make_client()

    response = client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
