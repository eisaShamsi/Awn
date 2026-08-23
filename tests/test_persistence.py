from datetime import UTC, datetime

from awn.application.tasks import TaskService
from awn.domain.tasks import TaskCreate, TaskPriority, TaskStatus, TaskUpdate
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.tasks import SqlAlchemyTaskRepository


def test_task_survives_repository_reconstruction(database: Database) -> None:
    first_service = TaskService(SqlAlchemyTaskRepository(database.session_factory))
    created = first_service.create(
        TaskCreate(
            title="مهمة دائمة",
            priority=TaskPriority.HIGH,
            due_at=datetime(2026, 8, 24, 9, tzinfo=UTC),
        )
    )

    second_service = TaskService(SqlAlchemyTaskRepository(database.session_factory))
    restored = second_service.get(created.id)

    assert restored is not None
    assert restored.title == "مهمة دائمة"
    assert restored.priority is TaskPriority.HIGH
    assert restored.due_at == datetime(2026, 8, 24, 9, tzinfo=UTC)

    completed = second_service.update(
        created.id,
        TaskUpdate(status=TaskStatus.COMPLETED),
    )
    assert completed is not None
    assert completed.status is TaskStatus.COMPLETED
