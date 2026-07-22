# MCP Platform (Python) — Design & Architecture

**Status:** Draft for approval · **Date:** 2026-07-22 · **Author:** Claude (for rp)

This document proposes a Python port of the existing TypeScript `mcp-platform`.
It is a design/architecture document only — no code is written until it is
approved. It is written to mirror the proven structure of the TS platform
(`packages/core` + `packages/finance-mcp`), keep every architectural property
that project already earned, and add the one capability layer you asked for
that the TS version does not yet have: **resilience** (retries, timeouts,
circuit breakers).

---

## 1. Goal

Build a **generic, enterprise-grade MCP server infrastructure in Python**, ship a
keyless **weather MCP server** in the same repo as its built-in reference/test
app, and prove reusability with three further apps (**finance, GitHub, eBay**)
that each live in their own repo and consume the core as a published library. The
finance app is a direct functional parallel to the TS project, so the two can be
compared feature-for-feature and a team fluent in one can read the other.

The platform must be:

- **Extensible** — adding a tool, vertical, tier, or transport touches only the
  relevant layer, never the others (registry pattern).
- **Multi-packaging** — the same tool logic ships as a standalone HTTP
  container, a stdio process, and an embeddable Python library. (The browser
  extension target does **not** port to Python — see §3.3.)
- **Deployable on-prem or on any public cloud** — a single OCI container image,
  runnable on a bare VM (systemd), Docker/Compose, or Kubernetes; no managed
  service is required.
- **Observable out of the box** — structured JSON logs + Prometheus-format
  metrics, vendor-neutral, no tracing dependency in scope yet.
- **Secure & monetizable** — pluggable API-key auth, tiered access
  (free / premium / enterprise), per-key rate limiting, per-call cost units
  feeding a usage/billing event stream.
- **Resilient** — configurable timeouts, bounded retries with backoff, and
  per-upstream circuit breakers so a flaky provider degrades gracefully instead
  of cascading. *(New vs. the TS platform.)*

---

## 2. What we are mirroring, and what is deliberately different

### 2.1 Carried over unchanged (concept-for-concept)

| TS concept | Python equivalent | Notes |
|---|---|---|
| `ToolDefinition` (zod shape + handler) | `ToolDefinition` (Pydantic model + async handler) | Transport-agnostic tool contract |
| `ToolRegistry` | `ToolRegistry` | register / register_all / get / list |
| `createToolExecutor` middleware chain | `build_tool_executor` | Same fixed order (§6) |
| `KeyStore` / `InMemoryKeyStore` | same interfaces (`Protocol`) | Swap for Redis/DB later |
| `RateLimiter`, `ResponseCache` | same, in-process, behind interfaces | Redis swap point |
| `UsageSink` / `LoggingUsageSink` | same | Billing event stream |
| Tier gating (`free`/`premium`/`enterprise`) | same enum + rank map | Monetization |
| stdio + Streamable HTTP transports | same, via FastMCP | §7 |
| `/healthz`, `/readyz`, graceful shutdown | same, via Starlette side-app | §7.2 |
| structured logs + Prometheus metrics | structlog + prometheus_client | §8 |
| Dockerfile + docker-compose + Prometheus demo | same | §11 |

### 2.2 Added (your requested scope)

- **Resilience layer** (`core/resilience.py`): a small, dependency-light
  `resilient_call()` helper wrapping upstream calls with timeout + retry +
  circuit breaker, injected into `ToolContext` so handlers opt in with one call.
  See §9.

### 2.3 Intentionally dropped or reshaped

- **Chrome MV3 browser extension** — does not port. A browser extension service
  worker runs JavaScript; Python cannot execute there. The equivalent need
  ("call the free tools from a browser") is met by consuming the HTTP container
  from JS, or by keeping the tiny existing JS extension pointed at the Python
  container. This is called out explicitly so it isn't mistaken for an omission.
- **`fetch`/undici** → **httpx.AsyncClient** with a shared pooled client.

---

## 3. Technology choices (all license- and security-vetted)

Selection criteria you set: permissive OSS license (MIT / BSD / Apache-2.0),
active and well-supported, no known major unpatched vulnerabilities. All
versions below are pinned at build time and scanned (see §3.2).

### 3.1 Core dependencies

