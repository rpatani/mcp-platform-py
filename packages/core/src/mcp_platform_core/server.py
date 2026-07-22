"""build_mcp_server: wire a ToolRegistry into a low-level MCP Server.

Built on the low-level ``mcp.server.lowlevel.Server`` rather than FastMCP's
decorator sugar so each tool's ``input_model`` maps to a *flat* top-level JSON
Schema (FastMCP would nest a single Pydantic-model argument under a wrapper
key). Input validation, tier/rate/cache/resilience, metrics and usage all live
in the middleware executor; this module is only the protocol adapter.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server

from mcp_platform_core.middleware import MiddlewareDeps, ToolExecutor, build_tool_executor
from mcp_platform_core.registry import ToolRegistry

# Set by the active transport (stdio: once at startup; http: per request from the
# Authorization/x-api-key header) and read when a tool is invoked.
current_api_key: ContextVar[str | None] = ContextVar("current_api_key", default=None)


def build_mcp_server(
    *,
    name: str,
    version: str,
    registry: ToolRegistry,
    deps: MiddlewareDeps,
) -> Server[Any, Any]:
    server: Server[Any, Any] = Server(name, version=version)
    executors: dict[str, ToolExecutor] = {
        tool.name: build_tool_executor(tool, deps) for tool in registry.list()
    }

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]  # mcp SDK decorator
    async def _list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_model.model_json_schema(),
            )
            for tool in registry.list()
        ]

    # validate_input=False: the middleware executor validates via the tool's
    # Pydantic model at the correct point in the fixed order (after tier/cache/
    # rate checks), so the protocol layer must not pre-empt it.
    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]  # mcp SDK decorator
    async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        executor = executors.get(name)
        if executor is None:
            raise ValueError(f"unknown tool: {name}")
        result = await executor(arguments, current_api_key.get())
        return result if isinstance(result, dict) else {"result": result}

    return server
