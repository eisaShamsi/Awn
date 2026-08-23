import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from awn.api.app import create_app
from awn.application.tasks import TaskService
from awn.config import Settings
from awn.domain.tasks import TaskCreate, TaskPriority, TaskStatus, TaskUpdate
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.models import (
    ConversationRecord,
    MessageRecord,
    PlanStepRecord,
    RunRecord,
    TaskRecord,
    UserRecord,
    WorkspaceRecord,
)
from awn.infrastructure.persistence.tasks import SqlAlchemyTaskRepository

POSTGRES_URL = os.getenv("AWN_TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_repository_round_trip_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    created_id: UUID | None = None

    try:
        assert database.dialect_name == "postgresql"
        service = TaskService(SqlAlchemyTaskRepository(database.session_factory))
        created = service.create(
            TaskCreate(title="PostgreSQL integration", priority=TaskPriority.HIGH)
        )
        created_id = created.id

        restored = service.get(created.id)
        assert restored is not None
        assert restored.title == "PostgreSQL integration"

        updated = service.update(created.id, TaskUpdate(status=TaskStatus.COMPLETED))
        assert updated is not None
        assert updated.status is TaskStatus.COMPLETED
    finally:
        if created_id is not None:
            with database.session_factory.begin() as session:
                session.execute(delete(TaskRecord).where(TaskRecord.id == created_id))
        database.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_core_execution_graph_round_trip_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    now = datetime(2026, 8, 23, 8, tzinfo=UTC)
    user_id = uuid4()
    workspace_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    run_id = uuid4()

    try:
        with database.session_factory.begin() as session:
            session.add_all(
                [
                    UserRecord(
                        id=user_id,
                        display_name="PostgreSQL user",
                        locale="ar",
                        timezone="Asia/Dubai",
                        created_at=now,
                        updated_at=now,
                    ),
                    WorkspaceRecord(
                        id=workspace_id,
                        owner_id=user_id,
                        name="PostgreSQL workspace",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    ConversationRecord(
                        id=conversation_id,
                        workspace_id=workspace_id,
                        title="PostgreSQL conversation",
                        status="active",
                        summary=None,
                        created_at=now,
                        updated_at=now,
                    ),
                    MessageRecord(
                        id=message_id,
                        conversation_id=conversation_id,
                        role="user",
                        content=[{"type": "text", "text": "نفذ"}],
                        created_at=now,
                    ),
                    RunRecord(
                        id=run_id,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        request_message_id=message_id,
                        trace_id=uuid4(),
                        status="received",
                        risk="low",
                        autonomy_level=2,
                        error_code=None,
                        started_at=None,
                        completed_at=None,
                        created_at=now,
                        updated_at=now,
                    ),
                    PlanStepRecord(
                        id=uuid4(),
                        run_id=run_id,
                        position=0,
                        title="تحليل الطلب",
                        status="pending",
                        risk="low",
                        requires_approval=False,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        with database.session_factory() as session:
            run = session.get(RunRecord, run_id)
            message = session.get(MessageRecord, message_id)
            assert run is not None
            assert message is not None
            assert run.workspace_id == workspace_id
            assert [step.title for step in run.steps] == ["تحليل الطلب"]
            assert message.content == [{"type": "text", "text": "نفذ"}]
    finally:
        with database.session_factory.begin() as session:
            owner = session.get(UserRecord, user_id)
            if owner is not None:
                session.delete(owner)
        database.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_core_api_flow_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    created_user_id: UUID | None = None

    try:
        with database.session_factory() as session:
            existing_users = session.scalar(select(func.count()).select_from(UserRecord))
        if existing_users:
            pytest.skip("PostgreSQL integration database already contains a user")

        app = create_app(
            Settings(
                environment="test",
                model_provider="fake",
                database_url=POSTGRES_URL,
            ),
            database=database,
        )
        with TestClient(app) as client:
            setup_response = client.post(
                "/api/v1/setup",
                json={"display_name": "PostgreSQL API", "workspace_name": "Integration"},
            )
            assert setup_response.status_code == 200
            setup = setup_response.json()
            created_user_id = UUID(setup["user"]["id"])
            workspace_id = setup["workspace"]["id"]

            conversation_response = client.post(
                f"/api/v1/workspaces/{workspace_id}/conversations",
                json={"title": "Persistent API conversation"},
            )
            assert conversation_response.status_code == 201
            conversation_id = conversation_response.json()["id"]

            message_response = client.post(
                f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
                json={"parts": [{"type": "text", "text": "نفذ"}]},
            )
            assert message_response.status_code == 201
            message_id = message_response.json()["id"]

            run_response = client.post(
                f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs",
                json={"request_message_id": message_id, "autonomy_level": 2},
            )
            assert run_response.status_code == 201
            assert run_response.json()["status"] == "received"
            assert client.get("/ready").json() == {
                "status": "ready",
                "database": "postgresql",
            }
    finally:
        if created_user_id is not None:
            with database.session_factory.begin() as session:
                owner = session.get(UserRecord, created_user_id)
                if owner is not None:
                    session.delete(owner)
        database.dispose()