| Concern | Library | License | Why |
|---|---|---|---|
| MCP protocol + server | `mcp` (official SDK, incl. FastMCP) | MIT | Reference implementation; supports stdio **and** Streamable HTTP — the two transports you chose |
| Input schemas / validation | `pydantic` v2 | MIT | The zod analogue; FastMCP already uses it, so no version conflict |
| Config from env/file | `pydantic-settings` | MIT | Typed, layered config (env → file → defaults) |
| Async HTTP client | `httpx` | BSD-3-Clause | Connection pooling to upstreams (undici analogue); async + sync |
| Structured logging | `structlog` | MIT / Apache-2.0 | JSON logs, `bind()` child loggers (pino analogue) |
| Metrics | `prometheus-client` (default) + `opentelemetry-sdk` (optional extra) | Apache-2.0 | Pluggable backend behind one interface; prometheus default, OTel opt-in |
| HTTP side-app (health/metrics) | `starlette` + `uvicorn` | BSD-3-Clause | FastMCP's own HTTP stack; reuse it for `/healthz`, `/readyz` |
| Retries/backoff | `tenacity` | Apache-2.0 | Battle-tested retry primitives for the resilience layer |
| Tests | `pytest`, `pytest-asyncio`, `respx` | MIT / BSD | Unit-test the middleware; mock httpx at the transport boundary |
| Packaging / workspace | `uv` + `hatchling` | MIT / Apache-2.0 | Fast, reproducible monorepo with two local packages |

Circuit breaker: implemented in-house (~40 lines) rather than adding `pybreaker`,
to keep the core dependency surface minimal. If you'd prefer the library,
`pybreaker` is MIT-licensed and a drop-in.

### 3.2 Security posture

- **Lockfile + hashes** via `uv.lock`; reproducible installs.
- **`pip-audit`** (Apache-2.0) run in CI and in the Docker build to fail on any
  dependency with a known CVE, satisfying the "no known major vulnerabilities"
  requirement continuously rather than as a one-time check.
- **`bandit`** static security linter over `src/` in CI.
- Container runs as a **non-root user**, distroless/slim base, read-only root
  filesystem compatible.
- Secrets (upstream API keys) only ever come from env/secret manager, never
  baked into the image or logged (structlog processor redacts known secret keys).

### 3.3 Resolved technology decisions

1. **Metrics backend — pluggable, both supported.** The `Metrics` façade is an
   interface; call sites are backend-agnostic. Two implementations ship:
   `prometheus-client` (the **default** runtime dependency, light) and an
   **OpenTelemetry adapter** as an optional extra (`mcp-platform-core[otel]`),
   selected via `MCP_METRICS_BACKEND=prometheus|otel`. Deployments pay only for
   the backend they use; no lock-in, no call-site churn. First drop builds the
   interface + prometheus default and includes the OTel adapter module.
2. **Build tool — `uv`** workspace (root repo) and `uv` in each app repo.
3. **Min Python — 3.11.**
4. **Core distribution — git-tag dependency** to start (apps pin
   `mcp-platform-core @ git+…@vX`), graduating to a private index later.
5. **Container registry — GHCR** by default (repos on GitHub).

---

## 4. Repository layout

```
mcp-platform-py/
├── README.md                         ← architecture narrative + usage
├── CLAUDE.md                         ← authoritative context doc (ported)
├── pyproject.toml                    ← uv workspace root
├── uv.lock
├── deploy/
│   ├── docker-compose.yml            ← demo: weather-mcp + Prometheus (keyless)
│   ├── prometheus.yml
│   └── smoke-test.sh                 ← end-to-end HTTP exerciser
└── packages/
    ├── core/                         ← generic, reusable framework → PUBLISHED as `mcp-platform-core`
    │   ├── pyproject.toml            ← the distributable library (SemVer, §4.2)
    │   └── src/mcp_platform_core/
    │       ├── __init__.py           ← public API surface (the cross-repo contract)
    │       ├── types.py              ← ToolDefinition, Tier, KeyStore, UsageSink…
    │       ├── registry.py           ← ToolRegistry
    │       ├── middleware.py         ← build_tool_executor + InMemory impls
    │       ├── resilience.py         ← timeouts / retries / circuit breaker (NEW)
    │       ├── server.py             ← build_mcp_server: wires registry → FastMCP
    │       ├── config.py             ← CoreConfig (pydantic-settings) + key loader
    │       ├── observability/ (logger.py, metrics.py)
    │       └── transports/ (http.py) ← run_stdio, run_http (+ health, shutdown)
    │   └── tests/ (conftest.py, test_middleware.py)
    └── weather_mcp/                  ← the in-repo REFERENCE/TEST app (proves core end-to-end)
        ├── pyproject.toml            ← depends on mcp-platform-core (local path in-repo)
        ├── Dockerfile · keys.example.json · .env.example
        └── src/weather_mcp/
            ├── server.py · lib.py
            └── tools/ (current.py, forecast.py, geocode.py)
```

