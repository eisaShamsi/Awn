"""Validated registry and invocation boundary for Awn tools."""

import re
from collections.abc import Iterable

from pydantic import BaseModel, ValidationError

from awn.tools.contracts import ToolContext, ToolDefinition

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class ToolRegistryError(RuntimeError):
    """Base error raised at the trusted tool boundary."""


class UnknownToolError(ToolRegistryError):
    pass


class InvalidToolInputError(ToolRegistryError):
    pass


class InvalidToolOutputError(ToolRegistryError):
    pass


class RetryableToolError(ToolRegistryError):
    """A transient tool failure that may be retried within the configured limit."""


class ToolRegistry:
    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        self._definitions: dict[tuple[str, str], ToolDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        if not _IDENTIFIER.fullmatch(definition.name):
            raise ValueError("tool names must use lowercase safe identifiers")
        if not _IDENTIFIER.fullmatch(definition.operation):
            raise ValueError("tool operations must use lowercase safe identifiers")
        if definition.timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        key = (definition.name, definition.operation)
        if key in self._definitions:
            raise ValueError(f"duplicate tool operation: {definition.identifier}")
        self._definitions[key] = definition

    def resolve(self, name: str, operation: str) -> ToolDefinition | None:
        return self._definitions.get((name, operation))

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def validate_input(
        self,
        name: str,
        operation: str,
        arguments: dict[str, object],
    ) -> BaseModel:
        definition = self.resolve(name, operation)
        if definition is None:
            raise UnknownToolError(f"unknown tool operation: {name}.{operation}")
        try:
            return definition.input_model.model_validate(arguments)
        except ValidationError as error:
            raise InvalidToolInputError("tool input did not match its schema") from error

    def validate_output(
        self,
        name: str,
        operation: str,
        output: object,
    ) -> BaseModel:
        definition = self.resolve(name, operation)
        if definition is None:
            raise UnknownToolError(f"unknown tool operation: {name}.{operation}")
        try:
            return definition.output_model.model_validate(output)
        except ValidationError as error:
            raise InvalidToolOutputError("tool output did not match its schema") from error

    def execute(
        self,
        name: str,
        operation: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> BaseModel:
        definition = self.resolve(name, operation)
        if definition is None:
            raise UnknownToolError(f"unknown tool operation: {name}.{operation}")
        tool_input = self.validate_input(name, operation, arguments)
        return self.execute_validated(name, operation, tool_input, context)

    def execute_validated(
        self,
        name: str,
        operation: str,
        tool_input: BaseModel,
        context: ToolContext,
    ) -> BaseModel:
        """Execute an input that was validated before the durable effect gate."""

        definition = self.resolve(name, operation)
        if definition is None:
            raise UnknownToolError(f"unknown tool operation: {name}.{operation}")
        if not isinstance(tool_input, definition.input_model):
            raise InvalidToolInputError("validated input does not match the tool contract")
        result = definition.handler(context, tool_input)
        return self.validate_output(name, operation, result)
