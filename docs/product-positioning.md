# Product positioning

**Decision (April 2026):** This project follows a **hybrid** product boundary with a **vertical-first** focus.

## Vertical core (primary)

**Bookmark-native knowledge:** capture URLs, extract text, tag, summarize, and retrieve—via **MCP** (agents, Claude, Cursor) and optional **REST**. Semantic search today is **SQLite + OpenAI embeddings** per bookmark (no chunking). Differentiation is the **end-to-end link workflow**, not a generic document upload API.

## Horizontal extension (secondary, roadmap)

**RAG-style capabilities** beyond keyword search are intentionally scoped to **saved bookmarks** until a cloud vector pipeline exists for DynamoDB/blogmarks. A separate **HTTP retrieve/query API** (e.g. `POST /v1/rag/query`) may be added later using the same auth and usage patterns as [`auth.py`](../src/mcp_bookmarks/auth.py) and [`usage_meter.py`](../src/mcp_bookmarks/usage_meter.py); it is **not** implemented yet.

## What this excludes (for now)

- Arbitrary file corpora unrelated to bookmarks as a first-class product surface.
- Competing head-on with commodity “embed any PDF” APIs without the bookmark ingest story.

## Related documents

- [Production readiness](production-readiness.md) — what is wired vs what you must configure and verify.
- [DynamoDB RAG design](dynamodb-rag-design.md) — chunking, embeddings, and vector storage for cloud mode.
