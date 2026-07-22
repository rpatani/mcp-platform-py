from __future__ import annotations

import json

import pytest

from mcp_platform_core.observability.logger import create_logger


def test_logger_binds_service_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    log = create_logger(service="test-svc", version="1.2.3", transport="http", level="info")
    log.info("hello")

    line = json.loads(capsys.readouterr().out)
    assert line["service"] == "test-svc"
    assert line["version"] == "1.2.3"
    assert line["event"] == "hello"


def test_logger_redacts_known_secret_keys(capsys: pytest.CaptureFixture[str]) -> None:
    log = create_logger(service="svc", version="1", transport="http", level="info")
    log.info("upstream call", api_key="super-secret", authorization="Bearer xyz", safe="ok")

    line = json.loads(capsys.readouterr().out)
    assert line["api_key"] == "***REDACTED***"
    assert line["authorization"] == "***REDACTED***"
    assert line["safe"] == "ok"


def test_stdio_transport_routes_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    log = create_logger(service="svc", version="1", transport="stdio", level="info")
    log.info("stdio event")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["event"] == "stdio event"


def test_http_transport_routes_to_stdout_not_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    log = create_logger(service="svc", version="1", transport="http", level="info")
    log.info("http event")

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["event"] == "http event"


def test_level_filters_below_threshold(capsys: pytest.CaptureFixture[str]) -> None:
    log = create_logger(service="svc", version="1", transport="http", level="warning")
    log.info("should be filtered")
    log.warning("should appear")

    captured = capsys.readouterr()
    assert "should be filtered" not in captured.out
    assert json.loads(captured.out)["event"] == "should appear"
