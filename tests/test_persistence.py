from datetime import UTC, datetime

from awn.application.identity import IdentityService
from awn.application.tasks import TaskService
from awn.domain.identity import SetupCommand
from awn.domain.tasks import TaskCreate, TaskPriority, TaskStatus, TaskUpdate
from awn.infrastructure.database import Database
from awn.infrastructure.persistence.identity import SqlAlchemyIdentityRepository
from awn.infrastructure.persistence.tasks import SqlAlchemyTaskRepository


def test_task_survives_repository_reconstruction(database: Database) -> None:
    identity_repository = SqlAlchemyIdentityRepository(database.session_factory)
    setup = IdentityService(identity_repository).bootstrap(
        SetupCommand(display_name="مستخدم دائم", workspace_name="مساحة دائمة")
    )
    first_service = TaskService(
        SqlAlchemyTaskRepository(database.session_factory),
        identity_repository,
    )
    created = first_service.create(
        setup.workspace.id,
        TaskCreate(
            title="مهمة دائمة",
            priority=TaskPriority.HIGH,
            due_at=datetime(2026, 8, 24, 9, tzinfo=UTC),
        ),
    )

    assert created is not None
    second_service = TaskService(
        SqlAlchemyTaskRepository(database.session_factory),
        identity_repository,
    )
    restored = second_service.get(setup.workspace.id, created.id)

    assert restored is not None
    assert restored.title == "مهمة دائمة"
    assert restored.priority is TaskPriority.HIGH
    assert restored.due_at == datetime(2026, 8, 24, 9, tzinfo=UTC)

    completed = second_service.update(
        setup.workspace.id,
        created.id,
        TaskUpdate(status=TaskStatus.COMPLETED),
    )
    assert completed is not None
    assert completed.status is TaskStatus.COMPLETED
