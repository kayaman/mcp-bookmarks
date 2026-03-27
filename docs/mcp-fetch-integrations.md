# Bright Data & Tavily MCP (multi-server)

Use alongside **bookmarks** when sites block simple HTTP fetches.

## Flow

1. `save_bookmark(url)` — stores metadata and returns `bookmark_id`.
2. Use **Bright Data** or **Tavily** MCP tools to obtain page HTML or extracted text (per vendor docs and your API keys).
3. `set_bookmark_body(bookmark_id, text)` — persists to the same schema as `extract_content` (`aiContent` in DynamoDB).
4. `get_tags` → `tag_bookmark` → `set_summary`.

## Cursor / Claude Desktop (`mcpServers`)

Add entries from each vendor’s MCP installation guide. Example shape (URLs and env names vary):

```json
{
  "mcpServers": {
    "bookmarks": {
      "type": "sse",
      "url": "http://localhost:8000/sse"
    },
    "tavily": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-tavily"],
      "env": {
        "TAVILY_API_KEY": "your-key"
      }
    }
  }
}
```

Bright Data: follow [Bright Data MCP](https://brightdata.com/) documentation for command/transport.

## Cost & TOS

- Metered APIs — set budgets in vendor dashboards.
- Respect robots.txt, terms of target sites, and copyright; `set_bookmark_body` is for text you are allowed to store.

## Fallback

If external MCPs are unavailable, use `extract_content(bookmark_id)` (trafilatura over HTTP from this server).
