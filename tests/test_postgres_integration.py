import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from awn.api.app import create_app
from awn.application.tasks import TaskService
from awn.application.worker import WorkerService
from awn.config import Settings
from awn.domain.identity import SetupState, User, Workspace, WorkspaceStatus
from awn.domain.tasks import Task, TaskCreate, TaskPriority, TaskStatus, TaskUpdate
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.cancellations import SqlAlchemyCancellationRepository
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
from awn.policy.engine import RiskLevel
from awn.tools.contracts import ToolContext, ToolDefinition
from awn.tools.registry import ToolRegistry
from awn.tools.tasks import build_task_create_tool

POSTGRES_URL = os.getenv("AWN_TEST_POSTGRES_URL")


class _StaticIdentityRepository:
    def __init__(self, state: SetupState) -> None:
        self._state = state

    def current(self) -> SetupState:
        return self._state


class _UnusedConversationService:
    def add_assistant_message(self, *_) -> None:
        raise AssertionError("reconciliation must not write a conversation message")


def _insert_cancellable_run(database: Database, *, available_at: datetime) -> tuple[UUID, ...]:
    user_id = uuid4()
    workspace_id = uuid4()
    conversation_id = uuid4()
    run_id = uuid4()
    step_id = uuid4()
    call_id = uuid4()
    with database.session_factory.begin() as session:
        session.add_all(
            [
                UserRecord(
                    id=user_id,
                    display_name="PostgreSQL cancellation user",
                    locale="ar",
                    timezone="Asia/Dubai",
                    created_at=available_at,
                    updated_at=available_at,
                ),
                WorkspaceRecord(
                    id=workspace_id,
                    owner_id=user_id,
                    name="PostgreSQL cancellation workspace",
                    status="active",
                    created_at=available_at,
                    updated_at=available_at,
                ),
                ConversationRecord(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    title="Cancellation race",
                    status="active",
                    summary=None,
                    created_at=available_at,
                    updated_at=available_at,
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
                    started_at=available_at,
                    completed_at=None,
                    created_at=available_at,
                    updated_at=available_at,
                ),
                PlanStepRecord(
                    id=step_id,
                    run_id=run_id,
                    position=0,
                    title="Cancellation race call",
                    status="pending",
                    risk="low",
                    requires_approval=True,
                    tool_name="tasks",
                    operation="create",
                    tool_input={"title": "race"},
                    created_at=available_at,
                    updated_at=available_at,
                ),
                ToolCallRecord(
                    id=call_id,
                    run_id=run_id,
                    plan_step_id=step_id,
                    tool_name="tasks",
                    operation="create",
                    input={"title": "race"},
                    output=None,
                    status="pending",
                    risk="low",
                    idempotency_key=uuid4().hex * 2,
                    error_code=None,
                    attempt_count=0,
                    max_attempts=3,
                    available_at=available_at,
                    lease_owner=None,
                    lease_expires_at=None,
                    started_at=None,
                    effect_committed_at=None,
                    effect_commit_token=None,
                    effect_commit_worker_id=None,
                    completed_at=None,
                    created_at=available_at,
                    updated_at=available_at,
                ),
            ]
        )
    return user_id, workspace_id, conversation_id, run_id, call_id


def _task_registry() -> ToolRegistry:
    def unused_handler(*_):
        raise AssertionError("persistence tests must not invoke tool handlers")

    return ToolRegistry(
        [
            ToolDefinition(
                name="tasks",
                operation="create",
                summary="PostgreSQL persistence fixture",
                input_model=TaskCreate,
                output_model=Task,
                risk=RiskLevel.LOW,
                side_effect=True,
                external=False,
                reversible=True,
                required_scopes=("tasks.write",),
                timeout_seconds=5,
                supports_idempotency=True,
                handler=unused_handler,
            )
        ]
    )


