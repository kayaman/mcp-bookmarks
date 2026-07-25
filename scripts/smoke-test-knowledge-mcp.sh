#!/usr/bin/env bash
# Smoke test for the Knowledge semantic MCP (EC2 + CloudFront, ADR-017).
#
#   BM_TOKEN=bm_v1_xxx ./smoke-test-knowledge-mcp.sh
#   BM_TOKEN=bm_v1_xxx MCP_HOST=https://mcp2.blogmarks.dev ./smoke-test-knowledge-mcp.sh
#
# The server runs FastMCP with stateless_http=True + json_response=True, so every
# call is self-contained: no Mcp-Session-Id handshake and no SSE frame parsing.
set -uo pipefail

MCP_HOST="${MCP_HOST:-https://mcp2.blogmarks.dev}"
ENDPOINT="$MCP_HOST/mcp"
pass=0; fail=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=$((fail+1)); }
hdr() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# rpc <method> <params-json> -> JSON-RPC response body on stdout
rpc() {
  curl -s --max-time 60 -X POST "$ENDPOINT" \
    -H "Authorization: Bearer ${BM_TOKEN}" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":$2}"
}

# call <tool> <args-json> -> the tool's text payload on stdout
call() {
  rpc tools/call "{\"name\":\"$1\",\"arguments\":$2}" \
    | jq -r '.result.content[0].text // .error.message // "NO_RESULT"'
}

hdr "0. Preflight"
command -v jq >/dev/null || { echo "jq is required"; exit 2; }
[ -n "${BM_TOKEN:-}" ] || { echo "Set BM_TOKEN (mint at https://blogmarks.dev/mcp)"; exit 2; }
echo "  endpoint: $ENDPOINT"

hdr "1. Transport & auth"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$MCP_HOST/health")
[ "$code" = 200 ] && ok "/health → 200" || bad "/health → $code (expected 200)"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST "$ENDPOINT" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
[ "$code" = 401 ] && ok "no token → 401" || bad "no token → $code (expected 401)"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST "$ENDPOINT" \
  -H 'Authorization: Bearer bm_v1_definitely_not_a_real_token' \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
[ "$code" = 401 ] && ok "bogus token → 401" || bad "bogus token → $code (expected 401)"

hdr "2. MCP handshake"
init=$(rpc initialize '{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}')
srv=$(jq -r '.result.serverInfo.name // empty' <<<"$init")
[ -n "$srv" ] && ok "initialize → serverInfo.name=$srv" || bad "initialize failed: $(head -c 200 <<<"$init")"

hdr "3. Tool surface"
tools=$(rpc tools/list '{}' | jq -r '.result.tools[].name' 2>/dev/null | sort)
n=$(wc -l <<<"$tools")
[ "$n" -gt 0 ] && ok "tools/list → $n tools" || bad "tools/list returned nothing"
for t in semantic_search_bookmarks search_bookmarks read_bookmark get_stats; do
  grep -qx "$t" <<<"$tools" && ok "exposes $t" || bad "MISSING tool: $t"
done

hdr "4. Keyword path (DynamoDB)"
stats=$(call get_stats '{}')
grep -qi 'bookmark' <<<"$stats" && ok "get_stats → ${stats:0:70}" || bad "get_stats → $stats"

kw=$(call search_bookmarks '{"query":"rust","limit":3}')
[ "$kw" != NO_RESULT ] && ok "search_bookmarks → ${#kw} chars" || bad "search_bookmarks failed"

hdr "5. Semantic path (hnswlib + Bedrock Titan)"
# Assert on the PARSED payload, never on string length: the failure response
# ({"results": [], "hint": "...still building or disabled..."}) is a long string,
# so a length check reports a false pass on the exact case this test exists for.
semantic() {
  local out; out=$(call semantic_search_bookmarks "{\"query\":$1,\"limit\":5}")
  local hits; hits=$(jq -r '.results | length' <<<"$out" 2>/dev/null)
  local hint; hint=$(jq -r '.hint // .error // empty' <<<"$out" 2>/dev/null)
  if [ -z "$hits" ]; then
    bad "$2 → unparseable: $(tr '\n' ' ' <<<"${out:0:120}")"
  elif [ "$hits" -eq 0 ]; then
    bad "$2 → 0 results${hint:+ ($hint)}"
  else
    ok "$2 → $hits hits, index size $(jq -r '.total_indexed // "?"' <<<"$out"), model $(jq -r '.model // "?"' <<<"$out")"
    jq -r '.results[:2][] | "      \(.score | tostring[:6])  \(.title[:64])"' <<<"$out"
  fi
}

semantic '"how do vector databases handle recall tradeoffs"' "topical query"

# A query sharing no literal tokens with its expected hits is the only check
# that distinguishes real embeddings from substring matching.
semantic '"machines that learn from examples"' "keyword-free query"

hdr "6. Scope gate (ADR-017 parity requirement)"
echo "  MANUAL: confirm a bookmark with mcpExposed=false is absent from results"
echo "  above, and that a tags-scoped token sees only its allow-listed tags."

hdr "Result"
printf '  %d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
