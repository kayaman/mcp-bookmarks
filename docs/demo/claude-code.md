# Demo: Claude Code CLI → mcp-bookmarks (production)

## Prerequisites

- `claude` CLI installed (`npm i -g @anthropic-ai/claude-code`)
- `BLOGMARKS_MCP_KEY` environment variable set to your bearer token

## Connect

```bash
export BLOGMARKS_MCP_KEY="<your-demo-token>"

claude mcp add --transport sse blogmarks https://mcp.blogmarks.dev/sse \
  --header "Authorization: Bearer $BLOGMARKS_MCP_KEY"
```

Verify the server is reachable and lists tools:

```bash
claude mcp list
# Should show: blogmarks  https://mcp.blogmarks.dev/sse  (14 tools)
```

## 5-step demo flow

Run these inside a `claude` session with the `blogmarks` MCP active:

```
1. save_bookmark("https://martinfowler.com/articles/2025-llm-agent.html")
   → returns bookmark_id (UUID) — confirms DynamoDB write

2. Read resource: bookmarks://taxonomy
   → tag list with slugs, descriptions, usage counts

3. Run prompt: save_and_tag("https://martinfowler.com/articles/2025-llm-agent.html")
   → full pipeline: extract → get_tags → tag_bookmark → set_summary

4. search_bookmarks(query="agents")
   → new item appears in results

5. Open https://blogmarks.dev in a browser
   → same item visible in the PWA — proves shared DynamoDB tables
```

## Quick curl smoke test

```bash
# Check /api/stats requires auth
curl -s https://mcp.blogmarks.dev/api/stats          # → 401
curl -s https://mcp.blogmarks.dev/api/stats \
  -H "Authorization: Bearer $BLOGMARKS_MCP_KEY"      # → {"total_bookmarks":...}

# Confirm SSE stream opens
curl -N -s \
  -H "Accept: text/event-stream" \
  -H "Authorization: Bearer $BLOGMARKS_MCP_KEY" \
  https://mcp.blogmarks.dev/sse                      # → event: endpoint …
```

## Notes

- Transport: **SSE** (`/sse`). Claude Code supports SSE natively.
- The `--header` flag forwards the `Authorization` header on every SSE request.
- To remove: `claude mcp remove blogmarks`
