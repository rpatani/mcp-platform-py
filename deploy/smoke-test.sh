#!/usr/bin/env bash
# End-to-end smoke test for a running weather-mcp HTTP server.
#   ./deploy/smoke-test.sh [http_base] [metrics_base]
# Defaults: http_base=http://localhost:8080  metrics_base=http://localhost:9464
#
# Exercises: /healthz, /readyz, an MCP session (initialize -> initialized ->
# tools/list -> tools/call get_current_weather), and asserts the metrics counter
# incremented. Requires: curl, jq. Free tools only — no secrets needed.
set -euo pipefail

HTTP_BASE="${1:-http://localhost:8080}"
METRICS_BASE="${2:-http://localhost:9464}"
MCP_URL="${HTTP_BASE}/mcp"
ACCEPT='application/json, text/event-stream'
PROTO='2025-06-18'

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; exit 1; }

# Streamable HTTP responses are SSE; pull the JSON out of the `data:` line.
sse_data() { grep '^data:' | sed 's/^data: //'; }

echo "== health =="
[ "$(curl -sf -o /dev/null -w '%{http_code}' "${HTTP_BASE}/healthz")" = "200" ] \
  && pass "/healthz 200" || fail "/healthz not 200"
[ "$(curl -sf -o /dev/null -w '%{http_code}' "${HTTP_BASE}/readyz")" = "200" ] \
  && pass "/readyz 200" || fail "/readyz not 200"

echo "== mcp session =="
INIT_REQ=$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"%s","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' "$PROTO")
HEADERS=$(curl -sf -L -D - -o /tmp/smoke_init.txt \
  -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" \
  -X POST "$MCP_URL" -d "$INIT_REQ")
SESSION=$(printf '%s' "$HEADERS" | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
[ -n "$SESSION" ] && pass "initialize -> session ${SESSION:0:8}…" || fail "no mcp-session-id header"

# The protocol requires an initialized notification before further requests.
curl -sf -L -o /dev/null \
  -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" -H "mcp-session-id: ${SESSION}" \
  -X POST "$MCP_URL" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

echo "== tools/list =="
TOOLS=$(curl -sf -L \
  -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" -H "mcp-session-id: ${SESSION}" \
  -X POST "$MCP_URL" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | sse_data | jq -r '.result.tools[].name' | sort | tr '\n' ' ')
EXPECTED="geocode_place get_current_weather get_forecast get_weather_premium "
[ "$TOOLS" = "$EXPECTED" ] && pass "4 tools listed" || fail "unexpected tools: [$TOOLS]"

echo "== tools/call get_current_weather =="
CALL=$(curl -sf -L \
  -H "Content-Type: application/json" -H "Accept: ${ACCEPT}" -H "mcp-session-id: ${SESSION}" \
  -X POST "$MCP_URL" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_current_weather","arguments":{"latitude":52.52,"longitude":13.41}}}' | sse_data)
IS_ERROR=$(printf '%s' "$CALL" | jq -r '.result.isError')
[ "$IS_ERROR" = "false" ] && pass "get_current_weather ok (isError=false)" || fail "call errored: $CALL"

echo "== metrics =="
# Prometheus emits labels alphabetically, so match order-independently.
METRICS=$(curl -sf "${METRICS_BASE}/metrics")
if printf '%s' "$METRICS" | grep '^mcp_tool_calls_total{' \
    | grep 'tool="get_current_weather"' | grep -q 'status="success"'; then
  pass "mcp_tool_calls_total incremented for get_current_weather"
else
  fail "success metric not found for get_current_weather"
fi

echo ""
echo "SMOKE TEST PASSED"
