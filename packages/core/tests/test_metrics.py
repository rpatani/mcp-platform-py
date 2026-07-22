from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.parser import text_string_to_metric_families

from mcp_platform_core.observability.metrics import (
    NullMetrics,
    OtelMetrics,
    PrometheusMetrics,
    build_metrics,
)


def _sample_value(body: str, metric_name: str, labels: dict[str, str]) -> float | None:
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            if sample.name == metric_name and all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                return sample.value
    return None


def test_null_metrics_is_disabled_and_noop() -> None:
    metrics = NullMetrics()

    metrics.record_tool_call("t", "free", "success", 0.1)
    metrics.record_cache_event("t", "hit")
    metrics.record_retry("t")
    metrics.set_circuit_state("u", 1)

    assert metrics.enabled() is False
    assert metrics.expose() is None


def test_prometheus_metrics_records_tool_call_and_duration() -> None:
    metrics = PrometheusMetrics(registry=CollectorRegistry())
    metrics.record_tool_call("get_forecast", "free", "success", 0.25)

    body, content_type = metrics.expose()
    text = body.decode()

    assert "text/plain" in content_type
    assert (
        _sample_value(
            text,
            "mcp_tool_calls_total",
            {"tool": "get_forecast", "tier": "free", "status": "success"},
        )
        == 1.0
    )
    duration_labels = {"tool": "get_forecast", "tier": "free"}
    assert _sample_value(text, "mcp_tool_call_duration_seconds_count", duration_labels) == 1.0


def test_prometheus_metrics_cache_and_retry_and_circuit_state() -> None:
    metrics = PrometheusMetrics(registry=CollectorRegistry())
    metrics.record_cache_event("geocode_place", "hit")
    metrics.record_retry("get_current_weather")
    metrics.set_circuit_state("open-meteo", 2)

    text = metrics.expose()[0].decode()

    cache_labels = {"tool": "geocode_place", "result": "hit"}
    assert _sample_value(text, "mcp_cache_events_total", cache_labels) == 1.0
    retry_labels = {"tool": "get_current_weather"}
    assert _sample_value(text, "mcp_upstream_retries_total", retry_labels) == 1.0
    assert _sample_value(text, "mcp_circuit_state", {"upstream": "open-meteo"}) == 2.0


def test_prometheus_metrics_enabled() -> None:
    assert PrometheusMetrics(registry=CollectorRegistry()).enabled() is True


def test_build_metrics_disabled_returns_null() -> None:
    assert isinstance(build_metrics("prometheus", enabled=False), NullMetrics)


def test_build_metrics_prometheus_default() -> None:
    assert isinstance(build_metrics("prometheus", enabled=True), PrometheusMetrics)


def test_build_metrics_otel() -> None:
    metrics = build_metrics("otel", enabled=True)
    assert isinstance(metrics, OtelMetrics)
    assert metrics.enabled() is True


def test_otel_metrics_records_and_exposes_same_instruments() -> None:
    pytest.importorskip("opentelemetry.sdk.metrics")
    metrics = OtelMetrics()
    metrics.record_tool_call("get_forecast", "free", "success", 0.25)
    metrics.record_cache_event("geocode_place", "miss")
    metrics.record_retry("get_current_weather")
    metrics.set_circuit_state("open-meteo", 1)

    text = metrics.expose()[0].decode()

    assert _sample_value(text, "mcp_tool_calls_total", {"tool": "get_forecast"}) == 1.0
    assert _sample_value(text, "mcp_circuit_state", {"upstream": "open-meteo"}) == 1.0
