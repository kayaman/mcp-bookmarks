# Demo: Cursor IDE → mcp-bookmarks (production)

## Prerequisites

- Cursor 0.43+ (MCP support GA)
- `MCP_API_KEY` set in your shell environment (Cursor must inherit it — launch from terminal)

## Connect

Create or edit `.cursor/mcp.json` in any workspace (or `~/.cursor/mcp.json` for global):

```json
{
  "mcpServers": {
    "bookmarks": {
      "type": "sse",
      "url": "https://<your-mcp-host>/sse",
      "headers": {
        "Authorization": "Bearer ${env:MCP_API_KEY}"
      }
    }
  }
}
```

Reload MCP: `Cmd/Ctrl+Shift+P` → **MCP: Reload Servers** → confirm `bookmarks` shows 14 tools.

## 5-step demo flow (Cursor Chat)

Open the MCP panel or a chat window and run:

```
1. @bookmarks save_bookmark url="https://martinfowler.com/articles/2025-llm-agent.html"
   → bookmark_id (UUID, proves DynamoDB write)

2. @bookmarks Read resource bookmarks://taxonomy
   → tag taxonomy with descriptions and usage counts

3. @bookmarks Use the save_and_tag prompt for https://martinfowler.com/articles/2025-llm-agent.html
   → full enrichment pipeline in one shot

4. @bookmarks search_bookmarks query="agents"
   → new item appears

5. Verify via read_bookmark(<id>) or AWS console — confirms DynamoDB write
```

## Notes

- Transport: **SSE** (`/sse`). Cursor's SSE connector supports custom headers.
- `${env:MCP_API_KEY}` interpolates the environment variable at runtime; the key is never stored in the JSON file.
- For project-level isolation, commit `.cursor/mcp.json` without the key and set `MCP_API_KEY` via your team's secret manager.
- The `.cursor/mcp.json` file is gitignored by default in this repo.
