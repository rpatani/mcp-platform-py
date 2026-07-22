# Manual testing guide (macOS) — MCP platform + apps

How to run and exercise everything locally on a Mac: the core reference app
(`weather-mcp`) and the three Phase B apps (`finance-mcp`, `github-mcp`,
`ebay-mcp`) that consume `mcp-platform-core` as an external, git-tag-pinned
library.

Repos (siblings under `~/46101labs/`):

| Repo | Auth style it proves | Free tools work with no secrets? |
|---|---|---|
| `mcp-platform-py` (core + `weather-mcp`) | keyless public reads | ✅ yes |
| `finance-mcp` | server-side API key (Alpha Vantage premium) | ✅ crypto + FX |
| `github-mcp` | static token + write ops | ✅ reads |
| `ebay-mcp` | OAuth 2 client-credentials | ⚠️ needs eBay creds for any call |

---

## 0. Prerequisites (one time)

```bash
# uv (package/venv manager) — you already have 0.9.x
curl -LsSf https://astral.sh/uv/install.sh | sh

# jq + curl are used by the smoke tests (curl ships with macOS)
brew install jq

# Docker Desktop only if you want the compose demos (optional)
```

All four repos target Python 3.11+ and run on the uv-managed interpreter; no
system Python needed.

---

## 1. Weather (core reference app) — fully keyless

```bash
cd ~/46101labs/mcp-platform-py
uv sync

# Run the full test suite + gates for the whole platform
uv run pytest                                   # 66 tests
uv run ruff check . && uv run mypy .
uv run pip-audit && uv run bandit -r packages/core/src packages/weather_mcp/src
```

### Run over HTTP and smoke-test it

```bash
# terminal A — start the server (keyless)
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 \
  MCP_KEYS_FILE=packages/weather_mcp/keys.example.json \
  uv run weather-mcp

# terminal B — drive a full MCP session + assert metrics
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464
```

Poke it by hand:

```bash
curl -s http://localhost:8080/healthz            # -> ok
curl -s http://localhost:8080/readyz             # -> ready
curl -s http://localhost:9464/metrics | grep mcp_tool_calls_total
```

### Run over stdio (what Claude Desktop / CLI clients use)

```bash
MCP_TRANSPORT=stdio uv run weather-mcp
# stdout is the JSON-RPC wire; logs go to stderr. Use an MCP client to drive it
# (see §6). Ctrl-C to stop.
```

### Docker compose demo (weather + Prometheus, no secrets)

```bash
docker compose -f deploy/docker-compose.yml up --build
# then, in another terminal:
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464
open http://localhost:9090/targets            # Prometheus scraping weather-mcp
docker compose -f deploy/docker-compose.yml down
```

---

## 2. finance-mcp — server-side key auth

```bash
cd ~/46101labs/finance-mcp
uv sync                    # resolves mcp-platform-core from the local git tag
uv run pytest              # 14 tests

# Run (keyless free tools: crypto + FX)
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 MCP_KEYS_FILE=keys.example.json uv run finance-mcp
# in another terminal:
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464   # calls get_crypto_price live
```

### Try the premium (tiered) tools

