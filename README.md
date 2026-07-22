# mcp-platform-py

Generic, enterprise-grade MCP server infrastructure in Python (`mcp-platform-core`),
plus `weather-mcp`, a keyless reference app that proves the core end to end.

See [CLAUDE.md](CLAUDE.md) for the operational summary and [DESIGN.md](DESIGN.md)
for full rationale. This README will be filled in with usage instructions as
Phase A lands (see CLAUDE.md §10 for the build order).

## Quickstart

```bash
uv sync
uv run pytest
uv run ruff check . && uv run mypy .

# stdio (keyless)
MCP_TRANSPORT=stdio uv run weather-mcp

# HTTP (keyless free tools)
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 \
  MCP_KEYS_FILE=packages/weather_mcp/keys.example.json \
  uv run weather-mcp
```
