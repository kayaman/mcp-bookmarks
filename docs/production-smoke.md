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

## Regression references

- Blogmarks PWA fixes: GitHub PRs *restore production article saves*, *PWA update delivery* (`kayaman/blogmarks`).