**Repo 1 ships two things:** the published library `mcp-platform-core`, and
`weather-mcp` as the built-in reference app that exercises the library end to end
(the regression harness + the "how to build an app on this" worked example).
Weather is the ideal built-in app because its free tier (Open-Meteo) is
**keyless** — the core repo's CI, smoke test, and `docker compose` demo all run
with **no secrets at all**. Its one premium tool (OpenWeatherMap) still
demonstrates tier gating. Inside this repo, `weather_mcp` depends on core via a
local path/uv-workspace link so the two evolve together.

### 4.1 The three app repos (separate, library-consuming)

Finance, GitHub, and eBay each live in their **own repository** and depend on
`mcp-platform-core` **as an external published library** — exactly as any third
party would. They contain only app-specific code and never vendor or fork core.
Each repo is identical in shape:

```
<app>-mcp/                           ← e.g. finance-mcp, github-mcp, ebay-mcp
├── README.md
├── pyproject.toml                   ← dependency: mcp-platform-core >=X,<Y
├── Dockerfile
├── keys.example.json · .env.example
├── deploy/ (docker-compose.yml, prometheus.yml, smoke-test.sh)
├── src/<app>_mcp/
│   ├── server.py                    ← imports from mcp_platform_core; wires tools + deps + transport
│   ├── lib.py                       ← embeddable client
│   └── tools/                       ← ONLY the app's ToolDefinitions
└── tests/                           ← app-specific tests (mock upstreams)
```

The dependency is one-directional: `app → mcp-platform-core`. An app repo pins a
core version; upgrading core is a deliberate bump, not an accident. This is a
stronger proof of reusability than a monorepo — the library has to stand on its
own with no shared build tricks.

### 4.2 Distributing core across repos (versioning is now a real contract)

Because there are external consumers, the core's public API (everything exported
from `mcp_platform_core/__init__.py`, §5) is a **versioned contract** under
**SemVer**: breaking an interface is a major bump. Distribution options, in the
order I'd adopt them:

| Stage | Mechanism | Notes |
|---|---|---|
| Early dev | Git tag / direct URL dependency: `mcp-platform-core @ git+https://…@v0.1.0#subdirectory=packages/core` | No index needed; app repos pin a tag |
| Team / on-prem | Private index — GitHub Packages, AWS CodeArtifact, or Artifactory | `pip install mcp-platform-core` from a private feed |
| Public (optional) | Publish to **PyPI** | Only if the framework is meant to be openly reusable |

Recommendation: start with the **git-tag dependency** (zero infra, works today),
move to a **private index** once the three apps are real. Core ships a
`CHANGELOG.md` and a documented "public API" list so app authors know what's
stable vs. internal. `openapi_mcp` (§17) becomes its own library-consuming repo
too, following the same pattern.

---

## 5. Core interfaces (the contract — never break without updating all impls)

Python equivalents of the TS interfaces, using `Protocol` for structural typing
and Pydantic/dataclasses for data.

```python
# types.py  (illustrative signatures, not final code)

Tier = Literal["free", "premium", "enterprise"]
TIER_RANK: dict[Tier, int] = {"free": 0, "premium": 1, "enterprise": 2}

@dataclass(frozen=True)
class ApiKeyRecord:
    api_key: str
    owner: str
    tier: Tier
    rate_limit_per_minute: int

class KeyStore(Protocol):
    async def resolve(self, api_key: str | None) -> ApiKeyRecord: ...

class UsageSink(Protocol):
    async def record(self, event: "UsageEvent") -> None: ...

@dataclass
class ToolContext:
    request_id: str
    account: ApiKeyRecord
    api_key: str | None
    resilient: "ResilientCaller"   # NEW: handlers call upstreams through this
    log: "structlog.BoundLogger"

class ToolDefinition(BaseModel):
    name: str
    description: str
    input_model: type[BaseModel]        # Pydantic model = zod-shape analogue
    min_tier: Tier = "free"
    cost_units: int = 1
    cache_ttl_ms: int | None = None     # opt-in only, never a default
    handler: Callable[[BaseModel, ToolContext], Awaitable[Any]]
```

Design decision: the tool's `input_model` is a **Pydantic model class**. FastMCP
derives the JSON Schema from it, validates input before the handler runs, and
the handler receives a typed instance — the same "validated before handler"
guarantee zod gives in the TS version.

---

## 6. Middleware chain (fixed order — must not be reordered)

Identical semantics to the TS `createToolExecutor`:

```
resolve account
  → tier check              (reject < min_tier)
  → cache lookup            (hit ⇒ zero-cost usage event, skip rate limiter)
  → rate-limit check        (per api_key, rolling 60s window)
  → run handler             (through resilience layer, §9)
  → populate cache          (if cache_ttl_ms set and success)
  → emit metrics + log + usage event
```

- A per-request **child logger** is bound with `request_id`, `tool`, `owner`,
  `tier` — every log line for that call is correlatable with metrics (and, later,
  traces).
- **Cache hits** skip rate limiting and are billed at zero cost (they never touch
  the upstream), exactly as in TS.
- Errors record an `error`-status metric + usage event with `success=False`, and
  re-raise so the transport maps them to an MCP tool error.
- `TierError` and `RateLimitError` are typed and surfaced to the client as clear,
  actionable messages.

This whole chain is transport-independent — stdio, HTTP, and the embeddable
library all call the same executor.

---

## 7. Transports (both stdio and HTTP, as you selected)

### 7.1 FastMCP wiring (`server.py`)

`build_mcp_server(info, registry, deps, api_key)` constructs a FastMCP server and
registers each tool with a thin adapter that (a) validates input via the tool's
Pydantic model, (b) calls the shared executor, (c) formats the result as MCP
`text` content or an `isError` payload. The bound `api_key` is set per transport,
matching the TS rationale:

- **stdio** — one process per client; read `MCP_API_KEY` once at startup.
- **HTTP** — one server instance per session; read the `Authorization: Bearer`
  (or `x-api-key`) header when the session initializes.

### 7.2 HTTP specifics (`transports/http.py`)

FastMCP provides the Streamable HTTP app (Starlette-based). We mount alongside it:

- `GET /healthz` — liveness.
- `GET /readyz` — readiness; returns 503 once shutdown begins so a rolling
  deploy / load balancer drains in-flight sessions first.
- **Graceful shutdown** on `SIGTERM`/`SIGINT`: flip readiness to 503, stop
  accepting new sessions, let in-flight requests finish, then exit.
- **Structured access log** per request with an `x-request-id` (generated or
  echoed).
- **Metrics** are exposed by the `Metrics` class on a **separate port**
  (`:9464/metrics` by default) so scraping isn't gated by MCP auth/session logic.

Served by `uvicorn`. Behind a reverse proxy / ingress for TLS in production
(Streamable HTTP + TLS is the 2026 production standard).

### 7.3 stdio specifics

`run_stdio(server)` connects FastMCP's stdio transport. **stdout is reserved for
the JSON-RPC wire protocol** — logs go to **stderr** and metrics auto-disable in
stdio mode (many short-lived processes would collide on the exposition port).

---

## 8. Observability

- **Logging** — `structlog` emitting JSON to stdout (HTTP) or stderr (stdio).
  Every line carries `service`/`version`; per-request child loggers add
  `request_id`/`tool`/`owner`/`tier`. Vendor-neutral: any shipper (Vector, Fluent
  Bit, Loki, CloudWatch agent, Datadog) can parse it. A redaction processor
  strips known secret keys.
- **Metrics** — **pluggable backend** behind one `Metrics` interface, selected by
  `MCP_METRICS_BACKEND` (`prometheus` default | `otel`). Same instruments either
  way:
  - `mcp_tool_calls_total{tool,tier,status}`
  - `mcp_tool_call_duration_seconds{tool,tier}` (histogram)
  - `mcp_cache_events_total{tool,result}`
  - Plus resilience additions: `mcp_upstream_retries_total{tool}` and
    `mcp_circuit_state{upstream}` (0=closed,1=open,2=half-open).
  - **prometheus** backend exposes `:9464/metrics`; **otel** backend records via
    the OTel Meter API (Prometheus exporter, or point at any OTLP collector).
- **Tracing** — out of scope now, but `request_id` is already the natural future
  trace id; documented as a clean extension (OTel), no core coupling yet.

Because both backends sit behind the same interface, call sites never change and
a deployment picks its backend by config — the "decide the backend later" property
becomes "decide the backend per deployment."

---

## 9. Resilience layer (NEW — your requested addition)

A small module (`core/resilience.py`) providing a `ResilientCaller` injected into
every `ToolContext`. Handlers wrap upstream calls:

```python
data = await ctx.resilient.call(
    "alphavantage",                     # upstream/circuit key
    lambda: client.get(url),            # the actual async call
    timeout_s=4.0,
    retries=2,                          # bounded, jittered exponential backoff
)
```

Components:

