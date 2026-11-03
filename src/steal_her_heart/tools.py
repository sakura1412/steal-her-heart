"""Explicit, schema-checked tools. Production web tools are not implemented yet."""

import asyncio
import re
from abc import ABC, abstractmethod

from pydantic import ValidationError

from .schemas import Contract, Identifier, Text, ToolContext, ToolError, ToolResult


class Tool(ABC):
    name: str
    description: str
    input_model: type[Contract]
    output_model: type[Contract]
    required_capabilities: frozenset[str] = frozenset()

    @abstractmethod
    async def run(self, arguments: Contract, context: ToolContext) -> Contract:
        """Execute a trusted plugin using validated arguments."""


class ToolDescription(Contract):
    name: Identifier
    description: Text
    input_schema: dict[str, object]
    output_schema: dict[str, object]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool.name):
            raise ValueError("Invalid tool name")
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        for model in (tool.input_model, tool.output_model):
            if not issubclass(model, Contract) or model.model_config.get("extra") != "forbid":
                raise ValueError("Tool boundaries must use strict Contract schemas")
            model.model_json_schema()
        self._tools[tool.name] = tool

    def describe(self) -> tuple[ToolDescription, ...]:
        return tuple(
            ToolDescription(
                name=t.name,
                description=t.description,
                input_schema=t.input_model.model_json_schema(),
                output_schema=t.output_model.model_json_schema(),
            )
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        )

    async def invoke(
        self, name: str, arguments: dict[str, object], context: ToolContext
    ) -> ToolResult:
        def failure(code: str, message: str) -> ToolResult:
            return ToolResult(
                tool=name, status="error", error=ToolError(code=code, message=message)
            )

        tool = self._tools.get(name)
        if tool is None:
            return failure("UNKNOWN_TOOL", "Tool is not registered")
        if not tool.required_capabilities.issubset(context.capabilities):
            return failure("PERMISSION_DENIED", "Tool capability is not authorized")
        try:
            parsed = tool.input_model.model_validate(arguments)
        except ValidationError:
            return failure("INVALID_ARGUMENT", "Tool arguments do not match the input schema")
        try:
            raw = await asyncio.wait_for(tool.run(parsed, context), timeout=context.timeout_s)
        except asyncio.TimeoutError:
            return failure("TOOL_TIMEOUT", "Tool deadline exceeded; execution may be incomplete")
        except Exception:
            return failure("TOOL_EXECUTION_ERROR", "Tool failed; raw exception is not exposed")
        try:
            payload = raw.model_dump(warnings=False) if isinstance(raw, Contract) else raw
            result = tool.output_model.model_validate(payload)
        except (ValidationError, TypeError):
            return failure("TOOL_PROTOCOL_ERROR", "Tool result does not match the output schema")
        return ToolResult(tool=name, status="ok", data=result.model_dump(mode="json"))


class HelloInput(Contract):
    name: Text = "Agent"


class HelloOutput(Contract):
    message: Text


class HelloTool(Tool):
    name = "hello"
    description = "Offline diagnostic tool; performs no research or external calls"
    input_model = HelloInput
    output_model = HelloOutput
    required_capabilities = frozenset({"diagnostic"})

    async def run(self, arguments: HelloInput, context: ToolContext) -> HelloOutput:
        return HelloOutput(message=f"Hello, {arguments.name}!")
