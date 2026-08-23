from fastapi.testclient import TestClient

from awn.api.app import create_app
from awn.config import Settings


def test_health_reports_safe_runtime_metadata() -> None:
    app = create_app(Settings(environment="test", model_provider="fake"))

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "name": "Awn",
        "version": "0.1.0.dev0",
        "environment": "test",
        "model_provider": "fake",
    }
