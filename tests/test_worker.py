from dataclasses import replace
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from awn.application.worker import WorkerService
from awn.infrastructure.persistence.tool_calls import SqlAlchemyToolCallRepository
from awn.tools.registry import RetryableToolError, ToolRegistry


def _queued_task(client: TestClient, monkeypatch) -> tuple[str, str]:
    setup = client.post(
        "/api/v1/setup",
        json={"display_name": "مدير عَوْن", "workspace_name": "مساحة العامل"},
    ).json()
    workspace_id = setup["workspace"]["id"]
    conversation = client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "اختبار العامل"},
    ).json()
    conversation_id = conversation["id"]
    base = f"/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}"
    message = client.post(
        f"{base}/messages",
        json={"parts": [{"type": "text", "text": "أنشئ مهمة لاختبار الطابور"}]},
    ).json()
    run = client.post(
        f"{base}/runs",
        json={"request_message_id": message["id"], "autonomy_level": 2},
    ).json()
    run_path = f"{base}/runs/{run['id']}"
    approval = client.get(f"{run_path}/approvals").json()[0]
    monkeypatch.setattr(client.app.state.worker_service, "run_until_idle", lambda: 0)
    response = client.post(
        f"{run_path}/approvals/{approval['id']}/decision",
        json={
            "decision": "approve",
            "action_fingerprint": approval["action_fingerprint"],
        },
    )
    assert response.status_code == 200
    assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "pending"
    return run_path, workspace_id


def test_expired_worker_lease_is_resumed_without_new_attempt(
    client: TestClient,
    database,
    monkeypatch,
) -> None:
    run_path, workspace_id = _queued_task(client, monkeypatch)
    repository = SqlAlchemyToolCallRepository(database.session_factory)
    claimed_at = datetime.now(UTC)

    abandoned = repository.claim_next(
        "stopped-worker",
        claimed_at=claimed_at,
        lease_seconds=1,
    )
    assert abandoned is not None
    assert abandoned.call.attempt_count == 1

    assert client.app.state.worker_service.run_once(now=claimed_at + timedelta(seconds=2)) is True

    call = client.get(f"{run_path}/tool-calls").json()[0]
    assert call["status"] == "succeeded"
    assert call["attempt_count"] == 1
    assert client.get(run_path).json()["status"] == "succeeded"
    stale_result = repository.succeed(
        abandoned.owner_id,
        abandoned.run.workspace_id,
        abandoned.run.conversation_id,
        abandoned.call.id,
        {"unexpected": True},
        worker_id="stopped-worker",
        expected_idempotency_key=abandoned.call.idempotency_key,
        expected_input=abandoned.call.input,
        occurred_at=datetime.now(UTC),
    )
    assert stale_result is None
    tasks = client.get(f"/api/v1/workspaces/{workspace_id}/tasks").json()
    assert [task["title"] for task in tasks] == ["لاختبار الطابور"]


def test_post_boundary_lease_recovery_transfers_evidence_authority(
    client: TestClient,
    database,
    monkeypatch,
) -> None:
    run_path, workspace_id = _queued_task(client, monkeypatch)
    repository = SqlAlchemyToolCallRepository(
        database.session_factory,
        client.app.state.tool_registry,
    )
    claimed_at = datetime.now(UTC)
    original = repository.claim_next(
        "original-worker",
        claimed_at=claimed_at,
        lease_seconds=1,
    )
    assert original is not None
    token = repository.commit_effect(
        original.owner_id,
        original.run.workspace_id,
        original.run.conversation_id,
        original.call.id,
        worker_id="original-worker",
    )
    assert token is not None

    recovered = repository.claim_next(
        "recovery-worker",
        claimed_at=claimed_at + timedelta(seconds=2),
        lease_seconds=30,
    )
    assert recovered is not None
    recovered_token = repository.commit_effect(
        recovered.owner_id,
        recovered.run.workspace_id,
        recovered.run.conversation_id,
        recovered.call.id,
        worker_id="recovery-worker",
    )
    assert recovered_token == token
    output = {
        "id": "44444444-4444-4444-8444-444444444444",
        "workspace_id": workspace_id,
        "title": "لاختبار الطابور",
        "description": None,
        "status": "pending",
        "priority": "normal",
        "due_at": None,
        "created_at": claimed_at.isoformat(),
        "updated_at": claimed_at.isoformat(),
    }
    assert (
        repository.succeed(
            original.owner_id,
            original.run.workspace_id,
            original.run.conversation_id,
            original.call.id,
            output,
            worker_id="original-worker",
            expected_idempotency_key=original.call.idempotency_key,
            expected_input=original.call.input,
            effect_commit_token=token,
            occurred_at=claimed_at + timedelta(seconds=3),
        )
        is None
    )
    assert (
        repository.succeed(
            recovered.owner_id,
            recovered.run.workspace_id,
            recovered.run.conversation_id,
            recovered.call.id,
            output,
            worker_id="recovery-worker",
            expected_idempotency_key=recovered.call.idempotency_key,
            expected_input=recovered.call.input,
            effect_commit_token=recovered_token,
            occurred_at=claimed_at + timedelta(seconds=3),
        )
        is not None
    )
    assert client.get(f"{run_path}/tool-calls").json()[0]["status"] == "succeeded"


def test_transient_failure_retries_only_to_configured_limit(
    client: TestClient,
    database,
    monkeypatch,
) -> None:
    run_path, _ = _queued_task(client, monkeypatch)
    task_definition = client.app.state.tool_registry.resolve("tasks", "create")
    assert task_definition is not None

    def fail_transiently(*_):
        raise RetryableToolError("temporary dependency outage")

    registry = ToolRegistry([replace(task_definition, handler=fail_transiently)])
    worker = WorkerService(
        SqlAlchemyToolCallRepository(database.session_factory),
        client.app.state.conversation_service,
        registry,
        lease_seconds=30,
        worker_id="retry-test-worker",
    )
    first_attempt = datetime.now(UTC)

    assert worker.run_once(now=first_attempt) is True
    assert worker.run_once(now=first_attempt + timedelta(seconds=2)) is True
    assert worker.run_once(now=first_attempt + timedelta(seconds=5)) is True

    call = client.get(f"{run_path}/tool-calls").json()[0]
    assert call["status"] == "failed"
    assert call["attempt_count"] == 3
    assert call["max_attempts"] == 3
    assert client.get(run_path).json()["status"] == "failed"
