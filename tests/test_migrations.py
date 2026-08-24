from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_migration_upgrades_and_downgrades(tmp_path) -> None:
    database_file = tmp_path / "migration.db"
    database_url = f"sqlite+pysqlite:///{database_file.as_posix()}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url

    command.upgrade(config, "head")
    command.check(config)

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert {
        "alembic_version",
        "approvals",
        "conversations",
        "messages",
        "plan_steps",
        "runs",
        "run_cancellations",
        "run_cancellation_events",
        "tasks",
        "tool_calls",
        "users",
        "workspaces",
    }.issubset(inspector.get_table_names())
    assert {"ix_tasks_status", "ix_tasks_due_at", "ix_tasks_workspace_status"} == {
        index["name"] for index in inspector.get_indexes("tasks")
    }
    assert {"ix_runs_workspace_status", "ix_runs_conversation_created"} == {
        index["name"] for index in inspector.get_indexes("runs")
    }
    assert {"ix_approvals_expires_at", "ix_approvals_run_status"} == {
        index["name"] for index in inspector.get_indexes("approvals")
    }
    assert {"ix_tool_calls_queue", "ix_tool_calls_run_status"} == {
        index["name"] for index in inspector.get_indexes("tool_calls")
    }
    assert {
        "effect_committed_at",
        "effect_commit_token",
        "effect_commit_worker_id",
    }.issubset(
        {column["name"] for column in inspector.get_columns("tool_calls")}
    )
    assert {"ix_run_cancellations_status"} == {
        index["name"] for index in inspector.get_indexes("run_cancellations")
    }
    assert {"ix_run_cancellation_events_cancellation"} == {
        index["name"] for index in inspector.get_indexes("run_cancellation_events")
    }
    assert "sequence_no" in {
        column["name"] for column in inspector.get_columns("run_cancellation_events")
    }
    assert "uq_run_cancellation_events_sequence" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("run_cancellation_events")
    }
    assert "ck_run_cancellation_events_evidence_code" in {
        constraint["name"]
        for constraint in inspector.get_check_constraints("run_cancellation_events")
    }

    command.downgrade(config, "base")

    assert "tasks" not in inspect(engine).get_table_names()
    engine.dispose()