The premium tools need **both** a `premium`-tier API key (from
`keys.example.json`, sent as a Bearer header) **and** `ALPHAVANTAGE_API_KEY`
(free from https://www.alphavantage.co/support/#api-key).

```bash
ALPHAVANTAGE_API_KEY=your_key \
  MCP_TRANSPORT=http MCP_KEYS_FILE=keys.example.json uv run finance-mcp
```

Then call `get_stock_quote` with `Authorization: Bearer premium-demo-key`
(see the raw-curl recipe in §5). Tier behavior you should observe:

- Bearer `premium-demo-key` → allowed (runs Alpha Vantage).
- no key / a `free` key → `isError: tool requires tier 'premium', account has tier 'free'`.
- premium key but no `ALPHAVANTAGE_API_KEY` → `isError: … requires ALPHAVANTAGE_API_KEY`.

---

## 3. github-mcp — token auth + writes

```bash
cd ~/46101labs/github-mcp
uv sync
uv run pytest              # 12 tests

# Run (read tools are keyless, just rate-limited)
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 MCP_KEYS_FILE=keys.example.json uv run github-mcp
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464   # calls get_repo live
```

### Try the write tools (create_issue / add_issue_comment)

These are **premium** and **mutating** — they genuinely create issues, so point
them at a repo you own. Needs `GITHUB_TOKEN` (a fine-grained PAT with
`issues:write`) and a `premium`-tier Bearer key.

```bash
GITHUB_TOKEN=ghp_xxx MCP_TRANSPORT=http MCP_KEYS_FILE=keys.example.json uv run github-mcp
# then call create_issue with Authorization: Bearer premium-demo-key (see §5)
```

Writes are never cached or auto-retried (a test asserts identical calls both hit
the upstream).

---

## 4. ebay-mcp — OAuth client-credentials

```bash
cd ~/46101labs/ebay-mcp
uv sync
uv run pytest              # 12 tests (incl. token fetch/cache/refresh)

# Run — all tools need eBay credentials to return data, but the server starts
# and responds gracefully without them.
MCP_TRANSPORT=http MCP_HTTP_PORT=8080 MCP_KEYS_FILE=keys.example.json uv run ebay-mcp
./deploy/smoke-test.sh http://localhost:8080 http://localhost:9464
# ^ credentials-aware: passes whether or not eBay creds are set.
```

With real credentials (from https://developer.ebay.com/ → my-account/keys):

```bash
EBAY_CLIENT_ID=... EBAY_CLIENT_SECRET=... \
  MCP_TRANSPORT=http MCP_KEYS_FILE=keys.example.json uv run ebay-mcp
# search_items / get_item then return live results; the app fetches + caches an
# OAuth app token automatically.
```

---

## 5. Raw MCP-over-HTTP recipe (no client needed)

Streamable HTTP responses are Server-Sent Events; pull the JSON from the `data:`
line. Each session: `initialize` → `notifications/initialized` → your calls.

```bash
BASE=http://localhost:8080/mcp
ACCEPT='application/json, text/event-stream'
KEY='premium-demo-key'   # or omit the Authorization header for anonymous/free

# 1) initialize — capture the session id header
SID=$(curl -s -L -D - -o /dev/null \
  -H "Content-Type: application/json" -H "Accept: $ACCEPT" \
  -H "Authorization: Bearer $KEY" \
  -X POST "$BASE" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"cli","version":"0"}}}' \
  | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')

# 2) initialized notification
curl -s -L -o /dev/null -H "Content-Type: application/json" -H "Accept: $ACCEPT" \
  -H "mcp-session-id: $SID" -H "Authorization: Bearer $KEY" \
  -X POST "$BASE" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3) list tools
curl -s -L -H "Content-Type: application/json" -H "Accept: $ACCEPT" \
  -H "mcp-session-id: $SID" -H "Authorization: Bearer $KEY" \
  -X POST "$BASE" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | grep '^data:' | sed 's/^data: //' | jq '.result.tools[].name'

# 4) call a tool
curl -s -L -H "Content-Type: application/json" -H "Accept: $ACCEPT" \
  -H "mcp-session-id: $SID" -H "Authorization: Bearer $KEY" \
  -X POST "$BASE" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_stock_quote","arguments":{"symbol":"IBM"}}}' \
  | grep '^data:' | sed 's/^data: //' | jq '.result'
```

Note: the endpoint is `/mcp`; a bare `/mcp` 307-redirects to `/mcp/`, so pass
`curl -L` (real MCP clients follow it automatically).

---

## 6. Drive a server with a real MCP client (stdio)

To use any of these from Claude Desktop, add to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "weather": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/<you>/46101labs/mcp-platform-py", "weather-mcp"],
      "env": { "MCP_TRANSPORT": "stdio" }
    },
    "finance": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/<you>/46101labs/finance-mcp", "finance-mcp"],
      "env": { "MCP_TRANSPORT": "stdio" }
    }
  }
}
```

Restart Claude Desktop; the tools appear in the client. (github/ebay follow the
same pattern; add their secrets under `env`.)

---

## 7. The core dependency (git tag) and pushing to a remote

Each app pins core in its `pyproject.toml`:

```toml
[tool.uv.sources]
mcp-platform-core = { git = "file:///Users/.../mcp-platform-py",
                      tag = "mcp-platform-core-v0.1.0",
                      subdirectory = "packages/core" }
```

For local dev this resolves from the local `mcp-platform-py` git repo at the
`mcp-platform-core-v0.1.0` tag — no network or remote needed. **When you push**
`mcp-platform-py` to GitHub, change the one `git = "file://…"` line in each app
to `git = "https://github.com/<you>/mcp-platform-py"` and re-run `uv sync`.
Nothing else changes.

> Docker builds of the app images require core to be reachable from *inside* the
> container, so the `file://` source must be switched to the remote URL before
> `docker compose up --build` works for finance/github/ebay. Local `uv run` works
> today with `file://`.

---

## 8. Quick "everything is green" sweep

```bash
for r in mcp-platform-py finance-mcp github-mcp ebay-mcp; do
  echo "== $r =="; cd ~/46101labs/$r
  uv run pytest -q && uv run ruff check . && uv run mypy . \
    && uv run pip-audit >/dev/null && echo "OK"
done
```
