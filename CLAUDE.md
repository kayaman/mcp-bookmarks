# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the server (default: http://0.0.0.0:8000)
uv run mcp-bookmarks

# Run with custom config
MCP_PORT=9000 MCP_HOST=127.0.0.1 BOOKMARKS_DB_PATH=/path/to/db.sqlite uv run mcp-bookmarks

# Run tests
uv run python tests/test_smoke.py
uv run python tests/test_api.py
uv run python tests/test_e2e_sse.py
uv run python tests/test_management.py

# CLI client
uv run mcp-bookmarks-cli

# Container
podman build -t mcp-bookmarks .
podman compose up -d
```

## Architecture

This is an MCP (Model Context Protocol) server exposing bookmark management via SSE transport. It also exposes a REST API on the same port via a combined Starlette router.

**Layers:**
- `server.py` — FastMCP app with 14 tools, 4 prompts, 2 resources; mounts the REST router
- `api.py` — Starlette REST routes (`/api/save`, `/api/bookmarks`, `/api/tags`, `/api/stats`, `/bookmarklet`)
- `db.py` — Async SQLite wrapper (aiosqlite); handles schema creation, migrations, and all CRUD
- `scraper.py` — httpx + BeautifulSoup for OG metadata; trafilatura for full article extraction
- `models.py` — Pydantic domain models: `OGMetadata`, `Tag`, `Bookmark`, `ArticleContent`, `BookmarkCreateResult`
- `cli.py` — Terminal client with direct DB access (bypasses the server)

**Database:** SQLite at `~/.mcp-bookmarks/bookmarks.db` (overridable via `BOOKMARKS_DB_PATH`). Three tables: `bookmarks`, `tags`, `bookmark_tags` (many-to-many). `db.py` auto-migrates `content`/`word_count` columns if absent.

**Tag system design:** The LLM reads the full tag taxonomy (slugs + descriptions + usage counts) via the `bookmarks://taxonomy` resource before tagging. The goal is semantic deduplication — descriptions communicate each tag's scope so the LLM reuses existing tags rather than creating near-duplicates.

**Typical save_and_tag flow:** `save_bookmark(url)` → `extract_content(id)` → `get_tags()` → `create_tag()` if needed → `tag_bookmark(id, slugs)` → `set_summary(id, text)`

## DynamoDB Mode

Set `DYNAMODB_MODE=true` to connect to the live blogmarks AWS tables instead of local SQLite. This lets the MCP server read and write the same data as the blogmarks PWA.

```bash
DYNAMODB_MODE=true \
AWS_DEFAULT_REGION=us-east-1 \
uv run mcp-bookmarks
```

The DynamoDB adapter is in `src/mcp_bookmarks/dynamodb.py` and implements the same interface as `Database` in `db.py`. Bookmarks saved via MCP are tagged with `userId=mcp-agent` so the `blogmarks-ai-processor` Lambda automatically enriches them.

**DynamoDB mode env vars:**

| Variable | Default | Purpose |
|---|---|---|
| `DYNAMODB_LINKS_TABLE` | `blogmarks-links` | Bookmark items table |
| `DYNAMODB_TAGS_TABLE` | `blogmarks-tags` | Tag taxonomy table |
| `DYNAMODB_USER_ID` | `mcp-agent` | userId stamped on MCP-saved bookmarks |

Standard AWS credentials must be available (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / profile).

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `MCP_PORT` | `8000` | Server bind port |
| `MCP_HOST` | `0.0.0.0` | Server bind address |
| `BOOKMARKS_DB_PATH` | `~/.mcp-bookmarks/bookmarks.db` | SQLite file location |
| `DYNAMODB_MODE` | `false` | Set to `true` to use DynamoDB instead of SQLite |
