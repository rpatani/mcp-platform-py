# CLAUDE.md — MCP Platform (Python)

Authoritative context for this repo. Read it fully before making changes.
The complete rationale lives in `DESIGN.md`; this file is the operational summary
Claude Code should treat as ground truth. Where they ever disagree, `DESIGN.md`
section numbers are cited — follow them.

---

## 1. What we are building

A **generic, enterprise-grade MCP server infrastructure in Python** (`mcp-platform-core`),
plus four concrete MCP server apps that prove the core is domain-agnostic:

- **weather** — built-in reference/test app, lives *in this repo*. Chosen because
  its free tier (Open-Meteo) is **keyless**, so CI / smoke tests / the compose
  demo run with **zero secrets**.
- **finance**, **github**, **ebay** — each lives in its **own separate repo** and
  depends on `mcp-platform-core` as a published library (git-tag dependency to
  start). Not built in this repo.

This is a Python port of an existing TypeScript platform. Keep functional parity
with it and add one new capability the TS version lacks: a **resilience layer**
(timeouts, retries, circuit breakers). See `DESIGN.md` §2 for the full
mirror/diverge table.

---

## 2. Resolved decisions (do not re-litigate)

| Decision | Choice |
|---|---|
| Language / runtime | Python **3.11+**, asyncio |
| MCP framework | official `mcp` SDK (FastMCP); transports **stdio + Streamable HTTP** |
| Schemas / validation | `pydantic` v2 (tool input models) |
| Config | `pydantic-settings` (env → file → defaults) |
| HTTP client | `httpx.AsyncClient` (shared, pooled) |
| Logging | `structlog` (JSON; stdout for HTTP, stderr for stdio) |
| Metrics | **pluggable** behind one `Metrics` interface: `prometheus-client` default + optional `[otel]` adapter; `MCP_METRICS_BACKEND=prometheus\|otel` |
| HTTP side-app | `starlette` + `uvicorn` for `/healthz`, `/readyz` |
| Resilience | `tenacity` + in-house circuit breaker (~40 LOC) |
| Tests | `pytest`, `pytest-asyncio`, `respx` (mock httpx) |
| Build tool | **uv** (workspace in this repo; uv in each app repo) |
| Core distribution | **git-tag dependency** first (`mcp-platform-core @ git+…@vX`) → private index later |
| Container registry | **GHCR** |
| Deployment | container-first; runs on serverless containers (Cloud Run/App Runner/ACA/Fargate), k8s, or bare VM. **True FaaS/Lambda is out of scope now.** |
| Repo scope (this repo) | core + weather only |

All dependencies must be **MIT / BSD / Apache-2.0**, actively maintained, no known
major CVEs. Enforce continuously with `pip-audit` + `bandit` in CI.

---

## 3. Repository layout (this repo)

```
mcp-platform-py/
├── CLAUDE.md · DESIGN.md · README.md
├── pyproject.toml                    ← uv workspace root
├── uv.lock
├── deploy/ (docker-compose.yml, prometheus.yml, smoke-test.sh)
└── packages/
    ├── core/                         → published as `mcp-platform-core`
    │   ├── pyproject.toml
    │   └── src/mcp_platform_core/
    │       ├── __init__.py           ← PUBLIC API surface (cross-repo contract; SemVer)
    │       ├── types.py              ← ToolDefinition, Tier, KeyStore, UsageSink, ToolContext…
    │       ├── registry.py           ← ToolRegistry
    │       ├── middleware.py         ← build_tool_executor + InMemory KeyStore/RateLimiter/Cache/UsageSink
    │       ├── resilience.py         ← ResilientCaller: timeout/retry/circuit breaker (NEW)
    │       ├── server.py             ← build_mcp_server: registry → FastMCP
    │       ├── config.py             ← CoreConfig (pydantic-settings) + key loader
    │       ├── observability/
    │       │   ├── logger.py         ← create_logger (structlog)
    │       │   └── metrics.py        ← Metrics interface + prometheus/otel backends
    │       └── transports/http.py    ← run_stdio, run_http (+ health, graceful shutdown)
    │   └── tests/ (conftest.py, test_middleware.py, test_resilience.py)
    └── weather_mcp/                  ← in-repo reference/test app
        ├── pyproject.toml (depends on core via local path)
        ├── Dockerfile · keys.example.json · .env.example
        └── src/weather_mcp/
            ├── server.py · lib.py
            └── tools/ (geocode.py, current.py, forecast.py)
```

Dependency direction is one-way: `app → core`. Never put app-specific logic in
core; never modify core from an app repo.

---

## 4. Public API contract (never break without a major version bump)

Everything exported from `mcp_platform_core/__init__.py` is a versioned contract
(external app repos depend on it). Core types (illustrative — finalize in code):

```python
Tier = Literal["free", "premium", "enterprise"]
TIER_RANK = {"free": 0, "premium": 1, "enterprise": 2}

@dataclass(frozen=True)
class ApiKeyRecord: api_key: str; owner: str; tier: Tier; rate_limit_per_minute: int

class KeyStore(Protocol):  async def resolve(self, api_key: str | None) -> ApiKeyRecord: ...
class UsageSink(Protocol): async def record(self, event: UsageEvent) -> None: ...

@dataclass
class ToolContext:
    request_id: str; account: ApiKeyRecord; api_key: str | None
    resilient: ResilientCaller; log: structlog.BoundLogger

class ToolDefinition(BaseModel):
    name: str
    description: str                 # LLM-facing; write with care
    input_model: type[BaseModel]     # pydantic model = zod-shape analogue
    min_tier: Tier = "free"
    cost_units: int = 1
    cache_ttl_ms: int | None = None  # opt-in ONLY; never a default
    handler: Callable[[BaseModel, ToolContext], Awaitable[Any]]
```

