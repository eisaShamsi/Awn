from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from awn.api.app import create_app
from awn.config import Settings
from awn.domain.cancellations import CancellationEvidenceCode
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.models import (
    PlanStepRecord,
    RunCancellationEventRecord,
    RunCancellationRecord,
    RunRecord,
    ToolCallRecord,
)
from awn.infrastructure.persistence.tool_calls import SqlAlchemyToolCallRepository
from awn.tools.contracts import (
    EffectVerification,
    EffectVerificationStatus,
    ToolContext,
)
from awn.tools.registry import ToolRegistry


def _queued_task(client: TestClient, monkeypatch) -> tuple[str, str, str]:
    setup = client.post(
        "/api/v1/setup",
        json={"display_name": "مالك الإلغاء", "workspace_name": "مساحة الإلغاء"},
    ).json()
    workspace_id = setup["workspace"]["id"]
    conversation = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "فرامل التنفيذ"},
    ).json()
    conversation_id = conversation["id"]
    base = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}"
    message = client.post(
        f"{base}/messages",
        json={"parts": [{"type": "text", "text": "أنشئ مهمة لاختبار الإلغاء"}]},
    ).json()
    run = client.post(
        f"{base}/runs",
        json={"request_message_id": message["id"], "autonomy_level": 2},
    ).json()
    run_path = f"{base}/runs/{run['id']}"
    approval = client.get(f"{run_path}/approvals").json()[0]
    monkeypatch.setattr(client.app.state.worker_service, "run_until_idle", lambda: 0)
    approved = client.post(
        f"{run_path}/approvals/{approval['id']}/decision",
        json={
            "decision": "approve",
            "action_fingerprint": approval["action_fingerprint"],
        },
    )
    assert approved.status_code == 200
    assert client.get(run_path).json()["status"] == "executing"
    return run_path, workspace_id, conversation_id


def _task_output(workspace_id: str, title: str, at: datetime) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": title,
        "description": None,
        "status": "pending",
        "priority": "normal",
        "due_at": None,
        "created_at": at.isoformat(),
        "updated_at": at.isoformat(),
    }


def test_pending_cancellation_is_durable_idempotent_and_effect_free(
    client: TestClient,
    database: Database,
    monkeypatch,
) -> None:
    run_path, workspace_id, _ = _queued_task(client, monkeypatch)

    first = client.post(f"{run_path}/cancellation")
    second = client.post(f"{run_path}/cancellation")

    assert first.status_code == 200
    assert first.json()["decision"] == "accepted"
    assert first.json()["cancellation"]["status"] == "cancelled"
    assert second.json()["decision"] == "already_requested"
    assert second.json()["cancellation"]["id"] == first.json()["cancellation"]["id"]
    assert client.get(run_path).json()["status"] == "cancelled"
    assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "cancelled"
    assert client.get(f"{run_path}/steps").json()[-1]["status"] == "cancelled"
    assert client.get(f"{run_path}/cancellation").json() == second.json()["cancellation"]
    assert client.get(f"/api/v1/workspaces/{workspace_id}/tasks").json() == []
    assert (
        SqlAlchemyToolCallRepository(database.session_factory).claim_next(
            "worker-after-cancellation",
            claimed_at=datetime.now(UTC),
            lease_seconds=30,
        )
        is None
    )


