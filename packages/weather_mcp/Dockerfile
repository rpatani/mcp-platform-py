# Multi-stage build. Build context MUST be the repo root so packages/core is visible:
#   docker build -f packages/weather_mcp/Dockerfile -t weather-mcp .
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Copy the workspace manifests + sources needed to build both members.
COPY pyproject.toml uv.lock ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/weather_mcp/pyproject.toml packages/weather_mcp/pyproject.toml
COPY packages/core/src packages/core/src
COPY packages/weather_mcp/src packages/weather_mcp/src

# Install into a self-contained venv at /app/.venv (no dev deps).
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.13-slim AS runtime

# Non-root user; read-only-root-fs compatible.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/packages /app/packages

ENV PATH="/app/.venv/bin:$PATH" \
    MCP_TRANSPORT=http \
    MCP_HTTP_PORT=8080 \
    MCP_METRICS_PORT=9464

USER appuser
EXPOSE 8080 9464

ENTRYPOINT ["weather-mcp"]