---

## 5. Middleware execution order (FIXED — never reorder)

```
resolve account
  → tier check              (reject < min_tier)
  → cache lookup            (hit ⇒ zero-cost usage event, SKIP rate limiter)
  → rate-limit check        (per api_key, rolling 60s window)
  → run handler             (through ctx.resilient)
  → populate cache          (only if cache_ttl_ms set and success)
  → emit metrics + structured log + usage event
```

Per-request child logger bound with `request_id`, `tool`, `owner`, `tier`.
Cache hits are billed at zero cost and skip rate limiting. Errors record an
`error` metric + a `success=False` usage event, then re-raise.

---

## 6. Conventions (hard rules)

- **Handlers are pure API clients.** No auth/tier/cache/retry/rate-limit logic in
  a handler — all of that is the middleware's job. Handlers call upstreams via
  `ctx.resilient.call(...)`.
- **Never write to stdout in the stdio path.** stdout is the JSON-RPC wire; logs
  go to stderr (structlog is configured for this). Metrics auto-disable for stdio.
- **`cache_ttl_ms` is opt-in.** No default TTL on `ToolDefinition`; each tool
  declares its own staleness tolerance.
- **Every tool needs a clear `description`.** It's how the LLM chooses tools;
  treat it like API docs.
- **Test the middleware, not the tools.** Handlers are thin; mock httpx with
  `respx`. Regression-test tier/rate/cache/resilience behavior.
- **Secrets only from env/secret manager.** Never baked into images or logged
  (structlog redaction processor strips known secret keys).
- **Metrics call sites are backend-agnostic** — only touch the `Metrics`
  interface, never a backend directly.

---

## 7. Weather app tools (the in-repo reference app)

| Tool | Provider | min_tier | cache_ttl | cost |
|---|---|---|---|---|
| `geocode_place` | Open-Meteo Geocoding | free | 24 h | 1 |
| `get_current_weather` | Open-Meteo | free | 5 min | 1 |
| `get_forecast` | Open-Meteo | free | 1 h | 1 |
| `get_weather_premium` | OpenWeatherMap (One Call) | premium | 5 min | 3 |

Free tools are keyless (Open-Meteo). The premium tool needs `OPENWEATHERMAP_API_KEY`
and exists to demonstrate tier gating. The wide TTL spread (5 min → 24 h)
exercises the per-tool cache.

---

## 8. Environment variables

| Variable | Default | Notes |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_HTTP_PORT` | `8080` | HTTP only |
| `MCP_HTTP_PATH` | `/mcp` | MCP endpoint path |
| `MCP_KEYS_FILE` | — | JSON key store path |
| `MCP_API_KEY` | — | stdio only: key this process acts as |
| `LOG_LEVEL` | `info` | structlog level |
| `MCP_METRICS_ENABLED` | `true` if HTTP else `false` | force override |
| `MCP_METRICS_BACKEND` | `prometheus` | `prometheus` or `otel` (needs `[otel]` extra) |
| `MCP_METRICS_PORT` | `9464` | Prometheus exposition port |
| `MCP_UPSTREAM_TIMEOUT_S` | `4.0` | default per-attempt timeout |
| `MCP_UPSTREAM_RETRIES` | `2` | default bounded retries |
| `MCP_BREAKER_THRESHOLD` | `5` | consecutive failures to open circuit |
| `MCP_BREAKER_COOLDOWN_S` | `30` | open-state cooldown |
| `OPENWEATHERMAP_API_KEY` | — | required only for the premium weather tool |

---

## 9. Commands

```bash
uv sync                                  # install workspace + lock
uv run pytest                            # tests
uv run ruff check . && uv run mypy .     # lint + types
uv run pip-audit && uv run bandit -r packages/*/src   # security gates

# stdio (keyless)
MCP_TRANSPORT=stdio uv run weather-mcp

# HTTP (keyless free tools)
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 \
  MCP_KEYS_FILE=packages/weather_mcp/keys.example.json \
  uv run weather-mcp

# demo stack (weather-mcp + Prometheus), no secrets
docker compose -f deploy/docker-compose.yml up --build

# smoke test a running HTTP server
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464
```

---

## 10. Build order for this repo (Phase A)

1. Scaffold uv workspace, two packages, tooling (ruff/mypy/pytest/pip-audit/bandit), CI.
2. `types.py`, `registry.py`.
3. `observability/logger.py` + `observability/metrics.py` (interface + prometheus
   default + otel adapter) + test fixtures.
4. `middleware.py` — `build_tool_executor` + InMemory KeyStore/RateLimiter/Cache/UsageSink; tests.
5. `resilience.py` — timeout/retry/circuit breaker; tests.
6. `server.py` + `transports/http.py` — FastMCP wiring, stdio, Streamable HTTP,
   `/healthz`, `/readyz`, graceful shutdown.
7. `config.py` — `CoreConfig` + key loader.
8. weather app — three keyless tools + one premium tool, `lib.py`, entrypoint, Dockerfile.
9. deploy assets + README; verification pass (pytest, smoke test, pip-audit, bandit).
10. Tag `mcp-platform-core v0.1.0` so the future app repos can pin it.

After Phase A: the three app repos (finance/github/ebay) consume core as a
library; later, an `openapi_mcp` generator repo (DESIGN.md §17).

---

## 11. Out of scope now (documented, not built)

Redis-backed KeyStore/RateLimiter/Cache (multi-replica + a prerequisite for true
FaaS), DB-backed key store + admin API, billing pipeline via usage events,
OpenTelemetry **tracing** (metrics adapter is in; tracing is not), the
`openapi_mcp` generator, and True FaaS/Lambda deployment. All sit behind existing
interfaces so they slot in without touching tool or middleware code.