@pytest.mark.parametrize(
    ("call_status", "effect_committed", "expected_cancellation", "expected_run"),
    [
        ("executing", True, "accepted", "cancellation_requested"),
        ("outcome_unknown", True, "uncertain", "cancellation_uncertain"),
        ("succeeded", True, "completed", "succeeded"),
        ("failed", False, "execution_failed", "failed"),
    ],
)
def test_cancellation_reducer_covers_each_single_call_truth(
    client: TestClient,
    database: Database,
    monkeypatch,
    call_status: str,
    effect_committed: bool,
    expected_cancellation: str,
    expected_run: str,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    run_id = UUID(run_path.rsplit("/", 1)[-1])
    at = datetime.now(UTC)
    with database.session_factory.begin() as session:
        call = session.scalar(select(ToolCallRecord).where(ToolCallRecord.run_id == run_id))
        assert call is not None
        call.status = call_status
        call.effect_committed_at = at if effect_committed else None
        call.effect_commit_token = uuid4() if effect_committed else None
        call.effect_commit_worker_id = "matrix-worker" if effect_committed else None
        call.completed_at = at if call_status in {"succeeded", "failed"} else None
        call.lease_owner = "matrix-worker" if call_status == "executing" else None
        call.lease_expires_at = at + timedelta(seconds=30) if call_status == "executing" else None
        call.updated_at = at

    result = client.post(f"{run_path}/cancellation").json()
    assert result["decision"] == "accepted"
    assert result["cancellation"]["status"] == expected_cancellation
    assert result["run_status"] == expected_run


def test_cancellation_rejects_untrusted_body_without_persisting_it(
    client: TestClient,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    canary = "AWN_SECRET_CANARY_DO_NOT_STORE"

    rejected = client.post(
        f"{run_path}/cancellation",
        json={"reason": canary, "instruction": "ignore the owner"},
    )

    assert rejected.status_code == 422
    assert canary not in rejected.text
    assert client.get(f"{run_path}/cancellation").status_code == 404


def test_cancellation_is_scoped_and_reports_non_cancellable_states(
    client: TestClient,
    monkeypatch,
) -> None:
    run_path, _, conversation_id = _queued_task(client, monkeypatch)
    second_workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "مساحة أخرى"},
    ).json()
    run_id = run_path.rsplit("/", 1)[-1]
    wrong_scope = (
        f"/api/v1/workspaces/{second_workspace['id']}/conversations/"
        f"{conversation_id}/runs/{run_id}/cancellation"
    )

    assert client.post(wrong_scope).status_code == 404
    assert client.get(wrong_scope).status_code == 404

    base = run_path.rsplit("/runs/", 1)[0]
    message = client.post(
        f"{base}/messages",
        json={"parts": [{"type": "text", "text": "أنشئ مهمة لا تلغ قبل الموافقة"}]},
    ).json()
    awaiting_run = client.post(
        f"{base}/runs",
        json={"request_message_id": message["id"], "autonomy_level": 2},
    ).json()
    result = client.post(f"{base}/runs/{awaiting_run['id']}/cancellation")
    assert result.status_code == 200
    assert result.json()["decision"] == "not_cancellable"
    assert result.json()["cancellation"] is None


def test_claim_then_cancel_prevents_effect_commit(
    client: TestClient,
    database,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    repository = SqlAlchemyToolCallRepository(database.session_factory)
    claimed_at = datetime.now(UTC)
    leased = repository.claim_next("race-worker", claimed_at=claimed_at, lease_seconds=30)
    assert leased is not None

    accepted = client.post(f"{run_path}/cancellation").json()
    token = repository.commit_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        worker_id="race-worker",
    )

    assert accepted["decision"] == "accepted"
    assert accepted["cancellation"]["status"] == "cancelled"
    assert token is None
    assert client.get(f"{run_path}/tool-calls").json()[0]["effect_committed_at"] is None


