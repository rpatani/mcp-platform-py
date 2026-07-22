"""ToolRegistry — the in-process catalog of ToolDefinitions a server exposes."""

from __future__ import annotations

from collections.abc import Iterable

from mcp_platform_core.types import ToolDefinition


class DuplicateToolError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"tool already registered: {name!r}")
        self.name = name


class ToolNotFoundError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"no such tool: {name!r}")
        self.name = name


class ToolRegistry:
    """Ordered, name-keyed collection of ToolDefinitions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise DuplicateToolError(tool.name)
        self._tools[tool.name] = tool

    def register_all(self, tools: Iterable[ToolDefinition]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(name) from None

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
