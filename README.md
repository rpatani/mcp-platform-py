# mcp-platform-py

A generic, enterprise-grade **MCP server infrastructure in Python**
(`mcp-platform-core`) plus **`weather-mcp`**, a keyless reference app that
exercises the whole platform end to end with **zero secrets**.

The core gives any MCP server, for free: pluggable API-key auth with tiered
access (free / premium / enterprise), per-key rate limiting, per-tool TTL
caching, per-call cost/usage events, a resilience layer (timeouts, bounded
retries, circuit breakers), structured JSON logs, Prometheus/OTel metrics, and
stdio + Streamable HTTP transports with health checks and graceful shutdown.

See [CLAUDE.md](CLAUDE.md) for the operational summary and [DESIGN.md](DESIGN.md)
for the full rationale.

## Layout

```
packages/
  core/         → published as mcp-platform-core (the reusable framework)
  weather_mcp/  → in-repo reference app (Open-Meteo free tools + one premium tool)
deploy/         → docker-compose, prometheus.yml, smoke-test.sh
```

Dependency direction is one-way: `app → core`. The three other apps
(finance / github / ebay) live in their own repos and pin core by git tag.

## Apps built on this core (Phase B)

Three sibling repos consume `mcp-platform-core` as an external, tag-pinned
library — each proving a different auth style:

| Repo | Auth style | Tools |
|---|---|---|
| `finance-mcp` | server-side API key | CoinGecko + Frankfurter (free), Alpha Vantage (premium) |
| `github-mcp` | static token + writes | repo/issue reads (free), issue writes (premium, never cached) |
| `ebay-mcp` | OAuth 2 client-credentials | eBay Browse API (free + premium) |

**[MANUAL-TESTING.md](MANUAL-TESTING.md)** is the step-by-step macOS guide for
running and exercising all four servers locally, including the git-tag core
dependency and the `file://` → GitHub URL switch when you push.

## Quickstart

```bash
uv sync                                  # install workspace + lock
uv run pytest                            # tests (core middleware + resilience, weather lib)
uv run ruff check . && uv run mypy .     # lint + types
uv run pip-audit && uv run bandit -r packages/core/src packages/weather_mcp/src   # security gates
```

### Run the weather server

```bash
# stdio (keyless) — for Claude Desktop / CLI MCP clients
MCP_TRANSPORT=stdio uv run weather-mcp

# HTTP (keyless free tools; tiers enabled by the keys file)
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 \
  MCP_KEYS_FILE=packages/weather_mcp/keys.example.json \
  uv run weather-mcp
```

- MCP endpoint: `POST http://localhost:8080/mcp`
- Liveness / readiness: `GET /healthz`, `GET /readyz`
- Metrics (separate port, not gated by MCP auth): `http://localhost:9464/metrics`

The premium tool (`get_weather_premium`) additionally needs
`OPENWEATHERMAP_API_KEY`; the three free tools need no secrets.

### Demo stack + smoke test

```bash
docker compose -f deploy/docker-compose.yml up --build     # weather-mcp + Prometheus, no secrets
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464
```

The smoke test drives a full MCP session (initialize → tools/list → call a free
tool) and asserts the metrics counter incremented.

## Tools (reference app)

| Tool | Provider | Tier | Cache TTL | Cost |
|---|---|---|---|---|
| `geocode_place` | Open-Meteo Geocoding | free | 24 h | 1 |
| `get_current_weather` | Open-Meteo | free | 5 min | 1 |
| `get_forecast` | Open-Meteo | free | 1 h | 1 |
| `get_weather_premium` | OpenWeatherMap One Call | premium | 5 min | 3 |

## Configuration

All behavior is env-driven (`CoreConfig`); see [CLAUDE.md §8](CLAUDE.md) for the
full table (`MCP_TRANSPORT`, `MCP_HTTP_PORT`, `MCP_KEYS_FILE`, `MCP_METRICS_*`,
`MCP_UPSTREAM_*`, `MCP_BREAKER_*`, …) and `packages/weather_mcp/.env.example`.

## Building an app on the core

An app registers `ToolDefinition`s and lets the platform do the rest:

```python
from mcp_platform_core import ToolDefinition, ToolContext
from pydantic import BaseModel

class MyInput(BaseModel):
    query: str

async def handler(args: MyInput, ctx: ToolContext) -> dict:
    # handlers are pure API clients — auth/tier/cache/retry are the platform's job
    return await ctx.resilient.call("my-upstream", lambda: client.get(...))

tool = ToolDefinition(
    name="my_tool", description="…", input_model=MyInput,
    min_tier="free", cost_units=1, cache_ttl_ms=60_000, handler=handler,
)
```

`packages/weather_mcp` is the worked example. Everything exported from
`mcp_platform_core/__init__.py` is a SemVer-versioned public contract.