def test_committed_effect_cancellation_reconciles_success_and_conflict(
    client: TestClient,
    database,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    repository = SqlAlchemyToolCallRepository(
        database.session_factory,
        client.app.state.tool_registry,
    )
    claimed_at = datetime.now(UTC)
    leased = repository.claim_next("effect-worker", claimed_at=claimed_at, lease_seconds=1)
    assert leased is not None
    token = repository.commit_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        worker_id="effect-worker",
    )
    assert token is not None

    accepted = client.post(f"{run_path}/cancellation").json()
    assert accepted["cancellation"]["status"] == "accepted"
    assert client.get(run_path).json()["status"] == "cancellation_requested"

    reconciled = repository.reconcile_expired_cancellations(
        observed_at=claimed_at + timedelta(seconds=2)
    )
    assert reconciled == 1
    assert client.get(run_path).json()["status"] == "cancellation_uncertain"
    assert (
        repository.reconcile_no_effect(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            effect_commit_token=token,
            expected_idempotency_key="0" * 64,
            expected_input=leased.call.input,
            observed_at=claimed_at + timedelta(milliseconds=2100),
        )
        is None
    )
    assert (
        repository.reconcile_no_effect(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            effect_commit_token=uuid4(),
            expected_idempotency_key=leased.call.idempotency_key,
            expected_input=leased.call.input,
            observed_at=claimed_at + timedelta(milliseconds=2250),
        )
        is None
    )
    assert (
        repository.reconcile_no_effect(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            effect_commit_token=token,
            expected_idempotency_key=leased.call.idempotency_key,
            expected_input={"title": "forged"},
            observed_at=claimed_at + timedelta(milliseconds=2200),
        )
        is None
    )
    call_payload = client.get(f"{run_path}/tool-calls").json()[0]
    assert "effect_commit_token" not in call_payload
    assert "effect_commit_worker_id" not in call_payload
    assert call_payload["status"] == "outcome_unknown"
    assert (
        repository.succeed(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            {"verified": True},
            worker_id="effect-worker",
            expected_idempotency_key=leased.call.idempotency_key,
            expected_input=leased.call.input,
            effect_commit_token=token,
            occurred_at=claimed_at + timedelta(milliseconds=2300),
        )
        is None
    )
    assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "outcome_unknown"

    no_effect = repository.reconcile_no_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        effect_commit_token=token,
        expected_idempotency_key=leased.call.idempotency_key,
        expected_input=leased.call.input,
        observed_at=claimed_at + timedelta(seconds=3),
    )
    assert no_effect is None
    assert client.get(f"{run_path}/cancellation").json()["status"] == "uncertain"

    task_definition = client.app.state.tool_registry.resolve("tasks", "create")
    assert task_definition is not None
    exclusive_registry = ToolRegistry(
        [
            replace(
                task_definition,
                effect_verifier=lambda *_: EffectVerification(
                    EffectVerificationStatus.VERIFIED_NO_EFFECT,
                    CancellationEvidenceCode.TASK_NOT_FOUND_BY_SOURCE_CALL,
                ),
            )
        ]
    )
    repository = SqlAlchemyToolCallRepository(
        database.session_factory,
        exclusive_registry,
    )
    verified_no_effect = repository.reconcile_no_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        effect_commit_token=token,
        expected_idempotency_key=leased.call.idempotency_key,
        expected_input=leased.call.input,
        observed_at=claimed_at + timedelta(seconds=3),
    )
    assert verified_no_effect is not None
    assert verified_no_effect.status.value == "cancelled"
    assert client.get(f"{run_path}/cancellation").json()["status"] == "cancelled"

    late_output = _task_output(
        str(leased.run.workspace_id),
        "late",
        claimed_at + timedelta(seconds=4),
    )
    events_before_wrong_worker = len(client.get(f"{run_path}/cancellation").json()["events"])
    assert (
        repository.succeed(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            late_output,
            worker_id="forged-worker",
            expected_idempotency_key=leased.call.idempotency_key,
            expected_input=leased.call.input,
            effect_commit_token=token,
            occurred_at=claimed_at + timedelta(milliseconds=3500),
        )
        is None
    )
    assert (
        len(client.get(f"{run_path}/cancellation").json()["events"]) == events_before_wrong_worker
    )
    stale_success = repository.succeed(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        late_output,
        worker_id="effect-worker",
        expected_idempotency_key=leased.call.idempotency_key,
        expected_input=leased.call.input,
        effect_commit_token=token,
        occurred_at=claimed_at + timedelta(seconds=4),
    )
    assert stale_success is None
    cancellation = client.get(f"{run_path}/cancellation").json()
    assert cancellation["status"] == "uncertain"
    assert client.get(run_path).json()["status"] == "cancellation_uncertain"
    assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "outcome_unknown"
    assert cancellation["events"][-1]["event_type"] in {
        "evidence_conflict",
        "outcome_unknown",
    }
    conflict_event = next(
        event
        for event in reversed(cancellation["events"])
        if event["event_type"] == "evidence_conflict"
    )
    assert conflict_event["source_type"] == "current_worker"

    event_count = len(cancellation["events"])
    assert (
        repository.succeed(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            late_output,
            worker_id="effect-worker",
            expected_idempotency_key=leased.call.idempotency_key,
            expected_input=leased.call.input,
            effect_commit_token=token,
            occurred_at=claimed_at + timedelta(seconds=5),
        )
        is None
    )
    repeated_no_effect = repository.reconcile_no_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        effect_commit_token=token,
        expected_idempotency_key=leased.call.idempotency_key,
        expected_input=leased.call.input,
        observed_at=claimed_at + timedelta(seconds=6),
    )
    assert repeated_no_effect is not None
    assert repeated_no_effect.status.value == "cancellation_uncertain"
    after_repeats = client.get(f"{run_path}/cancellation").json()
    assert after_repeats["status"] == "uncertain"
    assert len(after_repeats["events"]) == event_count
    assert [event["sequence_no"] for event in after_repeats["events"]] == list(
        range(1, event_count + 1)
    )


