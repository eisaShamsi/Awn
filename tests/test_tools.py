from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from awn.tools.contracts import EffectVerificationStatus, ToolContext
from awn.tools.registry import (
    InvalidToolInputError,
    InvalidToolOutputError,
    ToolRegistry,
    UnknownToolError,
)


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


def test_registry_rejects_invalid_tool_output(client: TestClient) -> None:
    registry: ToolRegistry = client.app.state.tool_registry

    with pytest.raises(InvalidToolOutputError):
        registry.validate_output("tasks", "create", {"verified": True})


def test_registered_effect_verifiers_read_resources_without_repeating_effect(
    client: TestClient,
) -> None:
    setup = client.post(
        "/api/v1/setup",
        json={"display_name": "مالك الأدلة", "workspace_name": "مساحة الأدلة"},
    ).json()
    workspace_id = UUID(setup["workspace"]["id"])
    registry: ToolRegistry = client.app.state.tool_registry
    call_id = uuid4()
    context = ToolContext(
        owner_id=UUID(setup["user"]["id"]),
        workspace_id=workspace_id,
        conversation_id=uuid4(),
        run_id=uuid4(),
        trace_id=uuid4(),
        tool_call_id=call_id,
        idempotency_key="a" * 64,
    )

    file_definition = registry.resolve("files", "create")
    assert file_definition is not None
    assert file_definition.effect_verifier is not None
    file_input = registry.validate_input(
        "files",
        "create",
        {"path": "evidence/proof.txt", "content": "verified"},
    )
    before = file_definition.effect_verifier(context, file_input)
    assert before.status is EffectVerificationStatus.UNKNOWN
    registry.execute_validated("files", "create", file_input, context)
    after = file_definition.effect_verifier(context, file_input)
    assert after.status is EffectVerificationStatus.EFFECT_PRESENT
    assert after.output is not None

    target = client.app.state.workspace_files.root / str(workspace_id) / "evidence/proof.txt"
    target.unlink()
    after_deletion = file_definition.effect_verifier(context, file_input)
    assert after_deletion.status is EffectVerificationStatus.EFFECT_PRESENT
    assert after_deletion.output == after.output