- **Timeout** — hard per-attempt deadline (via `httpx` timeout + `asyncio`).
- **Retry** — `tenacity`, bounded attempts, exponential backoff with jitter,
  retrying only idempotent GETs and transient errors (network, 429, 5xx), never
  4xx client errors.
- **Circuit breaker** — per upstream key: after N consecutive failures the
  circuit opens for a cooldown, failing fast with a clear error instead of
  hammering a down provider; a half-open probe closes it on recovery. State is
  exported as a metric.
- Config defaults live in `CoreConfig` (§10) and are overridable per call.

Rationale: keeps the "handlers are pure API clients" rule — the handler asks for
resilience declaratively; all retry/timeout/breaker bookkeeping stays in the
platform, consistent with the "no cross-cutting logic in handlers" constraint.

---

## 10. Configuration & environment variables

`CoreConfig` via `pydantic-settings`, layered env → optional `.env`/file →
typed defaults. Ports the TS env surface and adds resilience knobs.

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
| `ALPHAVANTAGE_API_KEY` | — | required for premium stock tools |

Key store file format is unchanged from TS (`keys.example.json`): `apiKey →
{ owner, tier, rateLimitPerMinute }`. Unknown keys fall through to anonymous
free tier (not an error).

---

## 11. The verticals (four domains, one shared infra)

Four hand-written verticals prove the core is genuinely domain-agnostic. They
are deliberately different in shape: keyless public reads, token-authenticated
read+write, and OAuth-authenticated marketplace reads. Each handler is a thin
async httpx client calling upstreams through `ctx.resilient`; **no
auth/tier/cache/retry logic ever lives in a handler**. Each ships a `lib.py`
embeddable client (no MCP protocol) usable from any Python app/notebook.

**Repo placement (per your split):** weather (§11.2) lives *inside the core repo*
as the reference/test app (chosen because its free tier is keyless → zero-secret
CI/demo); finance, github, and eBay each live in their *own* repo consuming
`mcp-platform-core` as a published library (§4.1). The tool designs below are
identical regardless of where the code lives.

### 11.1 Finance / market data (parity with the TS project)

| Tool | Provider | Min tier | cache_ttl | cost |
|---|---|---|---|---|
| `get_crypto_price` | CoinGecko | free | 15 s | 1 |
| `get_crypto_market` | CoinGecko | free | 30 s | 1 |
| `get_fx_rate` | exchangerate.host | free | 60 s | 1 |
| `get_stock_quote` | Alpha Vantage | premium | 30 s | 5 |
| `get_company_overview` | Alpha Vantage | premium | 6 h | 5 |

### 11.2 Weather / geospatial (keyless free tier, rich cache-TTL story)

| Tool | Provider | Min tier | cache_ttl | cost |
|---|---|---|---|---|
| `geocode_place` | Open-Meteo Geocoding | free | 24 h | 1 |
| `get_current_weather` | Open-Meteo | free | 5 min | 1 |
| `get_forecast` | Open-Meteo | free | 1 h | 1 |
| `get_weather_premium` | OpenWeatherMap (One Call) | premium | 5 min | 3 |

*Exercises:* keyless public reads and a wide TTL spread (5 min → 24 h) that
stresses the per-tool cache design.

### 11.3 Developer / DevOps — GitHub (auth + read *and* write)

| Tool | GitHub REST endpoint | Min tier | cache_ttl | cost |
|---|---|---|---|---|
| `search_repositories` | `GET /search/repositories` | free | 60 s | 1 |
| `get_repo` | `GET /repos/{o}/{r}` | free | 5 min | 1 |
| `list_issues` | `GET /repos/{o}/{r}/issues` | free | 60 s | 1 |
| `create_issue` | `POST /repos/{o}/{r}/issues` | premium | — (no cache on writes) | 5 |
| `add_issue_comment` | `POST …/issues/{n}/comments` | premium | — | 5 |

*Exercises:* token-authenticated calls, **write operations** (writes are never
cached), pagination, and upstream rate limits mapping onto tiers. Server-side
GitHub token from env; the `premium` tier gates the mutating tools.

### 11.4 E-commerce / marketplace — eBay (OAuth-authenticated reads)

| Tool | eBay REST API | Min tier | cache_ttl | cost |
|---|---|---|---|---|
| `search_items` | Browse API `/item_summary/search` | free | 60 s | 1 |
| `get_item` | Browse API `/item/{id}` | free | 5 min | 1 |
| `get_item_by_legacy_id` | Browse API `/get_item_by_legacy_id` | premium | 5 min | 3 |

