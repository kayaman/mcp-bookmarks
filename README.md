# mcp-bookmarks

An MCP (Model Context Protocol) server for intelligent bookmark management. Save URLs, extract Open Graph metadata and full article content, and build a curated tag taxonomy where the LLM acts as the decision engine for tag reuse.

Supports two storage backends:
- **SQLite** (default) — local `~/.mcp-bookmarks/bookmarks.db`
- **DynamoDB** (`DYNAMODB_MODE=true`) — connects to the live [blogmarks](https://blogmarks.dev) AWS tables, sharing data with the PWA

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  Claude / MCP Client                                          │
│                                                               │
│  "Save https://example.com/article"                           │
│    1. save_bookmark(url)      → extract OG metadata           │
│    2. extract_content(id)     → full article via trafilatura  │
│    3. get_tags()              → read existing taxonomy        │
│    4. create_tag()            → only if truly new concept     │
│    5. tag_bookmark(id, [...]) → assign tags                   │
│    6. set_summary(id, text)   → store AI summary              │
│                                                               │
│  Prompts:                                                     │
│    save_and_tag(url)          → full pipeline in one shot     │
│    bulk_save(urls)            → batch processing              │
│    curate_tags()              → taxonomy audit                │
│    knowledge_query(question)  → RAG over your bookmarks       │
│                                                               │
│  Resources:                                                   │
│    bookmarks://taxonomy       → full tag list as context      │
│    bookmarks://recent/{n}     → last N bookmarks              │
└──────────────┬────────────────────────────────────────────────┘
               │ SSE (http://localhost:8000/sse)
               ▼
┌───────────────────────────────────────────────────────────────┐
│  MCP Bookmarks Server (FastMCP + SSE + REST API)              │
│                                                               │
│  server.py    → 14 tools, 4 prompts, 2 resources             │
│  api.py       → REST: /api/save, /api/bookmarks, /api/tags   │
│  scraper.py   → OG extraction + trafilatura article parsing   │
│  models.py    → Pydantic: OGMetadata, ArticleContent, Tag …  │
│  db.py        → aiosqlite SQLite backend                      │
│  dynamodb.py  → boto3 DynamoDB backend (DYNAMODB_MODE=true)   │
└──────────────┬────────────────────────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
  SQLite           DynamoDB
  (default)        (blogmarks-links
                    blogmarks-tags)
```

## Setup

```bash
# Option A: uv (recommended)
uv sync

# Option B: pip
pip install -e .
```

## Running

### SQLite mode (default)

```bash
# Default: http://0.0.0.0:8000
uv run mcp-bookmarks

# Custom config
MCP_PORT=9000 MCP_HOST=127.0.0.1 uv run mcp-bookmarks

# Custom DB location
BOOKMARKS_DB_PATH=/path/to/bookmarks.db uv run mcp-bookmarks
```

### DynamoDB mode (live blogmarks data)

```bash
DYNAMODB_MODE=true \
AWS_DEFAULT_REGION=us-east-1 \
uv run mcp-bookmarks
```

`save_bookmark` returns a **`bookmark_id`**: UUID string in DynamoDB mode, integer in SQLite. Use that id with `extract_content`, `set_bookmark_body`, `tag_bookmark`, and `set_summary`.

Writes use the same field names as [blogmarks.dev](https://blogmarks.dev): `aiContent`, `aiWordCount`, `aiSummary`, `aiTags`, `aiProcessedAt`. Optional AWS Lambda (`blogmarks-ai-processor`) may still enrich items; the MCP can now persist text and tags directly without waiting for Lambda.

**`set_bookmark_body(bookmark_id, text)`** — use when another MCP (e.g. Bright Data, Tavily) already returned the page text; avoids a second HTTP fetch from this server.

### Podman Container

```bash
podman build -t mcp-bookmarks .
podman compose up -d
```

## Connecting Clients

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "bookmarks": {
      "type": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### Claude Code

```bash
claude mcp add --transport sse bookmarks http://localhost:8000/sse
```

### MCP Inspector (debugging)

```bash
uv run mcp-bookmarks &
npx -y @modelcontextprotocol/inspector
# Connect to http://localhost:8000/sse
```

### Fetch / search MCPs (Bright Data, Tavily)

Add Bright Data and/or Tavily as additional `mcpServers` (see each vendor’s MCP install docs and API keys). Typical flow: `save_bookmark(url)` → fetch HTML or snippets with the other MCP → `set_bookmark_body(bookmark_id, text)` → `get_tags` / `tag_bookmark` / `set_summary`.

### O'Reilly Learning (second MCP server)

Add the **O'Reilly platform MCP** as a second entry in `mcpServers` (see current O'Reilly docs for transport, URL, and auth). Keep **bookmarks** pointed at this server so the model can search O'Reilly and save highlights with `save_bookmark` / `save_and_tag`.

### Batch ingest (`blogmarks-crew`)

With `uv run mcp-bookmarks` running, ingest many URLs from a file (one per line; `#` comments allowed):

```bash
uv run blogmarks-crew ingest --urls-file urls.txt --api-base http://127.0.0.1:8000
```

Optional **CrewAI** topic clustering (install extras, set your LLM API key as required by CrewAI, e.g. `OPENAI_API_KEY`):

```bash
uv sync --extra crew
uv run blogmarks-crew agents --urls-file urls.txt
```

### AWS (Terraform)

Infrastructure-as-code for DynamoDB, RDS (pgvector-ready), Lambda, ECS, budgets, and cost tags lives in [`terraform/`](terraform/). Pre-production stacks can be destroyed and recreated with `terraform destroy` / `apply` while you have no customer data to preserve.

### Deploying this package (per release)

1. Bump `version` in [`pyproject.toml`](pyproject.toml) (semver).
2. **MCP server** used by Cursor/Claude: rebuild/restart whatever runs `uv run mcp-bookmarks` (or your container image) so workers pick up the new code.
3. **Optional Lambda** in [`terraform/`](terraform/): run `terraform/scripts/package-lambda.sh`, then `terraform apply` if you manage the processor with this repo (note: the sample Lambda uses a different item schema than production blogmarks unless you align attribute names).
4. **blogmarks.dev PWA/API** live in their own repo/deploy pipeline; this repository mainly ships the MCP + optional Terraform.

### Roadmap: PWA (Android Share Target)

The Blogmarks **PWA** (separate front-end repo / deployment) can use the [Web Share Target API](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/How_to/Share_data_between_apps) to receive URLs from Android share sheets and call the authenticated ingest API.

## Tools

| Tool | Description |
|---|---|
| `save_bookmark(url)` | Fetch URL, extract OG metadata, store bookmark |
| `extract_content(bookmark_id)` | Extract full article text via trafilatura |
| `set_bookmark_body(bookmark_id, text)` | Store text from another fetch tool (Bright Data, Tavily, etc.) |
| `read_bookmark(bookmark_id)` | Get full bookmark details including content |
| `get_tags(query?)` | List canonical tags with descriptions and usage counts |
| `create_tag(slug, name, description)` | Create a new tag — only when no existing tag fits |
| `tag_bookmark(bookmark_id, tag_slugs)` | Assign existing tags to a bookmark |
| `untag_bookmark(bookmark_id, tag_slugs)` | Remove tags from a bookmark |
| `search_bookmarks(query?, tag?, limit?)` | Search by text or filter by tag |
| `set_summary(bookmark_id, summary)` | Store an AI-generated summary |
| `get_stats()` | Total bookmarks and tags count |
| `delete_bookmark(bookmark_id)` | Delete a bookmark |
| `update_tag(slug, name?, description?)` | Update tag metadata |
| `delete_tag(slug)` | Delete a tag from all bookmarks |
| `merge_tags(source, target)` | Merge duplicate tags |
| `export_bookmarks(format?, tag?)` | Export as JSON, Markdown, or OPML |

## Prompts

| Prompt | Args | Description |
|---|---|---|
| `save_and_tag` | `url` | Full pipeline: save → extract → tag → summarize |
| `bulk_save` | `urls` (newline-separated) | Batch process multiple URLs |
| `curate_tags` | — | Audit taxonomy for duplicates, gaps, weak descriptions |
| `knowledge_query` | `question` | RAG: search bookmarks and synthesize an answer |

## Resources

| URI | Description |
|---|---|
| `bookmarks://taxonomy` | Full tag list with descriptions — ideal pre-context for tagging |
| `bookmarks://recent/{count}` | Last N bookmarks with tags and summaries |

## Tag Deduplication Strategy

Instead of rules, the LLM reads the full taxonomy and **semantically decides** tag reuse. Each tag carries a scoped description:

```json
{
  "slug": "machine-learning",
  "name": "Machine Learning",
  "description": "General ML concepts, algorithms, training techniques. NOT specific frameworks.",
  "usage_count": 47
}
```

This prevents `ml`, `ML-algorithms`, `machine_learning` from proliferating.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_PORT` | `8000` | Server port |
| `MCP_HOST` | `0.0.0.0` | Server bind address |
| `BOOKMARKS_DB_PATH` | `~/.mcp-bookmarks/bookmarks.db` | SQLite path (SQLite mode only) |
| `DYNAMODB_MODE` | `false` | Set `true` to use DynamoDB instead of SQLite |
| `DYNAMODB_LINKS_TABLE` | `blogmarks-links` | DynamoDB bookmark items table |
| `DYNAMODB_TAGS_TABLE` | `blogmarks-tags` | DynamoDB tag taxonomy table |
| `DYNAMODB_USER_ID` | `mcp-agent` | userId stamped on MCP-saved bookmarks |
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region for DynamoDB |

## Project Structure

```
mcp-bookmarks/
├── pyproject.toml           # Dependencies (includes boto3) and entrypoint
├── compose.yaml             # Podman/Docker compose
├── Containerfile            # Multi-stage container build
├── tests/
│   ├── test_smoke.py        # Core DB operations
│   ├── test_api.py          # REST API
│   ├── test_blogmarks_crew.py  # blogmarks-crew ingest (unittest)
│   ├── test_e2e_sse.py      # Full SSE + MCP protocol
│   └── test_management.py   # Tag management (merge, delete, update)
├── src/mcp_bookmarks/
│   ├── models.py            # Pydantic: OGMetadata, ArticleContent, Tag, Bookmark
│   ├── db.py                # SQLite backend (aiosqlite, auto-migration)
│   ├── dynamodb.py          # DynamoDB backend (boto3, DYNAMODB_MODE=true)
│   ├── scraper.py           # httpx + BS4 + trafilatura
│   ├── api.py               # REST routes
│   ├── cli.py               # Terminal client
│   └── server.py            # FastMCP: 14 tools, 4 prompts, 2 resources
└── src/blogmarks_crew/
    ├── cli.py               # blogmarks-crew ingest | agents
    ├── ingest.py            # Batch POST /api/save
    └── crew_pipeline.py     # Optional CrewAI topic clustering ([project.optional-dependencies] crew)
```
