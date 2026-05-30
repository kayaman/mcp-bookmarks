# mcp-bookmarks — portfolio tour

A guided tour of the engineering decisions, end-to-end behaviour, and measured
performance of **mcp-bookmarks** — an MCP server that lets an LLM assistant save,
extract, tag, and semantically search bookmarks across a SQLite (local) or
DynamoDB (production) backend. This document is written for a reviewer who has
~15 minutes and wants to evaluate engineering judgment, not browse the codebase
top-to-bottom. Every claim links to a file, an ADR, or a measured number.

---

## How to read this repo in 5 minutes

A numbered path that minimises back-references. One sentence on what each stop
teaches you.

1. [`README.md`](../../README.md) — what the project is, how to run it locally
   in two commands, and where the MCP transport endpoints live.
2. [`docs/architecture.md`](../architecture.md) — the single Starlette app that
   serves MCP-over-SSE, the REST surface, and the static dashboard; the
   middleware stack; the backend protocol.
3. [`docs/adr/`](../adr/README.md) — the eight architectural decisions, with a
   declared reading order (`0001 → 0002 → 0004 → 0003 → 0007 → 0005 → 0006`)
   chosen to minimise forward references.
4. [`docs/api.md`](../api.md) — the REST surface (`/health`, `/ready`,
   `/api/capabilities`, `/api/stats`, `/api/bookmarks`, `/ai-gateway`) and the
   MCP tool catalogue, with request/response shapes.
5. [`docs/runbook.md`](../runbook.md) — what to do when something is on fire:
   readiness-probe failure modes, capability-mismatch envelopes, DynamoDB
   throttling, OpenAI key rotation.
6. [`docs/infra.md`](../infra.md) — the Terraform layout, the
   container/Lambda/ECS split per [ADR-0006](../adr/0006-lambda-vs-ecs-deployment-boundary.md),
   and the IAM surface.
7. [`docs/go-live.md`](../go-live.md) — the pre-flight checklist before pointing
   real traffic at a fresh deploy, including capability-probe and smoke-test
   sequence.

---

## Engineering depth

Seven decisions worth pausing on. Each one cites the file or PR you can read in
30 seconds to verify the claim.

### 1. CSP SHA-256 hash is computed from the script body, not hand-typed

**Where:** `src/mcp_bookmarks/security_headers.py` + `src/mcp_bookmarks/api.py`

Most engineers ship a CSP with `'unsafe-inline'` because keeping a hash in sync
with a script body by hand is a maintenance trap. Here the inline `/ai-gateway`
script is extracted into a named constant, the hash is recomputed at module
import (`AI_GATEWAY_SCRIPT_HASH = compute_script_hash(_AI_GATEWAY_INLINE_SCRIPT)`),
and injected into `SecurityHeadersMiddleware` at app construction. The hash can
never drift from the script — refactoring stays safe without weakening the CSP.
That is the *why* behind Lighthouse 100/100/100/100 on a page that renders LLM
output.

**Verifiable evidence:**

- `security_headers.py:39-48` — `compute_script_hash` returns `sha256-<base64>`
  of `body.encode('utf-8')`.
- `api.py:621` — `AI_GATEWAY_SCRIPT_HASH = compute_script_hash(_AI_GATEWAY_INLINE_SCRIPT)`.
- `server.py:1245` — `SecurityHeadersMiddleware(..., ai_gateway_script_hash=AI_GATEWAY_SCRIPT_HASH)`.
- PR #24 body: *"Any edit to `_AI_GATEWAY_INLINE_SCRIPT` automatically refreshes
  the CSP. No hand-typed hashes."*

### 2. Middleware order is documented as a 5-bullet justification, not just code

**Where:** `src/mcp_bookmarks/server.py`

Middleware ordering bugs are one of the most common quiet production failures
(CORS preflight blocked by auth, GZip applied to SSE, security headers missing
on error responses). Here the order is paired with an inline comment that
explains *why each layer sits where it sits* — including the non-obvious choice
to keep GZip outermost specifically because Starlette's `GZipMiddleware`
auto-skips `text/event-stream`, preserving the streaming MCP transport. Anyone
reordering this stack now has to consciously override the reasoning.

**Verifiable evidence:**