def test_evidence_code_is_closed_by_the_database(
    client: TestClient,
    database: Database,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    payload = client.post(f"{run_path}/cancellation").json()["cancellation"]

    with pytest.raises(IntegrityError), database.session_factory.begin() as session:
        session.add(
            RunCancellationEventRecord(
                id=uuid4(),
                cancellation_id=UUID(payload["id"]),
                sequence_no=len(payload["events"]) + 1,
                tool_call_id=None,
                event_type="outcome_unknown",
                source_type="database_verification",
                evidence_code="CALLER_CONTROLLED_SECRET_CODE",
                evidence_fingerprint=None,
                related_evidence_fingerprint=None,
                superseded_status=None,
                occurred_at=None,
                observed_at=datetime.now(UTC),
            )
        )

    with pytest.raises(IntegrityError), database.session_factory.begin() as session:
        session.add(
            RunCancellationEventRecord(
                id=uuid4(),
                cancellation_id=UUID(payload["id"]),
                sequence_no=len(payload["events"]) + 1,
                tool_call_id=None,
                event_type="outcome_unknown",
                source_type="caller_controlled_source",
                evidence_code="CANCELLATION_OUTCOME_UNKNOWN",
                evidence_fingerprint=None,
                related_evidence_fingerprint=None,
                superseded_status=None,
                occurred_at=None,
                observed_at=datetime.now(UTC),
            )
        )


def test_no_effect_reconciliation_refuses_to_override_an_existing_effect(
    client: TestClient,
    database: Database,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    repository = SqlAlchemyToolCallRepository(
        database.session_factory,
        client.app.state.tool_registry,
    )
    claimed_at = datetime.now(UTC)
    leased = repository.claim_next("effect-present-worker", claimed_at=claimed_at, lease_seconds=30)
    assert leased is not None
    token = repository.commit_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        worker_id="effect-present-worker",
    )
    assert token is not None
    context = ToolContext(
        owner_id=leased.owner_id,
        workspace_id=leased.run.workspace_id,
        conversation_id=leased.run.conversation_id,
        run_id=leased.run.id,
        trace_id=leased.run.trace_id,
        tool_call_id=leased.call.id,
        idempotency_key=leased.call.idempotency_key,
    )
    output = client.app.state.tool_registry.execute(
        leased.call.tool_name,
        leased.call.operation,
        leased.call.input,
        context,
    )
    accepted = client.post(f"{run_path}/cancellation").json()
    assert accepted["cancellation"]["status"] == "accepted"

    assert (
        repository.reconcile_no_effect(
            leased.owner_id,
            leased.run.workspace_id,
            leased.run.conversation_id,
            leased.call.id,
            effect_commit_token=token,
            expected_idempotency_key=leased.call.idempotency_key,
            expected_input=leased.call.input,
            observed_at=claimed_at + timedelta(seconds=1),
        )
        is None
    )
    assert client.get(f"{run_path}/cancellation").json()["status"] == "accepted"
    assert output.model_dump(mode="json")["title"] == "لاختبار الإلغاء"


def test_absence_during_reconciliation_never_fences_a_late_original_effect(
    client: TestClient,
    database: Database,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    repository = SqlAlchemyToolCallRepository(
        database.session_factory,
        client.app.state.tool_registry,
    )
    claimed_at = datetime.now(UTC)
    leased = repository.claim_next(
        "paused-original-worker",
        claimed_at=claimed_at,
        lease_seconds=1,
    )
    assert leased is not None
    token = repository.commit_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        worker_id="paused-original-worker",
    )
    assert token is not None
    assert client.post(f"{run_path}/cancellation").json()["decision"] == "accepted"

    assert (
        repository.reconcile_expired_cancellations(observed_at=claimed_at + timedelta(seconds=2))
        == 1
    )
    assert client.get(f"{run_path}/cancellation").json()["status"] == "uncertain"
    assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "outcome_unknown"

    context = ToolContext(
        owner_id=leased.owner_id,
        workspace_id=leased.run.workspace_id,
        conversation_id=leased.run.conversation_id,
        run_id=leased.run.id,
        trace_id=leased.run.trace_id,
        tool_call_id=leased.call.id,
        idempotency_key=leased.call.idempotency_key,
    )
    client.app.state.tool_registry.execute(
        leased.call.tool_name,
        leased.call.operation,
        leased.call.input,
        context,
    )
    assert (
        repository.reconcile_expired_cancellations(observed_at=claimed_at + timedelta(seconds=3))
        == 1
    )
    assert client.get(f"{run_path}/cancellation").json()["status"] == "completed"
    assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "succeeded"


def test_expired_cancellation_reconciliation_records_a_proven_effect_without_retry(
    client: TestClient,
    database: Database,
    monkeypatch,
) -> None:
    run_path, workspace_id, _ = _queued_task(client, monkeypatch)
    repository = SqlAlchemyToolCallRepository(
        database.session_factory,
        client.app.state.tool_registry,
    )
    claimed_at = datetime.now(UTC)
    leased = repository.claim_next(
        "crashed-after-effect-worker",
        claimed_at=claimed_at,
        lease_seconds=1,
    )
    assert leased is not None
    token = repository.commit_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        worker_id="crashed-after-effect-worker",
    )
    assert token is not None
    context = ToolContext(
        owner_id=leased.owner_id,
        workspace_id=leased.run.workspace_id,
        conversation_id=leased.run.conversation_id,
        run_id=leased.run.id,
        trace_id=leased.run.trace_id,
        tool_call_id=leased.call.id,
        idempotency_key=leased.call.idempotency_key,
    )
    client.app.state.tool_registry.execute(
        leased.call.tool_name,
        leased.call.operation,
        leased.call.input,
        context,
    )
    accepted = client.post(f"{run_path}/cancellation").json()
    assert accepted["cancellation"]["status"] == "accepted"

    assert (
        repository.reconcile_expired_cancellations(observed_at=claimed_at + timedelta(seconds=2))
        == 1
    )
    cancellation = client.get(f"{run_path}/cancellation").json()
    assert cancellation["status"] == "completed"
    assert client.get(run_path).json()["status"] == "succeeded"
    call_payload = client.get(f"{run_path}/tool-calls").json()[0]
    assert call_payload["status"] == "succeeded"
    assert call_payload["output"]["title"] == "لاختبار الإلغاء"
    assert cancellation["events"][-2]["source_type"] == "reconciliation_worker"
    tasks = client.get(f"/api/v1/workspaces/{workspace_id}/tasks").json()
    assert [task["title"] for task in tasks] == ["لاختبار الإلغاء"]


