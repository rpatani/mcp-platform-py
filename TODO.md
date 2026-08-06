# TODO — MCP Platform

Open work items, captured 2026-08-06 during the first end-to-end manual test
pass across `mcp-platform-py` (core + weather) and the three Phase B app repos
(`finance-mcp`, `github-mcp`, `ebay-mcp`).

Ordering is roughly by priority within each section. See `CLAUDE.md` for the
resolved decisions and `DESIGN.md` for rationale; `CLAUDE.md` §11 lists what is
deliberately out of scope and is *not* duplicated here.

---

## 1. Immediate — before tagging `mcp-platform-core-v0.2.0`

- [ ] **Decide branch landing.** Debug-logging work sits on
      `feat/core-debug-logging-redaction` (commits `4c8b704`, `606c0d7`).
      Fast-forward to `main` before tagging, so the tag points at `main`.
- [ ] **Tag `mcp-platform-core-v0.2.0`.** Phase A build-order step 10.
      `v0.1.0` is already tagged; 0.2.0 is an additive/minor bump (new exports
      `redact` / `scrub_text` / `fingerprint` / `REDACTED`, plus an optional
      `log_level` kwarg on `run_http`). App repos may stay pinned to v0.1.0.
- [ ] **Verify the otel path once.** `MCP_METRICS_BACKEND=otel` is part of the
      public contract but its two tests skip by default. Run
      `uv sync --package mcp-platform-core --extra otel && uv run pytest` at
      least once before tagging.

## 2. Logging — follow-ups to the 0.2.0 work

- [ ] **Route uvicorn through structlog.** At `LOG_LEVEL=debug`, uvicorn's
      plain-text lines interleave with structlog JSON, so `jq` fails on the
      file and you need `grep '^{' file.log | jq .`. Blocks clean support
      bundles. Contained to `observability/logger.py`.
- [ ] **Split payload logging from `LOG_LEVEL`.** `tool_call_response` logs
      full upstream bodies at debug. For finance/eBay that is customer business
      data. Gate it behind a separate `MCP_LOG_PAYLOADS` so "enable debug for
      support" never implicitly means "send us your data".
- [ ] **`MCP_REDACT_EXTRA_KEYS`.** Redaction matches *known* credential shapes;
      a customer's internal token scheme matches nothing. Let them name their
      own fields.
- [ ] **Time-boxed debug mode** (`MCP_DEBUG_UNTIL`), so nobody leaves a customer
      site running at debug for six months.

## 3. Log persistence & customer-site troubleshooting

Context: core writes to stdout/stderr only — nothing is persisted. That is
correct for container-first deployment, but at a customer site we own neither
the infrastructure nor a collector, and a restart loses everything. Discussed
2026-08-06; nothing implemented yet.

- [ ] **Optional file sink with rotation** — `MCP_LOG_FILE`,
      `MCP_LOG_MAX_BYTES`, `MCP_LOG_BACKUPS`; off by default, stdout stays
      primary. Covers the customer with no log collector at all.
- [ ] **Support-bundle command** (`mcp-support-bundle`) writing one zip:
      version + build SHA, **redacted** config dump, `/healthz` + `/readyz`
      output, metrics snapshot, last N log lines. One command for the customer
      beats a back-and-forth — and a redacted config dump matters because half
      of real tickets are a bad env var, which today means asking them to paste
      an env listing containing their API keys.
- [ ] **Debug ring buffer / "flight recorder".** Keep the last ~500 debug events
      in memory always; dump only when an error fires. Gives full
      request/response detail for the failing call without running debug
      permanently. This is the item with real diagnostic leverage —
      intermittent customer failures are otherwise unreproducible. Touches the
      middleware chain, so land it separately.
- [ ] **Add a `logging:` block to `deploy/docker-compose.yml`.** Default
      `json-file` driver has no rotation → unbounded disk growth at a customer
      site.
- [ ] **Decide retention / data-transfer policy** (non-code). Once logs leave
      customer premises they are a data-transfer question, not a debugging
      convenience. Answer before the first escalation, not during it.
- [ ] **Support process: ask for a `request_id`, not a log file.** Already on
      every line; cheapest possible triage step. Document it.

## 4. Core robustness

- [ ] **Clean error on port-in-use.** Binding an occupied port raises a
      traceback out of the lifespan context (`transports/http.py`) instead of
      printing "port 8080 already in use" and exiting non-zero. Every app repo
      inherits this. Hit during manual testing when weather-mcp and finance-mcp
      both wanted 8080.

## 5. CI

- [ ] **Run the suite both with and without extras.** Without, to prove core
      works on the minimal dependency set; with `--all-extras`, to actually
      cover the otel adapter. A green run currently proves less than it looks
      like it does — the otel tests skip silently.

## 6. Docs — `MANUAL-TESTING.md`

- [ ] **Lead the premium-tool sections with the keyless tier check.** The tier
      gate runs *before* the handler (`CLAUDE.md` §5), so both outcomes are
      reachable with zero upstream credentials, and telling them apart *is* the
      test:
      - no key / free key → `requires tier 'premium'` (gate rejected)
      - premium key, no upstream key → `requires ALPHAVANTAGE_API_KEY` (gate
        passed, handler reached)
      Demote the provider signup to an optional step. Applies to finance §2 and
      weather's `get_weather_premium` equally.
- [ ] **Alpha Vantage free-key signup does not deliver a key** (observed
      2026-08-06 — the form accepts details but no key arrives). Options: note
      `ALPHAVANTAGE_API_KEY=demo` with `symbol=IBM`, or swap `finance-mcp` to a
      provider that issues keys instantly (Finnhub / Twelve Data / FMP). App
      change only — `lib.py` + one tool, no core impact.
- [ ] **Warn that each app repo has its own `deploy/smoke-test.sh`.** Filenames
      are identical but this repo's hard-codes weather's four tool names, so
      running it against another app fails at `tools/list`.
- [ ] **Document debug logging**: `LOG_LEVEL=debug`, the `tool_call_*` event
      table, `grep '^{' | jq` for filtering, and correlating one request end to
      end via `request_id`.
- [ ] **Note the port-collision workaround** — `MCP_HTTP_PORT` +
      `MCP_METRICS_PORT` for running two apps side by side.
- [ ] **Env var gotcha**: `VAR=value` on its own line sets a shell variable, not
      an exported one, so `uv run` never sees it. Use `export` or prefix it onto
      the command.

## 7. Phase B / distribution

- [ ] **Push `mcp-platform-py` to GitHub**, then switch each app repo's
      `[tool.uv.sources]` from `git = "file:///..."` to the remote URL. Docker
      builds of finance/github/ebay images cannot resolve `file://` from inside
      a container; local `uv run` works today either way.
