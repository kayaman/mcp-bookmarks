# Production smoke tests (Blogmarks + MCP)

Run after deploy or when validating [blogmarks.dev](https://blogmarks.dev) and the MCP server.

## PWA (repository: `kayaman/blogmarks`)

1. Open `https://blogmarks.dev` — app shell loads, no console errors.
2. Sign-in flow works (your IdP).
3. Save a public article URL — item appears in the list with title.
4. Open the saved item — **full text** (`aiContent` / article body) is present after processing (or within expected async delay).
5. Network tab: API responses `2xx` for save/fetch; no repeated `5xx` on the same URL.

## MCP server (`DYNAMODB_MODE=true`)

1. Credentials: `AWS_*` or instance role; tables `blogmarks-links` / `blogmarks-tags` (or overrides).
2. `uv run mcp-bookmarks` — listen on `MCP_PORT`, `GET /api/stats` returns JSON.
3. Client connects to `/sse`; run `save_bookmark` on a test URL → response includes string `bookmark_id` (UUID).
4. `extract_content(bookmark_id)` or `set_bookmark_body(bookmark_id, text)` → `read_bookmark` shows stored content.
5. DynamoDB console: new item has `aiContent` / `aiWordCount` when content tools ran.

## Production HTTPS endpoint (`https://mcp.blogmarks.dev`)

After `terraform apply` with `enable_alb=true` and `ecs_desired_count=1`:

### 1. Auth gate

```bash
# No key → 401
curl -s -o /dev/null -w "%{http_code}" https://mcp.blogmarks.dev/api/stats
# Expected: 401

# Valid key → 200 + JSON
curl -s https://mcp.blogmarks.dev/api/stats \
  -H "Authorization: Bearer $BLOGMARKS_MCP_KEY"
# Expected: {"total_bookmarks": N, "total_tags": M}
```

### 2. SSE transport (Claude Code, Cursor)

```bash
curl -N \
  -H "Accept: text/event-stream" \
  -H "Authorization: Bearer $BLOGMARKS_MCP_KEY" \
  https://mcp.blogmarks.dev/sse
# Expected: event: endpoint\ndata: /messages/?session_id=...
```

### 3. MCP Inspector — 19 tools via SSE

```bash
npx @modelcontextprotocol/inspector
# Connect to: https://mcp.blogmarks.dev/sse
# Authorization header: Bearer $BLOGMARKS_MCP_KEY
# Expected: 19 tools listed, 4 prompts, 2 resources
```

### 4. Streamable HTTP transport (ChatGPT connector)

```bash
# Two-step: initialize (gets session ID), then tools/list
SESSION=$(curl -si -X POST https://mcp.blogmarks.dev/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $BLOGMARKS_MCP_KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.1"}}}' \
  | grep -i "mcp-session-id" | awk '{print $2}' | tr -d '\r')

curl -s -X POST https://mcp.blogmarks.dev/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $BLOGMARKS_MCP_KEY" \
  -H "Mcp-Session-Id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
# Expected: list of 19 tools in JSON
```

### 5. End-to-end DynamoDB write

```bash
MCP_BASE_URL=https://mcp.blogmarks.dev \
MCP_API_KEY=$BLOGMARKS_MCP_KEY \
uv run python scripts/capture_demo.py
# Expected: all 5 steps PASS; item visible in https://blogmarks.dev
```

## Regression references

- Blogmarks PWA fixes: GitHub PRs *restore production article saves*, *PWA update delivery* (`kayaman/blogmarks`).