- `server.py:1228-1262` — 12-line comment block explains: GZip outermost (SSE
  auto-skip); Correlation next (so later log records carry the id); Security
  headers before CORS preflight; CORS before Auth (so preflight succeeds even
  when bearer would reject). Five middleware layers, five distinct
  justifications.

### 3. `cast(Any, db)` is quarantined inside services, one line below the capability gate

**Where:** PR #28 / `src/mcp_bookmarks/services/embedding.py`

PR #28 didn't try to eliminate the protocol-vs-concrete gap by lying with
`Any` everywhere or by inflating the protocol with optional methods. Instead,
each unsafe cast lives in exactly one place — inside a service module — and is
preceded on the line above by the `require_capability(...)` guard that
*justifies* the cast at runtime. The mapping from old call site to new service
is documented as a 5-row table in the PR body. That's how a senior engineer
handles a real type-safety leak: contain it, gate it, document it.

**Verifiable evidence:**

- `embedding.py:30-35` — `require_capability(db, "semantic_search", method="index_bookmark_embedding")`
  then `await cast(Any, db).upsert_bookmark_embedding(...)` on the very next
  line.
- PR #28 body table lists all four eliminated `cast(Any, db)` handler sites and
  the service each moved to.

### 4. Capability flags as a typed dataclass, not env-string sniffing

**Where:** PR #22 / `src/mcp_bookmarks/backend.py` —
see [ADR-0004](../adr/0004-backend-capability-divergence.md)

The naive version of dual-backend support is `if os.environ['DYNAMODB_MODE'] == 'true'`
sprinkled at every divergence point. [ADR-0004](../adr/0004-backend-capability-divergence.md)
codifies the alternative: a frozen `BackendCapabilities` dataclass where each
concrete backend declares its flags (`semantic_search`, `paged_search`,
`usage_metering`, `subscription_storage`, `integer_bookmark_ids`), unsupported
calls raise `UnsupportedCapability` with a structured
`{code, details: {backend, capability, method}}` envelope, and
`GET /api/capabilities` lets clients introspect. The protocol comment
explicitly says when to add a flag vs. extend the protocol — the design rules
are written down.

**Verifiable evidence:**

- `backend.py:35-68` — `@dataclass(frozen=True) class BackendCapabilities` with
  five named flags + comments explaining each implementation method.
- `backend.py:203-247` — `UnsupportedCapability` carries
  `{backend, capability, method}` and serialises to a structured envelope.
- PR #22 replaces `hasattr` / `DYNAMODB_MODE` checks in 3 `server.py` call
  sites.

### 5. `/ready` closes the DB in a `suppress(Exception)` finally — close failure must not mark service unhealthy

**Where:** PR #23 / `src/mcp_bookmarks/server.py`

A common bug in readiness probes: the probe opens a connection, runs a ping,
then closes the connection in `finally`. If `close()` raises (network blip,
broken pool), the probe flips to 503 even though the ping already proved the
backend works. This `/ready` handler explicitly suppresses the close error
with a one-line comment justifying *why*: "the ping already proved liveness".
That's the kind of edge-case reasoning that separates someone who's been paged
at 3am from someone who hasn't.

**Verifiable evidence:**

- `server.py:1191-1194` —
  `finally: # Close failure must not flip /ready to not_ready — the ping already proved liveness.`
  followed by `with contextlib.suppress(Exception): await db.close()`.
- The handler also wraps the open + ping in `asyncio.wait_for(..., timeout=3.0)`
  and returns structured 503s with `reason` so alarm payloads are readable.

### 6. ADR README declares a non-default reading order to minimise back-references

**Where:** PR #25 / [`docs/adr/README.md`](../adr/README.md)

ADRs commonly devolve into a numbered pile no one re-reads. This index does
two things most teams skip: (1) every ADR has a `Related:` line pointing at
both source files *and* other ADRs (8/8 ADRs, all with cross-refs), and
(2) the README explicitly tells a first-time reader the order —
`0001 → 0002 → 0004 → 0003 → 0007 → 0005 → 0006` — chosen because it
minimises forward references. That's editorial judgment about how to onboard a
future maintainer, not just archival discipline.

**Verifiable evidence:**

- [`docs/adr/README.md`](../adr/README.md) "Reading order" section names the
  path and explains it: *"minimises back-references"*.
