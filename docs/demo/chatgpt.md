# Demo: ChatGPT Custom Connector → mcp-bookmarks (production)

## Prerequisites

- ChatGPT Plus, Pro, or Team plan (custom connectors require a paid tier)
- A demo API key for `MCP_API_KEY`

## Transport

ChatGPT custom connectors use **Streamable HTTP** (`/mcp`), not SSE. Both transports are
served from the same server:

| Transport | Path | Used by |
|---|---|---|
| SSE | `https://<your-mcp-host>/sse` | Claude Code, Cursor |
| Streamable HTTP | `https://<your-mcp-host>/mcp` | ChatGPT |

## Connect

1. Go to **ChatGPT** → **Settings** → **Connectors** → **Add custom connector**
2. Fill in:
   - **Name**: `Bookmarks`
   - **URL**: `https://<your-mcp-host>/mcp`
   - **Authentication type**: Bearer token
   - **Token**: `<your-demo-token>`
3. Click **Save** and confirm the connector lists the available tools.

## 5-step demo flow

Paste these prompts into a ChatGPT conversation with the Bookmarks connector enabled:

```
1. "Save this bookmark: https://martinfowler.com/articles/2025-llm-agent.html"
   → ChatGPT calls save_bookmark; note the bookmark_id in the response

2. "Show me the full tag taxonomy"
   → Reads bookmarks://taxonomy resource

3. "Save and fully tag: https://martinfowler.com/articles/2025-llm-agent.html"
   → Runs save_and_tag prompt (extract → tag → summarize)

4. "Search my bookmarks for articles about agents"
   → Calls search_bookmarks(query="agents")

5. Inspect via read_bookmark(<id>) or AWS console — confirm DynamoDB write
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Cannot connect to server" | Confirm HTTPS; ChatGPT blocks HTTP origins |
| "401 Unauthorized" | Check the Bearer token matches `MCP_API_KEYS` on the server |
| "SSE endpoint not supported" | Use `/mcp` (Streamable HTTP), not `/sse` |
| Connector lists 0 tools | Server may be starting; wait 30s and retry |

## Notes

- ChatGPT does **not** support the SSE transport (`/sse`). You must use `/mcp`.
- The Streamable HTTP transport is stateless-compatible: ChatGPT may open a new session per message, which is fine.
- Token rotation: update the secret in AWS Secrets Manager (`mcp-bookmarks-api-keys`) and rotate the Connector token to match.
