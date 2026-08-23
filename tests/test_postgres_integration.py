import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from awn.api.app import create_app
from awn.application.tasks import TaskService
from awn.config import Settings
from awn.domain.identity import SetupState, User, Workspace, WorkspaceStatus
from awn.domain.tasks import TaskCreate, TaskPriority, TaskStatus, TaskUpdate
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.models import (
    ConversationRecord,
    MessageRecord,
    PlanStepRecord,
    RunRecord,
    ToolCallRecord,
    UserRecord,
    WorkspaceRecord,
)
from awn.infrastructure.persistence.tasks import SqlAlchemyTaskRepository
from awn.infrastructure.persistence.tool_calls import SqlAlchemyToolCallRepository

POSTGRES_URL = os.getenv("AWN_TEST_POSTGRES_URL")


class _StaticIdentityRepository:
    def __init__(self, state: SetupState) -> None:
        self._state = state

    def current(self) -> SetupState:
        return self._state


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_repository_round_trip_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    user_id = uuid4()
    workspace_id = uuid4()
    now = datetime.now(UTC)

    try:
        assert database.dialect_name == "postgresql"
        with database.session_factory.begin() as session:
            session.add_all(
                [
                    UserRecord(
                        id=user_id,
                        display_name="PostgreSQL task user",
                        locale="ar",
                        timezone="Asia/Dubai",
                        created_at=now,
                        updated_at=now,
                    ),
                    WorkspaceRecord(
                        id=workspace_id,
                        owner_id=user_id,
                        name="PostgreSQL task workspace",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
        identity_repository = _StaticIdentityRepository(
            SetupState(
                user=User(
                    id=user_id,
                    display_name="PostgreSQL task user",
                    locale="ar",
                    timezone="Asia/Dubai",
                    created_at=now,
                    updated_at=now,
                ),
                workspace=Workspace(
                    id=workspace_id,
                    owner_id=user_id,
                    name="PostgreSQL task workspace",
                    status=WorkspaceStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                ),
                created=True,
            )
        )
        service = TaskService(
            SqlAlchemyTaskRepository(database.session_factory),
            identity_repository,
        )
        created = service.create(
            workspace_id, TaskCreate(title="PostgreSQL integration", priority=TaskPriority.HIGH)
        )
        assert created is not None

        restored = service.get(workspace_id, created.id)
        assert restored is not None
        assert restored.title == "PostgreSQL integration"

        updated = service.update(
            workspace_id,
            created.id,
            TaskUpdate(status=TaskStatus.COMPLETED),
        )
        assert updated is not None
        assert updated.status is TaskStatus.COMPLETED
    finally:
        with database.session_factory.begin() as session:
            owner = session.get(UserRecord, user_id)
            if owner is not None:
                session.delete(owner)
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
def test_leased_tool_queue_recovers_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    now = datetime.now(UTC)
    user_id = uuid4()
    workspace_id = uuid4()
    conversation_id = uuid4()
    run_id = uuid4()
    step_id = uuid4()
    call_id = uuid4()

    try:
        with database.session_factory.begin() as session:
            session.add_all(
                [
                    UserRecord(
                        id=user_id,
                        display_name="PostgreSQL queue user",
                        locale="ar",
                        timezone="Asia/Dubai",
                        created_at=now,
                        updated_at=now,
                    ),
                    WorkspaceRecord(
                        id=workspace_id,
                        owner_id=user_id,
                        name="PostgreSQL queue workspace",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    ),
                    ConversationRecord(
                        id=conversation_id,
                        workspace_id=workspace_id,
                        title="PostgreSQL queue conversation",
                        status="active",
                        summary=None,
                        created_at=now,
                        updated_at=now,
                    ),
                    RunRecord(
                        id=run_id,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        request_message_id=None,
                        trace_id=uuid4(),
                        status="executing",
                        risk="low",
                        autonomy_level=2,
                        error_code=None,
                        started_at=now,
                        completed_at=None,
                        created_at=now,
                        updated_at=now,
                    ),
                    PlanStepRecord(
                        id=step_id,
                        run_id=run_id,
                        position=0,
                        title="اختبار استعادة العامل",
                        status="pending",
                        risk="low",
                        requires_approval=True,
                        tool_name="tasks",
                        operation="create",
                        tool_input={"title": "اختبار"},
                        created_at=now,
                        updated_at=now,
                    ),
                    ToolCallRecord(
                        id=call_id,
                        run_id=run_id,
                        plan_step_id=step_id,
                        tool_name="tasks",
                        operation="create",
                        input={"title": "اختبار"},
                        output=None,
                        status="pending",
                        risk="low",
                        idempotency_key="a" * 64,
                        error_code=None,
                        attempt_count=0,
                        max_attempts=3,
                        available_at=now,
                        lease_owner=None,
                        lease_expires_at=None,
                        started_at=None,
                        completed_at=None,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )

        repository = SqlAlchemyToolCallRepository(database.session_factory)
        abandoned = repository.claim_next(
            "postgres-stopped-worker",
            claimed_at=now,
            lease_seconds=1,
        )
        recovered = repository.claim_next(
            "postgres-recovery-worker",
            claimed_at=now + timedelta(seconds=2),
            lease_seconds=30,
        )

        assert abandoned is not None
        assert recovered is not None
        assert recovered.call.id == abandoned.call.id
        assert recovered.call.attempt_count == 1
        completed = repository.succeed(
            user_id,
            workspace_id,
            conversation_id,
            call_id,
            {"verified": True},
            worker_id="postgres-recovery-worker",
            completed_at=now + timedelta(seconds=3),
        )
        assert completed is not None
        assert completed.status.value == "succeeded"
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
                json={"parts": [{"type": "text", "text": "أنشئ مهمة لاختبار PostgreSQL"}]},
            )
            assert message_response.status_code == 201
            message_id = message_response.json()["id"]

            run_response = client.post(
                f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs",
                json={"request_message_id": message_id, "autonomy_level": 2},
            )
            assert run_response.status_code == 201
            assert run_response.json()["status"] == "received"
            run_id = run_response.json()["id"]
            run_path = (
                f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/runs/{run_id}"
            )
            assert client.get(run_path).json()["status"] == "awaiting_approval"
            approval = client.get(f"{run_path}/approvals").json()[0]
            approved = client.post(
                f"{run_path}/approvals/{approval['id']}/decision",
                json={
                    "decision": "approve",
                    "action_fingerprint": approval["action_fingerprint"],
                },
            )
            assert approved.status_code == 200
            assert client.get(run_path).json()["status"] == "succeeded"
            assert client.get(f"{run_path}/approvals").json()[0]["status"] == "consumed"
            assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "succeeded"
            tasks = client.get(f"/api/v1/workspaces/{workspace_id}/tasks").json()
            assert [task["title"] for task in tasks] == ["لاختبار PostgreSQL"]
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