*Exercises:* OAuth 2 **client-credentials** token flow (fetch + refresh an app
token behind the scenes), demonstrating a third auth style distinct from
GitHub's static token and weather's keyless calls.

---

## 12. Build, release & deployment (container-first)

### 12.1 What each thing ships as

Two distinct artifact types, because the core and the apps play different roles:

| Component | Primary artifact | Why |
|---|---|---|
| `mcp-platform-core` | **Python wheel** (published to index/PyPI) | It's a *library*, not runnable on its own — apps import it |
| Each app (weather/finance/github/eBay) | **OCI container image** (+ a wheel) | The image is the deployable; the wheel enables stdio/embedded use |

So the container image is the right cloud deliverable **for the apps**; core stays
a versioned library the images build on. Each app repo owns its own `Dockerfile`
and CI, and produces its own independently-versioned image.

### 12.2 The image (per app)

- **Multi-stage build**: a builder stage resolves locked deps and builds the
  wheel; a slim/distroless runtime stage carries only the wheel + runtime deps.
- **Non-root user**, read-only root filesystem compatible, `EXPOSE 8080 9464`.
- **Config strictly via env / secrets** (12-factor) — no keys baked into the
  image; the same image promotes dev→staging→prod unchanged.
- **HTTP transport** is the container's job; **stdio** delivery is *not* a
  container (many short-lived processes) — for desktop clients you ship the wheel
  and users run `uvx <app>-mcp` / `pipx install`.
- **Multi-arch** (linux/amd64 + linux/arm64) via `docker buildx`, so it runs on
  Graviton/Ampere cloud instances and Apple-silicon dev laptops.

### 12.3 Release pipeline (CI, per app repo)

On a version tag: build image → run tests + smoke test → **scan** (`trivy`/`grype`)
and generate an **SBOM** → push to a registry tagged with both SemVer and the git
SHA → *(optional)* sign with `cosign`. Registry can be **GHCR, AWS ECR, Google
Artifact Registry, Azure ACR, or Docker Hub** — the choice is deployment-time,
not baked into the design.

### 12.4 Where it runs (all from the one image)

- **Serverless containers (best fit for public cloud)** — Google **Cloud Run**,
  AWS **App Runner** / **ECS Fargate**, Azure **Container Apps**. This I/O-bound
  MCP workload suits them perfectly: managed HTTPS, autoscaling, scale-to-zero,
  pay-per-use, `/healthz`+`/readyz` wired to their health checks.
- **Kubernetes (any cloud, GKE/EKS/AKS)** — Deployment + Service + HPA;
  `/healthz`→liveness, `/readyz`→readiness, `:9464/metrics`→ServiceMonitor.
  Horizontal scaling swap points (Redis-backed KeyStore / RateLimiter /
  ResponseCache) sit behind interfaces, documented, not yet implemented.
- **On-prem / bare VM** — `docker run` under systemd, or `docker compose` (the
  in-repo demo brings up weather-mcp + Prometheus, **no secrets required**).
  `SIGTERM` graceful shutdown gives clean rolling restarts.

Why container over the alternatives (serverless zip, VM images/AMIs, buildpacks):
the OCI image is the **only** artifact that runs identically across every one of
the targets above, which is exactly the multi-cloud / on-prem portability the
platform is meant to have.

### 12.5 Serverless — two very different senses

First, the mental model: **MCP over Streamable HTTP *is* HTTP request/response**
(JSON-RPC over a POST to one endpoint). So serverless isn't blocked by "MCP vs.
REST" — an MCP call *is* an HTTP call. What challenges serverless is **session
state + SSE streaming + in-memory infra**, not the protocol shape.

- **Serverless *containers* (recommended): fully supported, no code changes.**
  Cloud Run / App Runner / ECS Fargate / Azure Container Apps run the same image,
  scale to zero, bill per use, and keep the **entire** MCP feature set. For most
  "I don't want to manage VMs" goals, this is the answer.
- **True FaaS (Lambda / Cloud Functions): possible in stateless mode, with
  trade-offs.** Run with FastMCP `stateless_http=True` (fresh session per
  request, no SSE persistence) behind a Lambda Function URL (Lambda Web Adapter)
  or API Gateway. Two consequences:
  - **State must be externalized.** Ephemeral, isolated invocations have no
    durable local memory, so the in-process `ResponseCache` and `RateLimiter`
    can't enforce anything globally — the **Redis/DynamoDB swap points (§2.1,
    §11-scaling) become mandatory, not optional**. They're already interfaces, so
    it's a config swap, not a rewrite.
  - **Stateful/streaming features are lost** — server-initiated notifications,
    resource subscriptions, long SSE responses. Plain tool-call request/response
    (the common case) works; cold starts add latency.

