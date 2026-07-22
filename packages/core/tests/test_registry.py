from __future__ import annotations

import pytest
from pydantic import BaseModel

from mcp_platform_core.registry import DuplicateToolError, ToolNotFoundError, ToolRegistry
from mcp_platform_core.types import ToolContext, ToolDefinition


class _Input(BaseModel):
    value: int


async def _handler(args: _Input, ctx: ToolContext) -> int:
    return args.value


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="test tool",
        input_model=_Input,
        handler=_handler,
    )


def test_register_and_get() -> None:
    registry = ToolRegistry()
    registry.register(_tool("a"))

    assert registry.get("a").name == "a"
    assert "a" in registry
    assert len(registry) == 1


def test_register_duplicate_raises() -> None:
    registry = ToolRegistry()
    registry.register(_tool("a"))

    with pytest.raises(DuplicateToolError):
        registry.register(_tool("a"))


def test_get_missing_raises() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError):
        registry.get("missing")


def test_register_all_preserves_insertion_order() -> None:
    registry = ToolRegistry()
    registry.register_all([_tool("a"), _tool("b"), _tool("c")])

    assert [t.name for t in registry.list()] == ["a", "b", "c"]
