# O'Reilly Learning + mcp-bookmarks

## Setup

1. Create a token: [MCP Tokens](https://learning.oreilly.com/access-tokens/) (Profile) or your org’s API Tokens / Content MCP token (Admin).
2. **Endpoint (Streamable HTTP):** `https://api.oreilly.com/api/content-discovery/v1/mcp/`
3. **Auth:** `Authorization: Bearer <token>` (no OAuth on this server). Full API doc: [learning.oreilly.com/apidocs/mcp/content](https://learning.oreilly.com/apidocs/mcp/content).
4. In **Cursor**, merge into `mcpServers` (use project **`.cursor/mcp.json`**, gitignored—copy from [`.cursor/mcp.json.example`](../.cursor/mcp.json.example)).
5. Keep **bookmarks** SSE on `http://localhost:8000/sse` (or your deploy).

**Combined guide (PT):** [`integracao-mcp-oreilly-brightdata.md`](integracao-mcp-oreilly-brightdata.md).

## Prompt pattern

> Search O'Reilly for “[topic]”. Summarize the best chapter or video. If the platform provides a stable link I may bookmark, call `save_bookmark` with that URL, then `get_tags`, `tag_bookmark`, and `set_summary`.

## Compliance

- Content is subject to your **O'Reilly subscription** and their terms.
- Do not paste full copyrighted chapters into bookmarks unless permitted; prefer links, short quotes, and your own summaries.