**Guidance:** if "serverless" means scale-to-zero with no servers to manage, use
a **serverless container platform** and change nothing. Choose **Lambda-style
FaaS** only if you're already standardized on it, then enable stateless mode +
Redis/DynamoDB-backed stores. This is also a concrete driver to implement the
Redis swap points sooner rather than later.

---

## 13. Testing & verification strategy

- **Unit tests (pytest + pytest-asyncio)** target the **middleware**, not the
  tool handlers (handlers are thin clients): tier acceptance/rejection, rate-limit
  window, cache hit/miss + zero-cost billing on hit, usage-event emission,
  error-path metrics. Ports the TS `middleware.test.ts` suite (~11 cases) and
  adds resilience cases: retry-then-succeed, breaker-opens-after-N,
  breaker-half-open-recovery, timeout maps to error.
- **Upstream mocking** with `respx` at the httpx boundary — no live network in
  tests.
- **Smoke test** (`deploy/smoke-test.sh`) exercises a running HTTP server end to
  end (initialize session → list tools → call a free tool → assert metrics
  increment).
- **CI gates**: `pytest`, `pip-audit`, `bandit`, `ruff`/`mypy`.

---

## 14. Build / run commands (target UX)

```bash
uv sync                                  # install workspace + lock
uv run pytest                            # run test suite

# stdio (Claude Desktop / CLI clients) — keyless, free weather tools
MCP_TRANSPORT=stdio uv run weather-mcp

# HTTP (container/cloud) — keyless for free tools; OWM key only enables the premium tool
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 \
  MCP_KEYS_FILE=packages/weather_mcp/keys.example.json \
  uv run weather-mcp

# demo stack (weather-mcp + Prometheus) — no secrets required
docker compose -f deploy/docker-compose.yml up --build

# smoke test
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464
```

---

## 15. Phased implementation plan (once approved)

**Phase A — Repo 1 (`mcp-platform-core` + weather reference app):**

1. **Scaffold** — uv workspace, two packages, tooling (ruff/mypy/pytest/pip-audit), CI.
2. **Core types + registry** — `types.py`, `registry.py`.
3. **Observability** — `logger.py`, `metrics.py` + fixtures.
4. **Middleware** — `build_tool_executor`, InMemory KeyStore/RateLimiter/Cache/UsageSink + tests.
5. **Resilience** — `resilience.py` + tests (the net-new layer).
6. **Server + transports** — FastMCP wiring, stdio, Streamable HTTP, health, shutdown.
7. **Config** — `CoreConfig`, key loader.
8. **Weather reference app** (in Repo 1) — keyless free tools + one premium tool,
   `lib.py`, entrypoint, Dockerfile; proves the library end to end over both
   transports with **zero secrets**, and validates the cache-TTL spread.
9. **Deploy + docs (Repo 1)** — compose, prometheus.yml, smoke-test.sh, README,
   CLAUDE.md; verification pass (tests, smoke test, `pip-audit`, `bandit`).
10. **Tag + publish core** — cut `mcp-platform-core v0.1.0` (git tag or private
    index) so external repos can depend on a pinned version.

**Phase B — the three app repos (each separate, consuming published core):**

11. **Finance repo** — server-side key + premium tier; the market-data vertical.
12. **GitHub repo** — token auth + write tools; validates auth + mutation paths.
13. **eBay repo** — OAuth client-credentials flow; validates a third auth style.
    (An app-repo cookiecutter/template makes 11–13 largely scaffolding + tools.)

**Phase C (later):** `openapi_mcp` as its own library-consuming repo (§17).

Suggested first deliverable: Phase A (steps 1–10) — a green, publishable core plus
the keyless weather reference app — then the three app repos, then the generator.

---

## 16. Decisions — all resolved ✔

| # | Decision | Choice |
|---|---|---|
| 1 | Metrics backend | **Pluggable** — `prometheus-client` default + optional `[otel]` adapter, `MCP_METRICS_BACKEND` selects |
| 2 | Build tool | **uv** |
| 3 | Min Python | **3.11** |
| 4 | Core distribution | **Git-tag dependency** first → private index later |
| 5 | Container registry | **GHCR** (default) |
| 6 | First code drop | **Repo 1 (core + weather)** first, publish `core v0.1.0`, then the three app repos |
| — | Repo split | Repo 1 = core + weather (keyless reference); finance/github/eBay = separate library-consuming repos |
| — | Transports | stdio + Streamable HTTP |
| — | Infra scope | auth · config/lifecycle · observability · resilience |
| — | True FaaS | **Not in scope now** (serverless containers only); revisit with Redis swap points if needed |
| — | OpenAPI generator (§17) | Later, separate library-consuming repo |

