from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from awn.api.app import create_app
from awn.config import Settings
from awn.infrastructure.database import Database


@pytest.fixture
def database() -> Iterator[Database]:
    instance = Database("sqlite+pysqlite:///:memory:")
    instance.create_schema()
    yield instance
    instance.dispose()


@pytest.fixture
def client(database: Database, tmp_path) -> Iterator[TestClient]:
    app = create_app(
        Settings(
            environment="test",
            model_provider="fake",
            workspace_files_root=tmp_path / "workspaces",
        ),
        database=database,
    )
    with TestClient(app) as test_client:
        yield test_client