def _task_output(workspace_id: UUID, title: str, at: datetime) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "workspace_id": str(workspace_id),
        "title": title,
        "description": None,
        "status": "pending",
        "priority": "normal",
        "due_at": None,
        "created_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }


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

        repository = SqlAlchemyToolCallRepository(
            database.session_factory,
            _task_registry(),
        )
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
        effect_commit_token = repository.commit_effect(
            user_id,
            workspace_id,
            conversation_id,
            call_id,
            worker_id="postgres-recovery-worker",
        )
        assert effect_commit_token is not None
        completed = repository.succeed(
            user_id,
            workspace_id,
            conversation_id,
            call_id,
            _task_output(workspace_id, "اختبار", now + timedelta(seconds=3)),
            worker_id="postgres-recovery-worker",
            expected_idempotency_key=recovered.call.idempotency_key,
            expected_input=recovered.call.input,
            effect_commit_token=effect_commit_token,
            occurred_at=now + timedelta(seconds=3),
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
def test_claim_and_cancellation_race_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    now = datetime(2000, 1, 1, tzinfo=UTC)
    user_id, workspace_id, conversation_id, run_id, call_id = _insert_cancellable_run(
        database,
        available_at=now,
    )
    tool_calls = SqlAlchemyToolCallRepository(database.session_factory)
    cancellations = SqlAlchemyCancellationRepository(database.session_factory)
    gate = Barrier(2)

    def claim():
        gate.wait()
        return tool_calls.claim_next(
            "postgres-claim-race",
            claimed_at=now + timedelta(seconds=1),
            lease_seconds=30,
        )

    def cancel():
        gate.wait()
        return cancellations.request(
            user_id,
            workspace_id,
            conversation_id,
            run_id,
            received_at=now + timedelta(seconds=1),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed_future = executor.submit(claim)
            cancelled_future = executor.submit(cancel)
            claimed = claimed_future.result(timeout=10)
            cancelled = cancelled_future.result(timeout=10)

        assert cancelled is not None
        assert cancelled.decision.value == "accepted"
        if claimed is not None and claimed.call.id == call_id:
            token = tool_calls.commit_effect(
                user_id,
                workspace_id,
                conversation_id,
                call_id,
                worker_id="postgres-claim-race",
            )
            assert token is None
        with database.session_factory() as session:
            call = session.get(ToolCallRecord, call_id)
            run = session.get(RunRecord, run_id)
            assert call is not None
            assert run is not None
            assert call.status == "cancelled"
            assert call.effect_committed_at is None
            assert run.status == "cancelled"
    finally:
        with database.session_factory.begin() as session:
            owner = session.get(UserRecord, user_id)
            if owner is not None:
                session.delete(owner)
        database.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_effect_commit_and_cancellation_race_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    now = datetime(2000, 1, 2, tzinfo=UTC)
    user_id, workspace_id, conversation_id, run_id, call_id = _insert_cancellable_run(
        database,
        available_at=now,
    )
    tool_calls = SqlAlchemyToolCallRepository(database.session_factory)
    cancellations = SqlAlchemyCancellationRepository(database.session_factory)

    try:
        claimed = tool_calls.claim_next(
            "postgres-effect-race",
            claimed_at=now + timedelta(seconds=1),
            lease_seconds=30,
        )
        assert claimed is not None
        assert claimed.call.id == call_id
        gate = Barrier(2)

        def commit():
            gate.wait()
            return tool_calls.commit_effect(
                user_id,
                workspace_id,
                conversation_id,
                call_id,
                worker_id="postgres-effect-race",
            )

        def cancel():
            gate.wait()
            started = time.perf_counter()
            result = cancellations.request(
                user_id,
                workspace_id,
                conversation_id,
                run_id,
                received_at=now + timedelta(seconds=2),
            )
            return result, time.perf_counter() - started

        with ThreadPoolExecutor(max_workers=2) as executor:
            commit_future = executor.submit(commit)
            cancel_future = executor.submit(cancel)
            token = commit_future.result(timeout=10)
            cancelled, elapsed = cancel_future.result(timeout=10)

        assert cancelled is not None
        assert cancelled.decision.value == "accepted"
        assert elapsed < 2.0
        with database.session_factory() as session:
            call = session.get(ToolCallRecord, call_id)
            run = session.get(RunRecord, run_id)
            assert call is not None
            assert run is not None
            if token is None:
                assert call.status == "cancelled"
                assert run.status == "cancelled"
            else:
                assert call.status == "executing"
                assert call.effect_commit_token == token
                assert run.status == "cancellation_requested"
                assert (
                    tool_calls.reconcile_expired_cancellations(
                        observed_at=now + timedelta(seconds=32)
                    )
                    == 1
                )
                session.expire_all()
                assert session.get(ToolCallRecord, call_id).status == "outcome_unknown"
                assert session.get(RunRecord, run_id).status == "cancellation_uncertain"
    finally:
        with database.session_factory.begin() as session:
            owner = session.get(UserRecord, user_id)
            if owner is not None:
                session.delete(owner)
        database.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_completion_and_cancellation_race_on_postgresql() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    now = datetime(2000, 1, 3, tzinfo=UTC)
    user_id, workspace_id, conversation_id, run_id, call_id = _insert_cancellable_run(
        database,
        available_at=now,
    )
    tool_calls = SqlAlchemyToolCallRepository(database.session_factory, _task_registry())
    cancellations = SqlAlchemyCancellationRepository(database.session_factory)

    try:
        claimed = tool_calls.claim_next(
            "postgres-complete-race",
            claimed_at=now + timedelta(seconds=1),
            lease_seconds=30,
        )
        assert claimed is not None
        token = tool_calls.commit_effect(
            user_id,
            workspace_id,
            conversation_id,
            call_id,
            worker_id="postgres-complete-race",
        )
        assert token is not None
        gate = Barrier(2)

        def complete():
            gate.wait()
            return tool_calls.succeed(
                user_id,
                workspace_id,
                conversation_id,
                call_id,
                _task_output(workspace_id, "race", now + timedelta(seconds=3)),
                worker_id="postgres-complete-race",
                expected_idempotency_key=claimed.call.idempotency_key,
                expected_input=claimed.call.input,
                effect_commit_token=token,
                occurred_at=now + timedelta(seconds=3),
            )

        def cancel():
            gate.wait()
            return cancellations.request(
                user_id,
                workspace_id,
                conversation_id,
                run_id,
                received_at=now + timedelta(seconds=3),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            completed_future = executor.submit(complete)
            cancelled_future = executor.submit(cancel)
            completed_future.result(timeout=10)
            cancelled = cancelled_future.result(timeout=10)

        assert cancelled is not None
        assert cancelled.decision.value in {"accepted", "too_late"}
        with database.session_factory() as session:
            call = session.get(ToolCallRecord, call_id)
            run = session.get(RunRecord, run_id)
            assert call is not None
            assert run is not None
            assert call.status == "succeeded"
            assert run.status == "succeeded"
            cancellation = cancellations.get(user_id, workspace_id, conversation_id, run_id)
            if cancellation is not None:
                assert cancellation.status.value == "completed"
    finally:
        with database.session_factory.begin() as session:
            owner = session.get(UserRecord, user_id)
            if owner is not None:
                session.delete(owner)
        database.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_restarted_worker_reconciles_post_effect_crash_without_reinvoking_handler() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    now = datetime.now(UTC)
    user_id, workspace_id, conversation_id, run_id, call_id = _insert_cancellable_run(
        database,
        available_at=now,
    )
    identity = _StaticIdentityRepository(
        SetupState(
            user=User(
                id=user_id,
                display_name="PostgreSQL reconciliation user",
                locale="ar",
                timezone="Asia/Dubai",
                created_at=now,
                updated_at=now,
            ),
            workspace=Workspace(
                id=workspace_id,
                owner_id=user_id,
                name="PostgreSQL reconciliation workspace",
                status=WorkspaceStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
            created=False,
        )
    )
    tasks = TaskService(SqlAlchemyTaskRepository(database.session_factory), identity)
    definition = build_task_create_tool(tasks)
    registry = ToolRegistry([definition])
    repository = SqlAlchemyToolCallRepository(database.session_factory, registry)

    try:
        leased = repository.claim_next(
            "postgres-crashed-worker",
            claimed_at=now,
            lease_seconds=1,
        )
        assert leased is not None
        token = repository.commit_effect(
            user_id,
            workspace_id,
            conversation_id,
            call_id,
            worker_id="postgres-crashed-worker",
        )
        assert token is not None
        context = ToolContext(
            owner_id=user_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            trace_id=leased.run.trace_id,
            tool_call_id=call_id,
            idempotency_key=leased.call.idempotency_key,
        )
        output = registry.execute(
            leased.call.tool_name,
            leased.call.operation,
            leased.call.input,
            context,
        )
        cancellation = SqlAlchemyCancellationRepository(database.session_factory).request(
            user_id,
            workspace_id,
            conversation_id,
            run_id,
            received_at=now + timedelta(milliseconds=100),
        )
        assert cancellation is not None
        assert cancellation.cancellation is not None
        assert cancellation.cancellation.status.value == "accepted"

        def forbidden_handler(*_):
            raise AssertionError("a reconciliation worker must not invoke the handler")

        restart_registry = ToolRegistry(
            [replace(definition, handler=forbidden_handler)]
        )
        restarted_worker = WorkerService(
            SqlAlchemyToolCallRepository(database.session_factory, restart_registry),
            _UnusedConversationService(),  # type: ignore[arg-type]
            restart_registry,
            lease_seconds=1,
            worker_id="postgres-reconciliation-worker",
        )
        assert restarted_worker.run_once(now=now + timedelta(seconds=2)) is True

        with database.session_factory() as session:
            call = session.get(ToolCallRecord, call_id)
            run = session.get(RunRecord, run_id)
            assert call is not None
            assert run is not None
            assert call.status == "succeeded"
            assert call.output == output.model_dump(mode="json")
            assert run.status == "succeeded"
        final = SqlAlchemyCancellationRepository(database.session_factory).get(
            user_id,
            workspace_id,
            conversation_id,
            run_id,
        )
        assert final is not None
        assert final.status.value == "completed"
        assert any(
            event.source_type.value == "reconciliation_worker"
            and event.evidence_code.value == "VALIDATED_TOOL_OUTPUT"
            for event in final.events
        )
    finally:
        with database.session_factory.begin() as session:
            owner = session.get(UserRecord, user_id)
            if owner is not None:
                session.delete(owner)
        database.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_causal_timestamps_are_captured_only_after_postgresql_run_lock() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    base_time = datetime(2026, 8, 24, 9, tzinfo=UTC)
    user_id, workspace_id, conversation_id, run_id, call_id = _insert_cancellable_run(
        database,
        available_at=base_time,
    )
    effect_time = base_time + timedelta(seconds=10)
    request_time = base_time + timedelta(seconds=20)
    occurrence_time = base_time + timedelta(seconds=15)
    result_observation_time = base_time + timedelta(seconds=30)
    effect_clock_called = Event()
    cancellation_clock_called = Event()
    result_clock_called = Event()

    def effect_clock() -> datetime:
        effect_clock_called.set()
        return effect_time

    def cancellation_clock() -> datetime:
        cancellation_clock_called.set()
        return request_time

    def result_clock() -> datetime:
        result_clock_called.set()
        return result_observation_time

    tool_calls = SqlAlchemyToolCallRepository(
        database.session_factory,
        clock=effect_clock,
    )
    cancellations = SqlAlchemyCancellationRepository(
        database.session_factory,
        clock=cancellation_clock,
    )

    try:
        claimed = tool_calls.claim_next(
            "postgres-timestamp-worker",
            claimed_at=base_time + timedelta(seconds=1),
            lease_seconds=60,
        )
        assert claimed is not None
        commit_started = Event()

        def commit():
            commit_started.set()
            return tool_calls.commit_effect(
                user_id,
                workspace_id,
                conversation_id,
                call_id,
                worker_id="postgres-timestamp-worker",
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            with database.session_factory.begin() as blocker:
                blocker.scalar(
                    select(RunRecord).where(RunRecord.id == run_id).with_for_update(of=RunRecord)
                )
                future = executor.submit(commit)
                assert commit_started.wait(timeout=1)
                assert not effect_clock_called.wait(timeout=0.2)
            token = future.result(timeout=3)
        assert token is not None
        assert effect_clock_called.is_set()

        cancel_started = Event()

        def cancel():
            cancel_started.set()
            return cancellations.request(
                user_id,
                workspace_id,
                conversation_id,
                run_id,
                received_at=base_time + timedelta(seconds=2),
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            with database.session_factory.begin() as blocker:
                blocker.scalar(
                    select(RunRecord).where(RunRecord.id == run_id).with_for_update(of=RunRecord)
                )
                future = executor.submit(cancel)
                assert cancel_started.wait(timeout=1)
                assert not cancellation_clock_called.wait(timeout=0.2)
            result = future.result(timeout=3)
        assert result is not None
        assert result.cancellation is not None
        assert result.cancellation.requested_at == request_time
        assert result.cancellation.received_at == base_time + timedelta(seconds=2)
        assert result.cancellation.requested_at > result.cancellation.received_at

        result_calls = SqlAlchemyToolCallRepository(
            database.session_factory,
            _task_registry(),
            clock=result_clock,
        )
        completion_started = Event()

        def complete():
            completion_started.set()
            return result_calls.succeed(
                user_id,
                workspace_id,
                conversation_id,
                call_id,
                _task_output(workspace_id, "race", occurrence_time),
                worker_id="postgres-timestamp-worker",
                expected_idempotency_key=claimed.call.idempotency_key,
                expected_input=claimed.call.input,
                effect_commit_token=token,
                occurred_at=occurrence_time,
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            with database.session_factory.begin() as blocker:
                blocker.scalar(
                    select(RunRecord).where(RunRecord.id == run_id).with_for_update(of=RunRecord)
                )
                future = executor.submit(complete)
                assert completion_started.wait(timeout=1)
                assert not result_clock_called.wait(timeout=0.2)
            completed = future.result(timeout=3)
        assert completed is not None
        assert result_clock_called.is_set()

        with database.session_factory() as session:
            call = session.get(ToolCallRecord, call_id)
            run = session.get(RunRecord, run_id)
            assert call is not None
            assert run is not None
            assert call.effect_committed_at == effect_time
            assert call.effect_committed_at <= result.cancellation.requested_at
            cancellation = cancellations.get(
                user_id,
                workspace_id,
                conversation_id,
                run_id,
            )
            assert cancellation is not None
            assert cancellation.updated_at == result_observation_time
            assert cancellation.updated_at >= cancellation.requested_at
            assert call.updated_at == result_observation_time
            assert run.updated_at == result_observation_time
            result_event = next(
                event
                for event in cancellation.events
                if event.evidence_code.value == "VALIDATED_TOOL_OUTPUT"
            )
            assert result_event.occurred_at == occurrence_time
            assert result_event.observed_at == result_observation_time
            assert [event.sequence_no for event in cancellation.events] == list(
                range(1, len(cancellation.events) + 1)
            )
    finally:
        with database.session_factory.begin() as session:
            owner = session.get(UserRecord, user_id)
            if owner is not None:
                session.delete(owner)
        database.dispose()


@pytest.mark.skipif(not POSTGRES_URL, reason="AWN_TEST_POSTGRES_URL is not configured")
def test_cancellation_uses_the_declared_postgresql_lock_order() -> None:
    assert POSTGRES_URL is not None
    database = Database(POSTGRES_URL)
    now = datetime(2026, 8, 24, 10, tzinfo=UTC)
    user_id, workspace_id, conversation_id, run_id, _ = _insert_cancellable_run(
        database,
        available_at=now,
    )
    lock_statements: list[str] = []

    def capture_locks(_, __, statement, ___, ____, _____):
        normalized = " ".join(statement.lower().split())
        if "for update" in normalized:
            lock_statements.append(normalized)

    event.listen(database.engine, "before_cursor_execute", capture_locks)
    try:
        result = SqlAlchemyCancellationRepository(database.session_factory).request(
            user_id,
            workspace_id,
            conversation_id,
            run_id,
            received_at=now + timedelta(seconds=1),
        )
        assert result is not None
        assert result.decision.value == "accepted"
        assert len(lock_statements) >= 4
        assert "for update of runs" in lock_statements[0]
        assert "for update of tool_calls" in lock_statements[1]
        assert "for update of run_cancellations" in lock_statements[2]
        assert "for update of plan_steps" in lock_statements[3]
    finally:
        event.remove(database.engine, "before_cursor_execute", capture_locks)
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