No open decisions remain. Ready to start **Phase A** (§15) on your go-ahead.

---

## 17. Roadmap — OpenAPI-driven server generation

Your eventual goal: given an OpenAPI spec, generate the MCP server on the fly.
This is a natural capstone for the platform, and the ecosystem has already
solved the hard parsing part — so the design is about *reuse and differentiation*,
not building a transpiler from scratch.

### 17.1 One important clarification: the "client side" is mostly free

MCP is a **self-describing** protocol. Once a server advertises its tools and
resources, any MCP client discovers them at runtime via `tools/list` and
`resources/list` — there is no per-server client code to generate for the
protocol itself. So "generate the client-side interface" largely happens
automatically. The only thing you'd *optionally* generate for clients is a typed
Python SDK/stubs for non-LLM programmatic callers; standard LLM clients (Claude,
Cursor, …) need nothing generated. This is worth stating up front so the scope
stays honest.

### 17.2 What already exists, and what your infra adds

`FastMCP.from_openapi(spec)` and `from_fastapi(app)` already convert an OpenAPI
3.0/3.1 spec into MCP tools/resources: parameter extraction, request-body
handling, response normalization, and include/exclude route mapping. Vanilla,
that gives you a **raw proxy** — no tiers, no rate limiting, no cache, no
resilience, no usage/billing, no per-key auth.

**The differentiation is exactly the middleware you're already building.** The
generator's job is to emit tools that run through *your* `build_tool_executor`
chain, so a spec-generated server inherits tiered access, per-key rate limits,
TTL caching, circuit-breaking/retry, structured logs+metrics, and usage events —
the things an enterprise actually needs and that raw `from_openapi` does not
provide. That is the reason `openapi_mcp` belongs in *this* platform rather than
being just a call to a library.

### 17.3 Design sketch (`packages/openapi_mcp`)

Core function: `generate_tools(spec, overlay) -> list[ToolDefinition]`, then
registered in the standard `ToolRegistry` — after which the entire existing infra
applies unchanged. Two modes:

- **Runtime (default):** parse the spec at startup and dynamically register a
  `ToolDefinition` per operation. Zero codegen; fastest path to "any spec → a
  running enterprise MCP server."
- **Static codegen:** emit reviewable `tools/*.py` modules (via
  `datamodel-code-generator` for the Pydantic input models) that you commit and
  can hand-edit — for when you want the generated output under source control.

Mapping rules:

| OpenAPI element | MCP / platform mapping |
|---|---|
| operation (path + method) | one MCP tool |
| `operationId` | tool `name` (snake_cased) |
| `summary` / `description` | tool `description` (LLM-facing) |
| parameters + `requestBody` schema | synthesized Pydantic `input_model` |
| `GET` (idempotent) | tool, optionally also an MCP resource if URI-addressable |
| `POST/PUT/PATCH/DELETE` | tool, never cached |
| `securitySchemes` | credential injection (apiKey header/query, bearer, OAuth2) from env |
| `x-mcp-tier` / `x-mcp-cost-units` / `x-mcp-cache-ttl-ms` | tier / cost / cache — supplied via vendor extensions **or** an overlay file keyed by operationId/tag |

Two design points that make it usable rather than a toy:

- **Overlay config** — tier, cost, and cache TTL aren't in the OpenAPI standard.
  A small `overlay.(yaml|json)` keyed by `operationId` (or tag/glob) supplies
  them, so the same public spec can be monetized differently per deployment
  without editing the spec.
- **Route filtering is mandatory, not optional** — real specs have hundreds of
  operations, and exposing all of them degrades an LLM's tool selection. The
  generator defaults to include-by-tag/allowlist so a deployment ships a curated
  toolset.

### 17.4 Fit with the rest of the platform

`openapi_mcp` is its own installable package that **depends on core and reuses it
wholesale** — registry, executor, cache, tiers, resilience, transports,
observability. The four hand-written verticals stay as the reference/regression
fixtures and as the honest counterpoint: *generate when you want breadth fast;
hand-write (like finance/weather/github/ebay) when you want tight control over
tool ergonomics, descriptions, and response shaping for the LLM.*

---

Everything above is the plan; on your go-ahead (and answers to §16) I'll start
implementing per §15, extended with the finance/github/ebay app repos and, later,
the `openapi_mcp` generator.
```
