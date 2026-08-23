import pytest
from fastapi.testclient import TestClient

from awn.tools.registry import InvalidToolInputError, ToolRegistry, UnknownToolError


def test_registry_exposes_only_registered_internal_operations(client: TestClient) -> None:
    registry: ToolRegistry = client.app.state.tool_registry

    assert [definition.identifier for definition in registry.definitions()] == [
        "tasks.create",
        "files.create",
    ]
    definition = registry.resolve("tasks", "create")
    assert definition is not None
    assert definition.side_effect is True
    assert definition.external is False
    assert definition.reversible is True
    assert definition.supports_idempotency is True
    assert definition.required_scopes == ("tasks.write",)

    file_definition = registry.resolve("files", "create")
    assert file_definition is not None
    assert file_definition.external is False
    assert file_definition.reversible is True
    assert file_definition.supports_idempotency is True
    assert file_definition.required_scopes == ("files.write",)

    with pytest.raises(ValueError, match="duplicate tool operation"):
        registry.register(definition)


def test_registry_rejects_unknown_or_invalid_tool_input(client: TestClient) -> None:
    registry: ToolRegistry = client.app.state.tool_registry

    with pytest.raises(UnknownToolError):
        registry.validate_input("mail", "send", {"title": "غير مسجل"})
    with pytest.raises(InvalidToolInputError):
        registry.validate_input("tasks", "create", {"title": "   "})
    with pytest.raises(InvalidToolInputError):
        registry.validate_input("tasks", "create", {"title": "مهمة", "unsafe": True})

    unsafe_paths = ("../secret.txt", "/absolute.txt", "C:/secret.txt", "dir\\secret.txt")
    for path in unsafe_paths:
        with pytest.raises(InvalidToolInputError):
            registry.validate_input(
                "files",
                "create",
                {"path": path, "content": "بيانات"},
            )