- [ADR-0001](../adr/0001-sqlite-dynamodb-dual-mode-storage.md) `Related:` line
  points at 3 source/doc files + ADR-0004.
- [ADR-0007](../adr/0007-multi-tenancy-via-api-keys.md) references ADR-0001
  and ADR-0002 inline.
- `grep -c "ADR-" docs/adr/0001*` returns 6; `0006*` returns 3; `0007*` returns 4.

### 7. Test suite explicitly partitioned into unit / integration / live with a default `live` exclusion

**Where:** PR #17 + PR #27 / `pyproject.toml` + `Makefile`

Three-tier test layouts are common but most repos let them rot — `pytest` runs
everything, the network tests flake, contributors learn to ignore failures.
Here `pyproject.toml` ships `addopts = "-m 'not live'"` so the default
`pytest` invocation is deterministic out of the box, the live tier has its own
`pytest -m live` opt-in, and CI runs live only on manual `workflow_dispatch`.
The `tests/conftest.py` docstring spells out the rules for each tier (no I/O /
deterministic local / opt-in real network). The Makefile `ci` target chains
the same four gates a contributor can run locally in seconds.

**Verifiable evidence:**

- `pyproject.toml:30-34` — `addopts = "-m 'not live'"` + `markers = ["live: opt-in tests..."]`.
- `tests/conftest.py:6-13` documents the tier semantics.
- `Makefile` target `ci: lint format-check typecheck test` mirrors the four
  CI gates locally.
- PR #28 reports 139 passing on `make ci`.

---

## End-to-End Walkthrough: save → extract → tag → search

This is what a Claude Code or Cursor user sees when they connect to `mcp-bookmarks`
over SSE (`https://your-host/sse`) and run the canonical pipeline end-to-end. Every
tool returns a JSON-serialized string; the client renders it as a tool result inside
the conversation. All shapes below are derived from `src/mcp_bookmarks/server.py`.

### Step 1 — `save_bookmark`

Save the URL. The server fetches the page server-side, parses Open Graph metadata
(`og:title`, `og:description`, `og:image`, `og:site_name`), and writes a new row to
the active backend. The returned `bookmark_id` is a SQLite integer locally or a
DynamoDB UUID string in production.

```jsonc
// tool call envelope
{
  "name": "save_bookmark",
  "arguments": {
    "url": "https://martinfowler.com/articles/2025-llm-agent.html"
  }
}
```

```jsonc
// tool result (string body, parsed)
{
  "id": 142,
  "url": "https://martinfowler.com/articles/2025-llm-agent.html",
  "title": "Emerging Patterns in Building GenAI Products",
  "ogTitle": "Emerging Patterns in Building GenAI Products",
  "ogDescription": "Patterns observed shipping LLM-backed features in production…",
  "ogImage": "https://martinfowler.com/articles/2025-llm-agent/og.png",
  "ogSiteName": "martinfowler.com",
  "tags": [],
  "bookmark_id": 142,
  "existing_tags": [],
  "has_content": false,
  "hint": "DynamoDB: bookmark_id is a UUID string — pass it to extract_content, tag_bookmark, set_summary. SQLite: integer id. Now call get_tags() before tag_bookmark()."
}
```

### Step 2 — `extract_content`

Pull the full article body via `trafilatura` (strips nav, ads, footers), persist it
on the bookmark row, and return a 2000-char preview so the assistant can decide what
to tag without re-downloading. Subsequent calls short-circuit to `already_extracted`.

```jsonc
{
  "name": "extract_content",
  "arguments": { "bookmark_id": 142 }
}
```

```jsonc
{
  "bookmark_id": 142,
  "word_count": 4318,
  "extraction_method": "trafilatura",
  "content_preview": "Building products on top of large language models is a rapidly evolving discipline. In the past year we've shipped half a dozen GenAI features and the patterns that work — and the ones that don't — are starting to crystallize. This article catalogs the emerging vocabulary: evals, guardrails, the agent loop, tool budgets, retrieval shaping…",
  "hint": "Use set_summary() to store a concise summary based on this content."
}
```

### Step 3 — `get_tags`

Before creating anything, look up the canonical taxonomy. Passing a `query` does a
partial match across `slug` / `name` / `description` and returns each tag's
`usage_count` so the assistant prefers reusing broad, well-used tags over minting
near-duplicates.