def test_cancellation_reducer_reports_verified_partial_effect(
    client: TestClient,
    database: Database,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    run_id = UUID(run_path.rsplit("/", 1)[-1])
    first_call = client.get(f"{run_path}/tool-calls").json()[0]
    now = datetime.now(UTC)
    second_step_id = uuid4()
    with database.session_factory.begin() as session:
        session.add_all(
            [
                PlanStepRecord(
                    id=second_step_id,
                    run_id=run_id,
                    position=99,
                    title="أثر ثانٍ يجب منعه",
                    status="pending",
                    risk="low",
                    requires_approval=True,
                    tool_name="tasks",
                    operation="create",
                    tool_input={"title": "الثاني"},
                    created_at=now + timedelta(seconds=1),
                    updated_at=now + timedelta(seconds=1),
                ),
                ToolCallRecord(
                    id=uuid4(),
                    run_id=run_id,
                    plan_step_id=second_step_id,
                    tool_name="tasks",
                    operation="create",
                    input={"title": "الثاني"},
                    output=None,
                    status="pending",
                    risk="low",
                    idempotency_key="b" * 64,
                    error_code=None,
                    attempt_count=0,
                    max_attempts=3,
                    available_at=now + timedelta(seconds=1),
                    lease_owner=None,
                    lease_expires_at=None,
                    started_at=None,
                    effect_committed_at=None,
                    effect_commit_token=None,
                    effect_commit_worker_id=None,
                    completed_at=None,
                    created_at=now + timedelta(seconds=1),
                    updated_at=now + timedelta(seconds=1),
                ),
            ]
        )

    repository = SqlAlchemyToolCallRepository(
        database.session_factory,
        client.app.state.tool_registry,
    )
    leased = repository.claim_next("partial-worker", claimed_at=now, lease_seconds=30)
    assert leased is not None
    assert str(leased.call.id) == first_call["id"]
    token = repository.commit_effect(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        worker_id="partial-worker",
    )
    assert token is not None
    succeeded = repository.succeed(
        leased.owner_id,
        leased.run.workspace_id,
        leased.run.conversation_id,
        leased.call.id,
        _task_output(str(leased.run.workspace_id), "لاختبار الإلغاء", now),
        worker_id="partial-worker",
        expected_idempotency_key=leased.call.idempotency_key,
        expected_input=leased.call.input,
        effect_commit_token=token,
        occurred_at=now + timedelta(milliseconds=2),
    )
    assert succeeded is not None
    assert succeeded.status.value == "executing"

    result = client.post(f"{run_path}/cancellation").json()
    assert result["decision"] == "accepted"
    assert result["cancellation"]["status"] == "partially_succeeded"
    assert result["run_status"] == "partially_succeeded"
    assert [call["status"] for call in client.get(f"{run_path}/tool-calls").json()] == [
        "succeeded",
        "cancelled",
    ]


def test_cancellation_rows_follow_run_lifecycle(
    client: TestClient,
    database: Database,
    monkeypatch,
) -> None:
    run_path, _, _ = _queued_task(client, monkeypatch)
    run_id = UUID(run_path.rsplit("/", 1)[-1])
    assert client.post(f"{run_path}/cancellation").json()["decision"] == "accepted"

    with database.session_factory.begin() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        session.delete(run)

    with database.session_factory() as session:
        assert (
            session.scalar(select(ToolCallRecord.id).where(ToolCallRecord.run_id == run_id)) is None
        )
        assert (
            session.scalar(
                select(RunCancellationRecord.id).where(RunCancellationRecord.run_id == run_id)
            )
            is None
        )
        assert session.scalar(select(RunCancellationEventRecord.id)) is None


def test_completed_run_reports_too_late(client: TestClient) -> None:
    setup = client.post(
        "/api/v1/setup",
        json={"display_name": "مالك", "workspace_name": "مساحة"},
    ).json()
    workspace_id = setup["workspace"]["id"]
    conversation = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "جواب مباشر"},
    ).json()
    base = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation['id']}"
    message = client.post(
        f"{base}/messages",
        json={"parts": [{"type": "text", "text": "أنشئ مهمة مكتملة"}]},
    ).json()
    run = client.post(
        f"{base}/runs",
        json={"request_message_id": message["id"], "autonomy_level": 2},
    ).json()
    run_path = f"{base}/runs/{run['id']}"
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

    result = client.post(f"{run_path}/cancellation")
    assert result.status_code == 200
    assert result.json()["decision"] == "too_late"
    assert client.get(f"{run_path}/cancellation").status_code == 404


def test_cancellation_survives_api_restart(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'cancellation-restart.db').as_posix()}"
    first_database = Database(database_url)
    first_database.create_schema()
    first_app = create_app(
        Settings(
            environment="test",
            model_provider="fake",
            workspace_files_root=tmp_path / "first-workspaces",
        ),
        database=first_database,
    )
    with TestClient(first_app) as first_client:
        run_path, _, _ = _queued_task(first_client, monkeypatch)
        accepted = first_client.post(f"{run_path}/cancellation").json()
        cancellation_id = accepted["cancellation"]["id"]

    second_database = Database(database_url)
    second_app = create_app(
        Settings(
            environment="test",
            model_provider="fake",
            workspace_files_root=tmp_path / "second-workspaces",
        ),
        database=second_database,
    )
    with TestClient(second_app) as second_client:
        restored = second_client.get(f"{run_path}/cancellation")
        assert restored.status_code == 200
        assert restored.json()["id"] == cancellation_id
        assert restored.json()["status"] == "cancelled"
