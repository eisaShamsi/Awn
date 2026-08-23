from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from awn.domain.conversations import MessagePart, MessagePartType
from awn.domain.runs import Run, RunRisk, RunStatus
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.models import (
    ConversationRecord,
    MessageRecord,
    PlanStepRecord,
    RunRecord,
    UserRecord,
    WorkspaceRecord,
)


def _new_run(now: datetime) -> Run:
    return Run(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        trace_id=uuid4(),
        status=RunStatus.RECEIVED,
        risk=RunRisk.LOW,
        autonomy_level=0,
        created_at=now,
        updated_at=now,
    )


def test_run_follows_the_approved_execution_lifecycle() -> None:
    now = datetime(2026, 8, 23, 8, tzinfo=UTC)
    run = _new_run(now)

    run = run.transition_to(RunStatus.PLANNING, at=now)
    run = run.transition_to(RunStatus.READY, at=now)
    run = run.transition_to(RunStatus.AWAITING_APPROVAL, at=now)
    run = run.transition_to(RunStatus.EXECUTING, at=now)
    run = run.transition_to(RunStatus.VERIFYING, at=now)
    run = run.transition_to(RunStatus.SUCCEEDED, at=now)

    assert run.status is RunStatus.SUCCEEDED
    assert run.started_at == now
    assert run.completed_at == now

    with pytest.raises(ValueError, match="invalid run transition"):
        run.transition_to(RunStatus.EXECUTING, at=now)


def test_run_rejects_naive_timestamps_and_invalid_autonomy() -> None:
    aware_now = datetime(2026, 8, 23, 8, tzinfo=UTC)

    with pytest.raises(ValidationError, match="timestamps must include a timezone"):
        _new_run(datetime(2026, 8, 23, 8))

    invalid_run = _new_run(aware_now).model_dump()
    invalid_run["autonomy_level"] = 4
    with pytest.raises(ValidationError, match="less than or equal to 3"):
        Run.model_validate(invalid_run)


def test_message_parts_are_structured_and_non_empty() -> None:
    text = MessagePart(type=MessagePartType.TEXT, text="ابدأ التنفيذ")
    tool = MessagePart(
        type=MessagePartType.TOOL_CALL,
        data={"tool": "github", "operation": "create_issue"},
    )

    assert text.text == "ابدأ التنفيذ"
    assert tool.data == {"tool": "github", "operation": "create_issue"}

    with pytest.raises(ValidationError, match="text parts require non-blank text"):
        MessagePart(type=MessagePartType.TEXT, text="  ")

    with pytest.raises(ValidationError, match="tool_result parts require data"):
        MessagePart(type=MessagePartType.TOOL_RESULT)


def _insert_execution_graph(database: Database, *, autonomy_level: int = 2) -> dict[str, object]:
    now = datetime(2026, 8, 23, 8, tzinfo=UTC)
    identifiers = {
        "user": uuid4(),
        "workspace": uuid4(),
        "conversation": uuid4(),
        "message": uuid4(),
        "run": uuid4(),
        "trace": uuid4(),
        "step": uuid4(),
    }

    with database.session_factory.begin() as session:
        session.add_all(
            [
                UserRecord(
                    id=identifiers["user"],
                    display_name="عيسى",
                    locale="ar",
                    timezone="Asia/Dubai",
                    created_at=now,
                    updated_at=now,
                ),
                WorkspaceRecord(
                    id=identifiers["workspace"],
                    owner_id=identifiers["user"],
                    name="مساحة عَوْن",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                ConversationRecord(
                    id=identifiers["conversation"],
                    workspace_id=identifiers["workspace"],
                    title="بداية عَوْن",
                    status="active",
                    summary=None,
                    created_at=now,
                    updated_at=now,
                ),
                MessageRecord(
                    id=identifiers["message"],
                    conversation_id=identifiers["conversation"],
                    role="user",
                    content=[{"type": "text", "text": "نفذ"}],
                    created_at=now,
                ),
                RunRecord(
                    id=identifiers["run"],
                    workspace_id=identifiers["workspace"],
                    conversation_id=identifiers["conversation"],
                    request_message_id=identifiers["message"],
                    trace_id=identifiers["trace"],
                    status="received",
                    risk="low",
                    autonomy_level=autonomy_level,
                    error_code=None,
                    started_at=None,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                ),
                PlanStepRecord(
                    id=identifiers["step"],
                    run_id=identifiers["run"],
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

    return identifiers


def test_execution_graph_is_persisted_and_scoped_to_its_owner(database: Database) -> None:
    identifiers = _insert_execution_graph(database)

    with database.session_factory() as session:
        run = session.get(RunRecord, identifiers["run"])
        assert run is not None
        assert run.workspace.owner_id == identifiers["user"]
        assert run.conversation.workspace_id == identifiers["workspace"]
        assert [step.title for step in run.steps] == ["تحليل الطلب"]

    with database.session_factory.begin() as session:
        owner = session.get(UserRecord, identifiers["user"])
        assert owner is not None
        session.delete(owner)

    with database.session_factory() as session:
        for record_type in (
            UserRecord,
            WorkspaceRecord,
            ConversationRecord,
            MessageRecord,
            RunRecord,
            PlanStepRecord,
        ):
            count = session.scalar(select(func.count()).select_from(record_type))
            assert count == 0


def test_database_enforces_autonomy_boundary(database: Database) -> None:
    with pytest.raises(IntegrityError):
        _insert_execution_graph(database, autonomy_level=4)


def test_database_rejects_run_from_another_workspace(database: Database) -> None:
    now = datetime(2026, 8, 23, 8, tzinfo=UTC)
    user_id = uuid4()
    conversation_workspace_id = uuid4()
    run_workspace_id = uuid4()
    conversation_id = uuid4()

    with pytest.raises(IntegrityError), database.session_factory.begin() as session:
        session.add_all(
            [
                UserRecord(
                    id=user_id,
                    display_name="عيسى",
                    locale="ar",
                    timezone="Asia/Dubai",
                    created_at=now,
                    updated_at=now,
                ),
                WorkspaceRecord(
                    id=conversation_workspace_id,
                    owner_id=user_id,
                    name="المحادثة",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                WorkspaceRecord(
                    id=run_workspace_id,
                    owner_id=user_id,
                    name="التشغيل",
                    status="active",
                    created_at=now,
                    updated_at=now,
                ),
                ConversationRecord(
                    id=conversation_id,
                    workspace_id=conversation_workspace_id,
                    title=None,
                    status="active",
                    summary=None,
                    created_at=now,
                    updated_at=now,
                ),
                RunRecord(
                    id=uuid4(),
                    workspace_id=run_workspace_id,
                    conversation_id=conversation_id,
                    request_message_id=None,
                    trace_id=uuid4(),
                    status="received",
                    risk="low",
                    autonomy_level=0,
                    error_code=None,
                    started_at=None,
                    completed_at=None,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