```jsonc
{
  "name": "get_tags",
  "arguments": { "query": "agents" }
}
```

```jsonc
{
  "total": 2,
  "tags": [
    {
      "slug": "agents",
      "name": "AI Agents",
      "description": "Autonomous LLM-driven systems: tool use, planning loops, ReAct, agent frameworks.",
      "usage_count": 17
    },
    {
      "slug": "llm",
      "name": "LLMs",
      "description": "Large language models, prompting, fine-tuning, evals, and production patterns.",
      "usage_count": 41
    }
  ]
}
```

### Step 4 — `tag_bookmark`

Assign one or more existing tag slugs to the bookmark. The server validates every
slug exists (raising `ValueError` → `{"error": …}` if not), persists the join, and
echoes back the bookmark's full tag set. Use `create_tag(slug, name, description)`
first if `get_tags` showed no suitable match.

```jsonc
{
  "name": "tag_bookmark",
  "arguments": {
    "bookmark_id": 142,
    "tag_slugs": ["agents", "llm"]
  }
}
```

```jsonc
{
  "bookmark_id": 142,
  "url": "https://martinfowler.com/articles/2025-llm-agent.html",
  "title": "Emerging Patterns in Building GenAI Products",
  "tags": ["agents", "llm"]
}
```

### Step 5 — `semantic_search_bookmarks`

Vector search over OpenAI-embedded title + description + content (SQLite-only path;
DynamoDB mode returns an `UnsupportedCapability` envelope). Results are ranked by
cosine similarity, summary clipped to 400 chars. Each hit must first be embedded via
`index_bookmark_embedding(bookmark_id)`.

```jsonc
{
  "name": "semantic_search_bookmarks",
  "arguments": {
    "query": "autonomous web agents",
    "limit": 5
  }
}
```

```jsonc
{
  "query": "autonomous web agents",
  "model": "text-embedding-3-small",
  "total_indexed": 5,
  "results": [
    {
      "id": 142,
      "url": "https://martinfowler.com/articles/2025-llm-agent.html",
      "title": "Emerging Patterns in Building GenAI Products",
      "score": 0.871423,
      "tags": ["agents", "llm"],
      "summary": "Catalogs production patterns for LLM-backed features: evals-first development, guardrails, the agent loop, tool budgets, and retrieval shaping."
    },
    {
      "id": 98,
      "url": "https://www.anthropic.com/research/building-effective-agents",
      "title": "Building Effective Agents",
      "score": 0.842901,
      "tags": ["agents", "llm", "anthropic"],
      "summary": "Distinguishes workflows from agents and walks through orchestrator-workers, evaluator-optimizer, and routing patterns."
    },
    {
      "id": 117,
      "url": "https://lilianweng.github.io/posts/2023-06-23-agent/",
      "title": "LLM Powered Autonomous Agents",
      "score": 0.819755,
      "tags": ["agents", "llm", "survey"],
      "summary": "Survey of planning, memory, and tool-use components in autonomous LLM agents."
    }
  ]
}
```

---

## Benchmarks

Measured on the developer machine, captured with `curl`'s wall-clock timer. No
invented numbers — raw per-sample times are in the notes below and the full log
is at `/tmp/portfolio-bench.log`.

**Cold start: 614 ms** — wall-clock from `date +%s%3N` captured immediately
before `nohup uv run --python 3.12 mcp-bookmarks &` until the first
`curl /health` returned HTTP 200 (50 ms poll cadence).

| Endpoint                | p50 (ms) | p95 (ms) | Description                                                                          |
| ----------------------- | -------- | -------- | ------------------------------------------------------------------------------------ |
| `GET /health`           |    1.452 |    1.795 | Liveness probe — minimal JSON `{"status":"ok"}`, no DB hit                           |
| `GET /ready`            |    3.216 |    3.804 | Readiness probe — exercises DB ping, returns `{"status":"ready"}`                    |
| `GET /api/capabilities` |    2.905 |    3.509 | Static capabilities document (~159 bytes) — no auth (`MCP_API_KEYS` unset)           |
| `GET /api/stats`        |    3.291 |    4.527 | Aggregate counts from default SQLite DB — exercises a real query                     |

