# START HERE — moving implementation to Claude Code

This folder is a self-contained handoff for building the Python MCP platform in
the Claude Code CLI or the VS Code extension. It contains everything decided
during planning.

## Files

- **CLAUDE.md** — authoritative project context. Claude Code loads this
  automatically when you open the folder. It has the resolved decisions, repo
  layout, public API contract, middleware order, conventions, env vars, commands,
  and the Phase A build order.
- **DESIGN.md** — the full design doc with all rationale (why each choice, the
  TS→Python mirror table, resilience layer, deployment incl. serverless, the
  OpenAPI-generator roadmap). Reference when you want the "why."

## Setup (one time)

1. Move or copy this `mcp-platform-py/` folder to wherever you keep repos, e.g.
   `~/code/mcp-platform-py`, and make it its own git repo:
   ```bash
   mv mcp-platform-py ~/code/mcp-platform-py
   cd ~/code/mcp-platform-py && git init
   ```
2. Open it in Claude Code:
   - **CLI:** `cd ~/code/mcp-platform-py && claude`
   - **VS Code:** open the folder, then launch the Claude Code panel.
3. Make sure `uv` is installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

## Kickoff prompt (paste into Claude Code)

> Read CLAUDE.md and DESIGN.md fully. We are implementing Phase A: the
> `mcp-platform-core` library plus the in-repo keyless `weather` app, per
> CLAUDE.md §10. Follow the resolved decisions in §2 and the hard rules in §6
> exactly — do not re-litigate them.
>
> Start with step 1 (scaffold the uv workspace, `packages/core` and
> `packages/weather_mcp`, and tooling: ruff, mypy, pytest, pip-audit, bandit, plus
> a GitHub Actions CI). Then implement steps 2–5 (types, registry, observability
> with the pluggable prometheus/otel metrics, middleware, resilience) with unit
> tests, before wiring the transports and the weather app.
>
> Work in small commits per step. After each step, run `uv run pytest`,
> `uv run ruff check .`, and `uv run mypy .` and keep them green. Test the
> middleware and resilience layers, not the tool handlers (mock httpx with
> respx). Ask me before adding any dependency not already listed in CLAUDE.md §2.

## Definition of done for Phase A

- `uv run pytest` green (middleware: tier/rate/cache/usage; resilience:
  retry/breaker/timeout).
- `weather-mcp` runs over stdio and HTTP; free tools work with **no secrets**.
- `docker compose -f deploy/docker-compose.yml up` brings up weather-mcp +
  Prometheus; `/healthz`, `/readyz`, and `:9464/metrics` all respond.
- `deploy/smoke-test.sh` passes end to end.
- `pip-audit` and `bandit` clean.
- Tag `mcp-platform-core v0.1.0`.

## After Phase A

The three app repos (finance, github, ebay) are separate repos that pin
`mcp-platform-core` via a git tag and contain only app code. A cookiecutter/
template makes them mostly "add tools." See DESIGN.md §4.1, §11, §15 (Phase B).
The `openapi_mcp` generator is Phase C (DESIGN.md §17).
