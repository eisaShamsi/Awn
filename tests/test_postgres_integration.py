import os
from uuid import UUID

import pytest
from sqlalchemy import delete

from awn.application.tasks import TaskService
from awn.domain.tasks import TaskCreate, TaskPriority, TaskStatus, TaskUpdate
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.models import TaskRecord
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
