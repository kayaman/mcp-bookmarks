# DynamoDB / cloud RAG design (bookmark vertical)

Design target: **semantic retrieval over stored bookmark text** (`aiContent` / full article body) in **DynamoDB mode**, aligned with [blogmarks](https://blogmarks.dev) item shape. Implementation is **future work**; this document fixes choices for when you build it.

## Goals

- **Tenant isolation:** Partition by `orgId` (or equivalent) consistently with [`dynamodb.py`](../src/mcp_bookmarks/dynamodb.py).
- **Model versioning:** Store embedding model id and a **content hash** so re-embed only when text or model changes.
- **Scale:** Avoid full-table cosine in Python (unlike current SQLite path).

## Chunking

| Parameter | Suggested default | Notes |
|-----------|-------------------|--------|
| **Unit** | Paragraph-aware splits | Prefer `\n\n` boundaries; fall back to fixed windows inside long paragraphs. |
| **Target size** | ~800–1,200 tokens (or ~3–4k characters) | Stay under embedding model context (e.g. 8k for `text-embedding-3-small` input). |
| **Overlap** | ~100–200 tokens | Improves recall across chunk boundaries. |
| **Cap** | Max chunks per bookmark (e.g. 50) | Protects cost on huge pages; truncate or sample with explicit metadata. |

**Metadata per chunk:** `bookmarkId`, `orgId`, `chunkIndex`, `url`, `title` (optional), `contentSha256`, `embedModel`, `createdAt`.

## Embedding model

- **Default:** Same as local mode: `text-embedding-3-small` via `OPENAI_EMBED_MODEL` (see [`rag.py`](../src/mcp_bookmarks/rag.py)).
- **Record** `embedModel` on every vector row for migrations and re-index jobs.
- **Batching:** OpenAI embeddings API accepts multiple inputs; batch chunks per bookmark (respect token limits per request).

## Vector storage options

Choose one primary store (hybrid search can combine later).

1. **PostgreSQL + pgvector** (Terraform already mentions RDS pgvector-ready)  
   - **Pros:** SQL, joins with subscription/tenant tables, familiar ops.  
   - **Cons:** Another service to run and connect from MCP/ECS.

2. **OpenSearch Serverless / managed OpenSearch with k-NN**  
   - **Pros:** Purpose-built ANN, scales with document count.  
   - **Cons:** Cost, configuration; wire IAM from ECS/Lambda.

3. **Managed vector DB** (Pinecone, Weaviate Cloud, etc.)  
   - **Pros:** Fast to adopt, good ANN.  
   - **Cons:** Extra vendor; sync from DynamoDB is your responsibility.

4. **DynamoDB-only (not recommended for ANN at scale)**  
   - Store chunk metadata + **base64 vector** or **S3** payload: workable only for small corpora; queries devolve to scan or heavy GSIs without native similarity.

**Recommendation for blogmarks scale:** **pgvector** or **OpenSearch k-NN** with a single write path after `aiContent` is persisted.

## Index schema sketch (pgvector)

- Table `bookmark_chunks`: `id`, `tenant_id`, `bookmark_id`, `chunk_index`, `content_preview`, `embedding vector(1536)`, `embed_model`, `content_hash`, `updated_at`.  
- Index: IVFFlat or HNSW on `embedding` (per Postgres/pgvector version).  
- **Query:** `embedding <=> $query_vector` ORDER BY distance LIMIT k, filtered by `tenant_id`.

## Sync and backfill

1. **On write:** After successful `set_bookmark_body` / `extract_content` persistence to DynamoDB, enqueue **index job** (SQS + worker, or async Lambda) with `bookmarkId` + `contentVersion`/`hash`.  
2. **Backfill:** Batch scan links table (per org), filter items with `aiContent`, enqueue missing or stale hashes.  
3. **Delete:** On bookmark delete, remove all chunks for `bookmark_id` (tombstone or cascade).

## MCP / API surface (future)

- **`semantic_search_bookmarks`:** In DynamoDB mode, call vector store with tenant filter instead of returning “SQLite only” error.  
- **Optional REST:** `POST /api/rag/search` with `{ "query": "...", "limit": 8 }` returning citations `title`, `url`, `snippet`, `score`—same auth as `/api/*`.

## Cost controls

- Dedupe by `content_hash` before embedding.  
- Respect existing **`MCP_MONTHLY_USAGE_LIMIT`** and record **`embedding_index`** (or similar) in usage events.  
- Optional: max embeddings per tenant per day in application config.
