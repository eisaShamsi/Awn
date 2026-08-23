from fastapi.testclient import TestClient


def test_health_reports_safe_runtime_metadata(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "name": "Awn",
        "version": "0.1.0.dev0",
        "environment": "test",
        "model_provider": "fake",
    }


def test_readiness_checks_the_database(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "sqlite"}
