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
    assert {"alembic_version", "tasks"}.issubset(inspector.get_table_names())
    assert {"ix_tasks_status", "ix_tasks_due_at"} == {
        index["name"] for index in inspector.get_indexes("tasks")
    }

    command.downgrade(config, "base")

    assert "tasks" not in inspect(engine).get_table_names()
    engine.dispose()
