# Production smoke tests

Run after deploy or when validating the MCP server in production.

## MCP server (`DYNAMODB_MODE=true`)

1. Credentials: `AWS_*` or instance role; tables `mcp-bookmarks-links` / `mcp-bookmarks-tags` (or your overrides).
2. `uv run mcp-bookmarks` — listen on `MCP_PORT`, `GET /api/stats` returns JSON.
3. Client connects to `/sse`; run `save_bookmark` on a test URL → response includes string `bookmark_id` (UUID).
4. `extract_content(bookmark_id)` or `set_bookmark_body(bookmark_id, text)` → `read_bookmark` shows stored content.
5. DynamoDB console: new item has `aiContent` / `aiWordCount` when content tools ran.

## Production HTTPS endpoint

After `terraform apply` with `enable_alb=true` and `ecs_desired_count=1`, the public hostname is `var.mcp_hostname` (referred to below as `$HOST`).

### 1. Auth gate

```bash
HOST="<your-mcp-host>"

# No key → 401
curl -s -o /dev/null -w "%{http_code}" https://$HOST/api/stats
# Expected: 401

# Valid key → 200 + JSON
curl -s https://$HOST/api/stats \
  -H "Authorization: Bearer $MCP_API_KEY"
# Expected: {"total_bookmarks": N, "total_tags": M}
```

### 2. SSE transport (Claude Code, Cursor)

```bash
curl -N \
  -H "Accept: text/event-stream" \
  -H "Authorization: Bearer $MCP_API_KEY" \
  https://$HOST/sse
# Expected: event: endpoint\ndata: /messages/?session_id=...
```

### 3. MCP Inspector — 19 tools via SSE

```bash
npx @modelcontextprotocol/inspector
# Connect to: https://$HOST/sse
# Authorization header: Bearer $MCP_API_KEY
# Expected: 19 tools listed, 4 prompts, 2 resources
```

### 4. Streamable HTTP transport (ChatGPT connector)

```bash
# Two-step: initialize (gets session ID), then tools/list
SESSION=$(curl -si -X POST https://$HOST/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.1"}}}' \
  | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\r')

curl -s -X POST https://$HOST/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
# Expected: list of 19 tools in JSON
```

### 5. End-to-end DynamoDB write

```bash
MCP_BASE_URL=https://$HOST \
MCP_API_KEY=$MCP_API_KEY \
uv run python scripts/capture_demo.py
# Expected: all 5 steps PASS; new item visible in DynamoDB
```
