# ADR-0005: Vector search roadmap

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Marco Gonzalez Junior
- **Related:** [`docs/dynamodb-rag-design.md`](../dynamodb-rag-design.md), [`src/mcp_bookmarks/rag.py`](../../src/mcp_bookmarks/rag.py), [ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md), [ADR-0004](0004-backend-capability-divergence.md)

## Context

Semantic search over the bookmark corpus is a differentiator: the wedge
in [`docs/product-positioning.md`](../product-positioning.md) is
"agent-addressable memory," not "another keyword search box." Two
constraints made the implementation non-uniform:

1. The SQLite path is small enough that we can compute an OpenAI
   embedding per bookmark, store the vector as JSON in a
   `bookmark_embeddings` table, and do cosine similarity in Python at
   query time. Total fixed cost. Negligible latency at thousands of
   rows.
2. The DynamoDB path is multi-tenant, multi-task, and needs ANN
   (approximate nearest neighbor) at scale. DynamoDB itself has no
   native vector type or k-NN index. The right answer is a sidecar
   vector store (pgvector or OpenSearch k-NN) populated from the
   DynamoDB stream — meaningful infrastructure work.

Shipping cloud semantic search alongside the rest of the cloud path
would have doubled the scope of every cloud PR. Shipping it later means
the DynamoDB capability matrix has a permanent `semantic_search: False`
until we do.

## Decision

We ship **full semantic search on SQLite today**; the **cloud vector
pipeline is design-stage with implementation deferred**.

- **Today, SQLite path:** `index_bookmark_embedding` and
  `semantic_search_bookmarks` MCP tools exist
  ([`src/mcp_bookmarks/server.py`](../../src/mcp_bookmarks/server.py)).
  Embedding model is `text-embedding-3-small` via `OPENAI_API_KEY`;
  helpers in [`src/mcp_bookmarks/rag.py`](../../src/mcp_bookmarks/rag.py).
  The vectors live in the same SQLite file as the bookmarks.
- **Today, DynamoDB path:** Both tools return the standard
  `unsupported` envelope via `require_capability(db, "semantic_search")`
  (see [ADR-0004](0004-backend-capability-divergence.md)). Cloud
  callers get keyword search via `search_bookmarks` — a working
  fallback.
- **Future, DynamoDB path:** When we build the cloud vector pipeline,
  the design lives in
  [`docs/dynamodb-rag-design.md`](../dynamodb-rag-design.md). The
  shortlist is:
  - **pgvector on RDS** — already provisioned in
    [`terraform/rds.tf`](../../terraform/rds.tf) for this reason;
    chunking and embedding model versioning per chunk; cosine via
    `<=>` operator.
  - **OpenSearch Serverless / managed OpenSearch with k-NN** — better
    ANN at scale, more configuration to write, additional vendor.

Triggering the build: when "semantic search on cloud" becomes a top-three
user request, we ship pgvector with a single backfill Lambda from the
DynamoDB stream. The capability flag flips to `True` and clients
automatically pick up the tools.

## Consequences

- **Good:**
  - The SQLite path delivers a real semantic-search feature today, with
    no async indexing infrastructure. The local single-user experience
    sells the product.
  - The capability divergence is honest: clients see
    `semantic_search: false` on DynamoDB and don't waste a request
    expecting it to work.
  - The design doc is already half-written
    ([`docs/dynamodb-rag-design.md`](../dynamodb-rag-design.md)) so the
    eventual implementation has a head start.

- **Bad:**
  - Marketing copy that says "semantic search over your bookmarks"
    needs a footnote on the cloud path until this lands. The
    capability matrix on `/api/capabilities` is the source of truth;
    docs that don't link to it can drift.
  - SQLite vectors as JSON blobs aren't pretty. At ~50,000 bookmarks
    the index file grows substantially and Python-side cosine over the
    full corpus per query starts to feel slow (>500ms). That's our
    scaling ceiling on the SQLite path.

- **Operational:**
  - RDS pgvector is provisioned but **not in active use**. Operators
    can keep `enable_lambda_processor=false` and pay only for the
    `db.t4g.micro` instance; full operational cost when we wire the
    pipeline is documented in
    [`docs/infra.md`](../infra.md).
  - When a cloud user asks "where's semantic search?" — the answer is
    `GET /api/capabilities`, which tells them, plus this ADR which
    tells them why and when.

## Alternatives considered

- **Build pgvector now.** Considered for capability parity. Rejected:
  not yet justified by user demand, and shipping under-tested
  cloud-RAG code is worse than shipping no cloud-RAG code. The
  capability flag is honest about the gap.
- **Postgres-only repo (no DynamoDB).** Considered to collapse the
  vector question. Rejected for the same reasons in
  [ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md) — the laptop
  user experience would suffer.
- **Pinecone or Weaviate Cloud as the vector store.** Considered for
  fast time-to-implementation. Rejected because the operational
  surface (vendor relationship, billing, IAM-equivalent access
  control, sync drift from DynamoDB) adds substantial complexity for
  a benefit pgvector already covers on RDS we already have.

## References

- [`docs/dynamodb-rag-design.md`](../dynamodb-rag-design.md) — full
  design: chunking strategy, embedding model versioning, vector store
  shortlist, index sketch.
- [`src/mcp_bookmarks/rag.py`](../../src/mcp_bookmarks/rag.py) — embedding
  helpers; `embed_texts`, `cosine_similarity`, `embed_model`.
- [`src/mcp_bookmarks/server.py`](../../src/mcp_bookmarks/server.py) —
  `index_bookmark_embedding`, `semantic_search_bookmarks` (both behind
  `require_capability("semantic_search")`).
- [`terraform/rds.tf`](../../terraform/rds.tf) — the RDS instance
  provisioned in anticipation of pgvector.
- [ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md).
- [ADR-0004](0004-backend-capability-divergence.md).
