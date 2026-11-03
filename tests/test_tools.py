import asyncio
from uuid import uuid4

import pytest

from steal_her_heart.schemas import ToolContext
from steal_her_heart.tools import HelloOutput, HelloTool, ToolRegistry


def invoke(registry, name="hello", args=None, caps=frozenset({"diagnostic"}), timeout=1.0):
    return asyncio.run(
        registry.invoke(
            name,
            args or {},
            ToolContext(
                task_id=uuid4(),
                capabilities=caps,
                timeout_s=timeout,
            ),
        )
    )


def test_registry_validates_capabilities_arguments_and_duplicate_names():
    registry = ToolRegistry()
    registry.register(HelloTool())
    with pytest.raises(ValueError):
        registry.register(HelloTool())
    assert invoke(registry).data == {"message": "Hello, Agent!"}
    assert invoke(registry, caps=frozenset()).error.code == "PERMISSION_DENIED"
    assert invoke(registry, name="web_search").error.code == "UNKNOWN_TOOL"
    assert invoke(registry, args={"invented": "x"}).error.code == "INVALID_ARGUMENT"
    assert registry.describe()[0].input_schema["additionalProperties"] is False


def test_plugin_output_is_validated_even_if_model_construct_bypassed_schema():
    class BrokenHello(HelloTool):
        async def run(self, arguments, context):
            return HelloOutput.model_construct(message=42)

    registry = ToolRegistry()
    registry.register(BrokenHello())
    assert invoke(registry).error.code == "TOOL_PROTOCOL_ERROR"


def test_timeout_is_bounded_and_exceptions_do_not_leak_secrets():
    class SlowHello(HelloTool):
        async def run(self, arguments, context):
            await asyncio.sleep(1)
            return HelloOutput(message="late")

    registry = ToolRegistry()
    registry.register(SlowHello())
    assert invoke(registry, timeout=0.01).error.code == "TOOL_TIMEOUT"

    class BadHello(HelloTool):
        async def run(self, arguments, context):
            raise RuntimeError("fake-secret-123")

    registry = ToolRegistry()
    registry.register(BadHello())
    result = invoke(registry)
    assert result.error.code == "TOOL_EXECUTION_ERROR"
    assert "fake-secret" not in result.model_dump_json()
