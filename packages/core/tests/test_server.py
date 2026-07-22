"""End-to-end wiring test: drive a built server through an in-memory client session."""

from __future__ import annotations

import json

import structlog
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel

from mcp_platform_core.middleware import (
    InMemoryKeyStore,
    InMemoryRateLimiter,
    InMemoryResponseCache,
    LoggingUsageSink,
    MiddlewareDeps,
)
from mcp_platform_core.observability.metrics import NullMetrics
from mcp_platform_core.registry import ToolRegistry
from mcp_platform_core.resilience import ResilientCaller
from mcp_platform_core.server import build_mcp_server
from mcp_platform_core.types import ToolContext, ToolDefinition


class AddInput(BaseModel):
    a: int
    b: int


async def _add(args: AddInput, ctx: ToolContext) -> dict[str, int]:
    return {"sum": args.a + args.b}


def _deps() -> MiddlewareDeps:
    return MiddlewareDeps(
        key_store=InMemoryKeyStore(),
        rate_limiter=InMemoryRateLimiter(),
        cache=InMemoryResponseCache(),
        usage_sink=LoggingUsageSink(),
        metrics=NullMetrics(),
        logger=structlog.get_logger(),
        resilient=ResilientCaller(),
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="add",
            description="Add two integers.",
            input_model=AddInput,
            handler=_add,
        )
    )
    return registry


async def test_list_tools_exposes_flat_input_schema() -> None:
    server = build_mcp_server(name="test", version="0.1.0", registry=_registry(), deps=_deps())

    async with create_connected_server_and_client_session(server) as client:
        result = await client.list_tools()

    tools = {t.name: t for t in result.tools}
    assert "add" in tools
    schema = tools["add"].inputSchema
    # Flat top-level fields (the reason we build on the low-level Server, not FastMCP sugar).
    assert set(schema["properties"]) == {"a", "b"}
    assert tools["add"].description == "Add two integers."


async def test_call_tool_runs_through_executor() -> None:
    server = build_mcp_server(name="test", version="0.1.0", registry=_registry(), deps=_deps())

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})

    assert result.isError is False
    assert result.structuredContent == {"sum": 5}
    assert json.loads(result.content[0].text) == {"sum": 5}


async def test_call_unknown_tool_returns_error() -> None:
    server = build_mcp_server(name="test", version="0.1.0", registry=_registry(), deps=_deps())

    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("nonexistent", {})

    assert result.isError is True