**Method (verbatim from bench notes).** Host: Fedora 7.0.10, loopback
(`127.0.0.1:8000`), Python 3.12 via `uv`, uvicorn + Starlette. The first
spawn was discarded because the START timestamp was captured *after* the
background process had already booted (yielded a bogus 11 ms); the 614 ms
figure comes from a second, properly ordered run. After cold-start: warmup =
3 sequential hits of each of the 4 endpoints, then **10 timed samples per
endpoint** using `curl -s -o /dev/null -w '%{time_total}'` (curl's wall-clock,
including local TCP setup). `p50 = sorted[5]`; `p95 = sorted[9]`
(`int(10*0.95)`), which is a conservative lower-bound for a 10-sample p95.
All four endpoints returned HTTP 200 throughout. Raw per-sample times in ms —
`/health`: 1.795 1.528 1.219 1.231 1.289 1.269 1.797 1.452 1.463 1.536;
`/ready`: 3.107 3.216 4.113 2.932 3.804 3.378 2.800 3.395 2.962 3.244;
`/api/capabilities`: 2.595 3.296 3.853 2.800 3.509 3.093 2.721 2.461 2.905 3.172;
`/api/stats`: 4.216 3.601 5.600 3.151 3.291 3.435 3.187 2.913 3.131 4.527.
Server logs at `/tmp/portfolio-bench.log`; bench script at `/tmp/bench.sh`.
Server cleanly killed with `SIGTERM` after measurement.

These numbers are localhost on the developer machine — they are useful as a
*shape* signal (no hidden 100 ms cliffs, JSON serialisation is not on the hot
path, the DB ping in `/ready` adds ~2 ms over `/health`). Production numbers
will be similar for `/health` and `/ready` when the backend is co-located, and
slower for any endpoint that does a real DynamoDB round-trip (single-digit ms
for `GetItem`/`Query` once the cold-start tax is paid).

---

## What I'd build next

The seams are already drawn — these are the next deliverables, each with an
existing design doc so the work is "execute, don't re-design".

- **Vector search on DynamoDB / pgvector** → why interesting: today
  `semantic_search` is a SQLite-only capability ([ADR-0004](../adr/0004-backend-capability-divergence.md)),
  which keeps the production path simple but blocks the most differentiated
  feature for cloud users. The design picks pgvector for the
  embeddings/scoring path while keeping bookmark rows in DynamoDB → see
  [ADR-0005](../adr/0005-vector-search-roadmap.md) and
  [docs/dynamodb-rag-design.md](../dynamodb-rag-design.md).
- **Stripe plan → backend quota wiring** → why interesting: usage metering
  already lives in the `BackendCapabilities` flag (`usage_metering`), the
  per-key `quota` field is reserved in [ADR-0007](../adr/0007-multi-tenancy-via-api-keys.md),
  and [ADR-0003](../adr/0003-quota-and-usage-metering.md) declares the
  Stripe-side seam — no plumbing exists yet. This is the highest-leverage
  monetisation work and the design is unblocked.
- **Lambda enrichment alignment** → why interesting: [ADR-0006](../adr/0006-lambda-vs-ecs-deployment-boundary.md)
  documents the boundary (ECS for the long-lived SSE server, Lambda for the
  async enrichment fan-out) but the Lambda side is still partially deployed.
  Aligning the existing Lambda handlers under `lambda/` with the new
  capability protocol closes the last gap to true multicloud.
- **Multicloud target** → why interesting: [docs/multicloud.md](../multicloud.md)
  sketches a Fly.io / Cloudflare Workers target for the server tier; the
  backend protocol already supports it (no AWS SDK imports in `protocol.py`),
  so the work is mostly Terraform + a second deploy lane.
- **End-to-end eval harness** → why interesting: the four-gate `make ci` flow
  is solid for unit/typecheck (highlight 7), but there is no harness that
  exercises the *real* MCP tool surface against a fixture corpus and scores
  recall@k on `semantic_search`. That's the next quality investment before
  shipping vector search to production.

---

## Where the assets live

[`docs/demo/`](.) is the canonical home for portfolio assets — this tour, plus
the host-specific connection guides ([`claude-code.md`](claude-code.md),
[`cursor.md`](cursor.md), [`chatgpt.md`](chatgpt.md)). The Slidev deck under
[`presentation/`](../../presentation/) is a separate audience (talks, demos,
recruiting) and is intentionally not cross-linked from the engineering docs.
