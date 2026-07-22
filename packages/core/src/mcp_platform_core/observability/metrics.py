"""Pluggable Metrics interface — call sites never touch a backend directly (CLAUDE.md §6).

Two backends ship: ``PrometheusMetrics`` (default) and ``OtelMetrics`` (behind
the ``mcp-platform-core[otel]`` extra), selected via ``MCP_METRICS_BACKEND``.
``NullMetrics`` implements "metrics auto-disable for stdio" as a backend swap
rather than scattered branching.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, Protocol

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

if TYPE_CHECKING:
    from opentelemetry.metrics import CallbackOptions, Observation


class Metrics(Protocol):
    def record_tool_call(self, tool: str, tier: str, status: str, duration_s: float) -> None: ...

    def record_cache_event(self, tool: str, result: str) -> None: ...

    def record_retry(self, tool: str) -> None: ...

    def set_circuit_state(self, upstream: str, state: int) -> None: ...

    def enabled(self) -> bool: ...

    def expose(self) -> tuple[bytes, str] | None: ...


class NullMetrics:
    """No-op Metrics — used whenever metrics are disabled (stdio, by default)."""

    def record_tool_call(self, tool: str, tier: str, status: str, duration_s: float) -> None:
        pass

    def record_cache_event(self, tool: str, result: str) -> None:
        pass

    def record_retry(self, tool: str) -> None:
        pass

    def set_circuit_state(self, upstream: str, state: int) -> None:
        pass

    def enabled(self) -> bool:
        return False

    def expose(self) -> tuple[bytes, str] | None:
        return None


class PrometheusMetrics:
    """Default backend — the five instruments in DESIGN.md §8, on a dedicated registry."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry if registry is not None else CollectorRegistry()
        self._tool_calls = Counter(
            "mcp_tool_calls_total",
            "Total tool calls",
            ["tool", "tier", "status"],
            registry=self._registry,
        )
        self._call_duration = Histogram(
            "mcp_tool_call_duration_seconds",
            "Tool call duration in seconds",
            ["tool", "tier"],
            registry=self._registry,
        )
        self._cache_events = Counter(
            "mcp_cache_events_total",
            "Cache hit/miss events",
            ["tool", "result"],
            registry=self._registry,
        )
        self._upstream_retries = Counter(
            "mcp_upstream_retries_total",
            "Upstream call retries",
            ["tool"],
            registry=self._registry,
        )
        self._circuit_state = Gauge(
            "mcp_circuit_state",
            "Circuit breaker state (0=closed,1=open,2=half-open)",
            ["upstream"],
            registry=self._registry,
        )

    def record_tool_call(self, tool: str, tier: str, status: str, duration_s: float) -> None:
        self._tool_calls.labels(tool=tool, tier=tier, status=status).inc()
        self._call_duration.labels(tool=tool, tier=tier).observe(duration_s)

    def record_cache_event(self, tool: str, result: str) -> None:
        self._cache_events.labels(tool=tool, result=result).inc()

    def record_retry(self, tool: str) -> None:
        self._upstream_retries.labels(tool=tool).inc()

    def set_circuit_state(self, upstream: str, state: int) -> None:
        self._circuit_state.labels(upstream=upstream).set(state)

    def enabled(self) -> bool:
        return True

    def expose(self) -> tuple[bytes, str]:
        return generate_latest(self._registry), CONTENT_TYPE_LATEST


class OtelMetrics:
    """OpenTelemetry backend — requires the ``mcp-platform-core[otel]`` extra.

    Uses opentelemetry-exporter-prometheus so ``:9464/metrics`` behaves the
    same regardless of backend choice (full OTLP-collector wiring is future
    work; tracing stays out of scope for Phase A).
    """

    def __init__(self) -> None:
        try:
            from opentelemetry.exporter.prometheus import PrometheusMetricReader
            from opentelemetry.sdk.metrics import MeterProvider
        except ImportError as exc:
            raise ImportError(
                "OtelMetrics requires the 'otel' extra: install mcp-platform-core[otel]"
            ) from exc

        self._registry = CollectorRegistry()
        reader = PrometheusMetricReader(registry=self._registry)
        provider = MeterProvider(metric_readers=[reader])
        meter = provider.get_meter("mcp_platform_core")

        self._tool_calls = meter.create_counter(
            "mcp_tool_calls_total", description="Total tool calls"
        )
        self._call_duration = meter.create_histogram(
            "mcp_tool_call_duration_seconds", unit="s", description="Tool call duration in seconds"
        )
        self._cache_events = meter.create_counter(
            "mcp_cache_events_total", description="Cache hit/miss events"
        )
        self._upstream_retries = meter.create_counter(
            "mcp_upstream_retries_total", description="Upstream call retries"
        )
        self._circuit_states: dict[str, int] = {}
        meter.create_observable_gauge(
            "mcp_circuit_state",
            callbacks=[self._observe_circuit_state],
            description="Circuit breaker state (0=closed,1=open,2=half-open)",
        )

    def _observe_circuit_state(self, options: CallbackOptions) -> Iterable[Observation]:
        from opentelemetry.metrics import Observation

        return [
            Observation(state, {"upstream": upstream})
            for upstream, state in self._circuit_states.items()
        ]

    def record_tool_call(self, tool: str, tier: str, status: str, duration_s: float) -> None:
        attrs = {"tool": tool, "tier": tier, "status": status}
        self._tool_calls.add(1, attrs)
        self._call_duration.record(duration_s, {"tool": tool, "tier": tier})

    def record_cache_event(self, tool: str, result: str) -> None:
        self._cache_events.add(1, {"tool": tool, "result": result})

    def record_retry(self, tool: str) -> None:
        self._upstream_retries.add(1, {"tool": tool})

    def set_circuit_state(self, upstream: str, state: int) -> None:
        self._circuit_states[upstream] = state

    def enabled(self) -> bool:
        return True

    def expose(self) -> tuple[bytes, str]:
        return generate_latest(self._registry), CONTENT_TYPE_LATEST


def build_metrics(
    backend: Literal["prometheus", "otel"] = "prometheus", *, enabled: bool = True
) -> Metrics:
    if not enabled:
        return NullMetrics()
    if backend == "otel":
        return OtelMetrics()
    return PrometheusMetrics()
