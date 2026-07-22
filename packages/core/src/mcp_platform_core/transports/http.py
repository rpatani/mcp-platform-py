"""Transports: run_stdio and run_http (health checks, metrics port, graceful shutdown).

Despite the module name, both transports live here per the repo layout in
CLAUDE.md §3. stdout is reserved for the JSON-RPC wire in stdio mode; structlog
already routes stdio logs to stderr and metrics auto-disable there.
"""

from __future__ import annotations

import contextlib
import signal
from collections.abc import AsyncIterator
from typing import Any

import structlog
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from mcp_platform_core.observability.metrics import Metrics
from mcp_platform_core.server import current_api_key


async def run_stdio(
    server: Server[Any, Any],
    *,
    api_key: str | None,
    log: structlog.BoundLogger,
) -> None:
    """One process per client: bind the single api_key for the whole session."""
    from mcp.server.stdio import stdio_server

    token = current_api_key.set(api_key)
    try:
        async with stdio_server() as (read_stream, write_stream):
            log.info("stdio_transport_started")
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        current_api_key.reset(token)


def _extract_api_key(scope: Scope) -> str | None:
    raw_headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
    headers: dict[str, str] = {k.decode().lower(): v.decode() for k, v in raw_headers}
    auth = headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-api-key")


async def run_http(
    server: Server[Any, Any],
    *,
    host: str = "0.0.0.0",  # noqa: S104  # nosec B104
    port: int = 8080,
    mcp_path: str = "/mcp",
    metrics: Metrics,
    metrics_port: int = 9464,
    log: structlog.BoundLogger,
) -> None:
    """Streamable HTTP transport with /healthz, /readyz, metrics port, graceful drain.

    Binds all interfaces by default (``0.0.0.0``): the intended deployment is a
    container behind an ingress/reverse proxy that terminates TLS.
    """
    session_manager = StreamableHTTPSessionManager(app=server)
    readiness = {"ready": True}

    async def mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
        # The transport's only auth job is extracting the credential; resolving it
        # into an ApiKeyRecord stays the middleware's job (step 1 of the fixed order).
        token = current_api_key.set(_extract_api_key(scope))
        try:
            await session_manager.handle_request(scope, receive, send)
        finally:
            current_api_key.reset(token)

    async def healthz(_request: Request) -> Response:
        return PlainTextResponse("ok")

    async def readyz(_request: Request) -> Response:
        if readiness["ready"]:
            return PlainTextResponse("ready")
        return PlainTextResponse("draining", status_code=503)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            log.info("http_transport_started", port=port, mcp_path=mcp_path)
            yield

    app = Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/readyz", readyz),
            Mount(mcp_path, app=mcp_asgi),
        ],
        lifespan=lifespan,
    )

    main_server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    servers = [main_server]

    if metrics.enabled():
        metrics_app = _build_metrics_app(metrics)
        metrics_server = uvicorn.Server(
            uvicorn.Config(metrics_app, host=host, port=metrics_port, log_level="warning")
        )
        servers.append(metrics_server)

    # Our own drain handler is authoritative. uvicorn captures signals per-server
    # via capture_signals(); with two servers only the last-installed handler would
    # win (and stop only that server). Neutralize it so our single loop handler
    # drains both servers deterministically.
    for srv in servers:
        srv.capture_signals = contextlib.nullcontext  # type: ignore[assignment]

    _install_signal_handlers(readiness, servers, log)

    import asyncio

    await asyncio.gather(*(s.serve() for s in servers))


def _build_metrics_app(metrics: Metrics) -> Starlette:
    async def metrics_endpoint(_request: Request) -> Response:
        exposition = metrics.expose()
        if exposition is None:
            return PlainTextResponse("metrics disabled", status_code=404)
        body, content_type = exposition
        return Response(body, media_type=content_type)

    return Starlette(routes=[Route("/metrics", metrics_endpoint)])


def _install_signal_handlers(
    readiness: dict[str, bool],
    servers: list[uvicorn.Server],
    log: structlog.BoundLogger,
) -> None:
    import asyncio

    def _drain() -> None:
        # Flip readiness to 503 first so a load balancer drains us, then let
        # uvicorn finish in-flight requests before exiting. Idempotent: a second
        # signal while draining is a no-op.
        if not readiness["ready"]:
            return
        log.info("graceful_shutdown_initiated")
        readiness["ready"] = False
        for server in servers:
            server.should_exit = True

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _drain)
