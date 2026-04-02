# mcp-bookmarks

An MCP (Model Context Protocol) server for intelligent bookmark management. Save URLs, extract Open Graph metadata and full article content, and build a curated tag taxonomy where the LLM acts as the decision engine for tag reuse.

Supports two storage backends:
- **SQLite** (default) — local `~/.mcp-bookmarks/bookmarks.db`
- **DynamoDB** (`DYNAMODB_MODE=true`) — connects to the live [blogmarks](https://blogmarks.dev) AWS tables, sharing data with the PWA

## Product direction

This repo is positioned as a **hybrid** product: **bookmark-native RAG and capture** (MCP + REST) is the primary wedge; generic “upload any corpus” RAG-as-a-service is **out of scope** until an optional HTTP retrieve API is built on top of the same auth/usage stack. Semantic search is **full-featured in SQLite**; **DynamoDB mode** still uses keyword search for retrieval until a cloud vector pipeline exists.

| Document | Purpose |
|----------|---------|
| [`docs/product-positioning.md`](docs/product-positioning.md) | Vertical vs horizontal boundary (decision record) |
| [`docs/production-readiness.md`](docs/production-readiness.md) | What is wired (auth, quotas, Stripe) and what to verify in production |
| [`docs/dynamodb-rag-design.md`](docs/dynamodb-rag-design.md) | Chunking, embedding model, vector store options for blogmarks/DynamoDB |

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
│  server.py    → MCP tools, prompts, resources                   │
│  api.py       → REST: /api/save, /api/usage, …                │
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

### O’Reilly + Bright Data + bookmarks (multi-MCP)

Integration is **three MCP servers in the client** (not bundled into this repo). Step-by-step (PT): **[`docs/integracao-mcp-oreilly-brightdata.md`](docs/integracao-mcp-oreilly-brightdata.md)**. Copy **[`.cursor/mcp.json.example`](.cursor/mcp.json.example)** → **`.cursor/mcp.json`** and replace `YOUR_OREILLY_MCP_TOKEN` and Bright Data token (`.cursor/mcp.json` is **gitignored**). O’Reilly official endpoint: `https://api.oreilly.com/api/content-discovery/v1/mcp/` + Bearer token ([docs](https://learning.oreilly.com/apidocs/mcp/content)). Cursor agents also load project rule **[`.cursor/rules/oreilly-mcp-agents.mdc`](.cursor/rules/oreilly-mcp-agents.mdc)** to prefer `search-oreilly-content` when relevant.

**Typical web flow:** `save_bookmark(url)` → Bright Data `scrape_as_markdown` (or search) → `set_bookmark_body(bookmark_id, text)` → `get_tags` / `tag_bookmark` / `set_summary`.

### Fetch / search MCPs (Bright Data, Tavily)

See also **[`docs/mcp-fetch-integrations.md`](docs/mcp-fetch-integrations.md)** for Tavily and Bright Data JSON snippets. O’Reilly-only prompts and compliance: **[`docs/oreilly-mcp.md`](docs/oreilly-mcp.md)**.

### Batch ingest (`blogmarks-crew`)

With `uv run mcp-bookmarks` running, ingest many URLs from a file (one per line; `#` comments allowed):

```bash
uv run blogmarks-crew ingest --urls-file urls.txt --api-base http://127.0.0.1:8000
# With REST API keys enabled:
uv run blogmarks-crew ingest --urls-file urls.txt --api-key "$MCP_API_KEY"
```

Optional **CrewAI** (install extras, set LLM env vars as required by CrewAI, e.g. `OPENAI_API_KEY`):

```bash
uv sync --extra crew
# Topic clusters from URL list only (no fetch)
uv run blogmarks-crew agents --urls-file urls.txt
# Save URL via REST, then librarian + editor agents tag + summarize (uses new REST tools)
uv run blogmarks-crew enrich --url https://example.com/article --api-base http://127.0.0.1:8000
# Topic slug ideas from one saved bookmark
uv run blogmarks-crew suggest-topics --bookmark-id 1 --api-base http://127.0.0.1:8000
```

### AWS (Terraform)

Infrastructure-as-code for DynamoDB (links, tags, **usage events**, **subscriptions**), RDS (pgvector-ready), Lambda, ECS, optional **ALB** (`enable_alb`), budgets, and cost tags lives in [`terraform/`](terraform/). Outputs include `alb_dns_name` when the load balancer is enabled. Stripe targets `POST /webhooks/stripe` on the same host as the MCP server.

### REST: auth, usage, billing hook

- **`MCP_API_KEYS`** — When set, `/api/*` requires `Authorization: Bearer <key>` or `X-API-Key`. Keys may map to tenants as `key:org-id` (see [`auth.py`](src/mcp_bookmarks/auth.py)).
- **`GET /api/usage`** — Monthly event count for the authenticated tenant (SQLite `usage_events`; pair with `MCP_MONTHLY_USAGE_LIMIT` for quotas).
- **`POST /webhooks/stripe`** — Configure in Stripe with **`STRIPE_WEBHOOK_SECRET`**; subscription snapshots go to SQLite and/or **`DYNAMODB_SUBSCRIPTIONS_TABLE`**.

### Semantic search (SQLite + OpenAI)

With **`OPENAI_API_KEY`** and **SQLite** mode (not DynamoDB): call **`index_bookmark_embedding`** after **`extract_content`**, then **`semantic_search_bookmarks`**. Vectors live in the local DB table `bookmark_embeddings`. For **DynamoDB / blogmarks**, semantic index design (chunking, vector store) is specified in [`docs/dynamodb-rag-design.md`](docs/dynamodb-rag-design.md)—not yet implemented in code.

### Rust fetch CLI (optional)

[`rust/blogmarks-fetch/`](rust/blogmarks-fetch/) — `cargo run --release -- https://example.com` prints JSON (`status`, `html_bytes`, optional `title` from `<title>`). Extend with readability-style extraction for batch ingest.

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
| `index_bookmark_embedding(bookmark_id)` | SQLite only: OpenAI embedding for title+description+content |
| `semantic_search_bookmarks(query, limit?)` | SQLite only: cosine similarity over stored embeddings |
| `ensemble_with_judge(task, models?, judge_model?)` | Optional: N models in parallel via OpenAI-compatible **AI Gateway**, then LLM judge merges/picks best (`ENSEMBLE_ENABLED=true`) |

### AI Gateway: vários modelos + juiz LLM

Aponta `AI_GATEWAY_BASE_URL` (ou `OPENAI_BASE_URL`) para o teu gateway **compatível com OpenAI** (`…/v1/chat/completions`). Define `ENSEMBLE_ENABLED=true`, chave em `AI_GATEWAY_API_KEY` ou `OPENAI_API_KEY`, e `ENSEMBLE_MODELS` (lista separada por vírgulas). O MCP tool **`ensemble_with_judge`** e **`POST /api/ensemble`** (`{"task":"...","models":["a","b"],"judge_model":"..."}`) fazem N chamadas em paralelo e uma chamada de **juiz** (`JUDGE_MODEL`, default `gpt-4o-mini`) que devolve JSON com `answer`, `rationale`, `chosen_index`. Custo: **N+1** chamadas; usa também o medidor de quota se estiver ativo.

**Painel web:** com o servidor a correr, abre **`/ai-gateway`** (ex.: `http://localhost:8000/ai-gateway`) para testar o ensemble no browser; **`GET /api/ai-gateway/status`** expõe só metadados seguros (sem chaves). Se **`MCP_API_KEYS`** estiver definido, usa o mesmo `Bearer` ou `X-API-Key` que nos outros endpoints (o painel pode guardar a chave REST em `sessionStorage` neste separador). Ver [`docs/ai-gateway-ensemble.md`](docs/ai-gateway-ensemble.md).

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
| `DYNAMODB_ORG_ID` | — | Optional org/tenant id for DynamoDB isolation + MCP usage tenant |
| `DYNAMODB_USAGE_TABLE` | — | DynamoDB table for usage events when in cloud |
| `DYNAMODB_SUBSCRIPTIONS_TABLE` | — | Stripe subscription rows (webhook) |
| `MCP_API_KEYS` | — | Comma-separated REST API keys (`key` or `key:org`) |
| `MCP_MONTHLY_USAGE_LIMIT` | `0` | Monthly quota (0 = off); enforced on MCP tools + REST save |
| `STRIPE_WEBHOOK_SECRET` | — | `whsec_...` for `/webhooks/stripe` |
| `OPENAI_API_KEY` | — | Embeddings for semantic search tools |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | Embedding model id |
| `ENSEMBLE_ENABLED` | `false` | `true` to allow `ensemble_with_judge` + `POST /api/ensemble` |
| `AI_GATEWAY_BASE_URL` | — | Gateway OpenAI-compatible (e.g. `https://api.openai.com/v1`) |
| `AI_GATEWAY_API_KEY` | — | Overrides `OPENAI_API_KEY` for ensemble calls if set |
| `ENSEMBLE_MODELS` | — | Default comma-separated models when the tool omits `models` |
| `JUDGE_MODEL` | `gpt-4o-mini` | Model that scores/merges candidate answers |
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region for DynamoDB |

## Project Structure

```
mcp-bookmarks/
├── pyproject.toml           # Dependencies (includes boto3) and entrypoint
├── compose.yaml             # Podman/Docker compose
├── Containerfile            # Multi-stage container build
├── docs/
│   ├── product-positioning.md   # Product boundary (vertical-first hybrid)
│   ├── production-readiness.md  # Auth, billing, quotas, RAG deployment notes
│   └── dynamodb-rag-design.md # Cloud vector pipeline (future implementation)
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
    ├── cli.py               # blogmarks-crew ingest | agents | enrich | suggest-topics
    ├── ingest.py            # Batch POST /api/save
    ├── api_base_util.py     # Normalize --api-base to …/api
    ├── rest_crew_tools.py   # httpx tools for CrewAI → REST
    ├── crew_pipeline.py     # CrewAI URL-list topic clusters ([crew] extra)
    ├── crew_enrich.py       # Librarian + editor after POST /save
    └── crew_topics.py       # Topic slug suggestions from GET /bookmarks/{id}
```
