"""Resilience layer: timeout + retry + circuit breaker wrapping upstream calls.

This module currently only defines the ``ResilientCaller`` seam so that
``types.ToolContext`` can reference a real type. The full timeout/retry/
circuit-breaker implementation and its test suite land in build-order Step 5
(CLAUDE.md §10) — do not treat this stub as done.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class ResilientCaller:
    """Injected into every ToolContext; handlers call upstreams through this."""

    async def call(
        self,
        upstream: str,
        fn: Callable[[], Awaitable[Any]],
        *,
        timeout_s: float | None = None,
        retries: int | None = None,
    ) -> Any:
        raise NotImplementedError
