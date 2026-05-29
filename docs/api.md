# REST API reference

All routes are mounted at `/api/*`. Two routes (`/bookmarklet`,
`/webhooks/stripe`) live at the app root.

> Status: introduces the WDN-396 / OSS-6 contract — standardized error
> envelope + Pydantic request validation across every write endpoint.
> OpenAPI schema generation is deferred to a follow-up PR.

## Authentication

Two mechanisms run in parallel; they can be combined.

### 1. Static API keys (`MCP_API_KEYS`)

When `MCP_API_KEYS` is set, every `/api/*` request must carry a bearer
token in `Authorization: Bearer <key>` or `X-API-Key: <key>`. The key may
be plain (`devkey1`) or scoped to a tenant (`devkey2:org-id`). The tenant
component lands on `request.state.tenant_id` and scopes every read/write
to that tenant in both backends.

Unauthenticated requests get a `401 unauthorized` error envelope.

### 2. Bearer auth for `/mcp`, `/sse`, `/messages` (`MCP_BEARER_AUTH`)

When `MCP_BEARER_AUTH=true`, the SSE and Streamable HTTP transports also
require a bearer token. Two token kinds are accepted:

- **Cognito ID tokens** — when `COGNITO_USER_POOL_ID` + `COGNITO_CLIENT_ID`
  are configured, JWTs are validated against the pool's JWKS.
- **`bm_v1_*` scoped tokens** — opaque strings minted by an upstream
  provisioner; looked up by SHA-256 hash in the `MCP_CONNECTIONS_TABLE`
  DynamoDB table (default `mcp-bookmarks-connections`).

`MCP_BEARER_AUTH` does **not** gate `/api/*` (which uses static keys
above) or public paths (`/health`, `/`, `/bookmarklet`, `/ai-gateway`,
`/webhooks/stripe`).

## Tenant resolution

Tenant id is resolved in this order:

1. `request.state.tenant_id` (set by `TenantAuthMiddleware` after a
   successful API-key match, or by `BearerAuthMiddleware`).
2. `DYNAMODB_ORG_ID` environment variable.
3. The literal string `"default"`.

The resolved tenant is propagated to every backend call so cross-tenant
reads are impossible by construction. See
[`docs/architecture.md`](architecture.md) for layer boundaries and
[`docs/product-positioning.md`](product-positioning.md) for the multi-tenant
scope.

## Error envelope

Every non-2xx response uses a single shape:

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Monthly usage quota exceeded for this tenant",
    "details": {"used": 5000, "limit": 5000}
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `code` | string (from the enum below) | **Stable** — clients branch on this |
| `message` | string | Human-readable; freely re-worded across versions |
| `details` | object \| absent | Optional structured context; absent when there's nothing useful to add |

### Error-code taxonomy

| `code` | HTTP | When |
|---|---|---|
| `unauthorized` | 401 | Missing or invalid bearer / API key |
| `forbidden` | 403 | Authenticated but the operation is not allowed (e.g. ensemble disabled) |
| `not_found` | 404 | Bookmark, tag, or resource does not exist (or not visible to this tenant) |
| `invalid_request` | 400 | Body well-formed but content rejected (e.g. duplicate tag slug in `tag_bookmark`) |
| `invalid_json` | 400 | Body could not be parsed as JSON |
| `validation_error` | 422 | Pydantic rejected the body shape; `details.fields` lists the offending paths |
| `conflict` | 409 | Idempotency / duplicate-key collision (e.g. `POST /api/tag` with an existing slug) |
| `rate_limited` | 429 | `MCP_MONTHLY_USAGE_LIMIT` exceeded for this tenant |
| `invalid_signature` | 400 | `/webhooks/stripe` payload did not verify against `STRIPE_WEBHOOK_SECRET` |
| `internal_error` | 500 | Unexpected failure (not raised explicitly by handlers — reserved for the fallback middleware) |
| `service_unavailable` | 503 | Required dependency unwired (e.g. `STRIPE_WEBHOOK_SECRET` not set) |

Validation-error `details.fields` is a list of
`{"loc": "field.path", "type": "missing", "message": "required"}`
entries.

## Endpoints

### `POST /api/save` — save a URL

**Body (JSON):**

```json
{"url": "https://example.com/article", "title": "optional override"}
```

`Content-Type: application/x-www-form-urlencoded` is also accepted for
the bookmarklet (`url=<value>`). Query-string `?url=` works as a final
fallback.

**Response (200):**

```json
{
  "status": "saved",
  "bookmark_id": 42,
  "title": "Article Title",
  "description": "OG description",
  "word_count": 1247,
  "message": "Saved! Connect via MCP to tag and summarize."
}
```

Failures: `invalid_request` (no URL), `validation_error` (bad URL shape),
`rate_limited`, `unauthorized` (when API keys configured).

### `GET /api/stats` — corpus stats

```json
{"total_bookmarks": 42, "total_tags": 17}
```

### `GET /api/bookmarks` — list / search

Query params: `query` (text), `tag` (slug), `limit` (default 20).

### `GET /api/bookmarks/{id}` — one bookmark with content

Failures: `not_found`. Large content (>400 kB) is truncated and the
response carries `content_truncated: true`.

### `POST /api/tag` — create a tag

**Body:**

```json
{"slug": "machine-learning", "name": "Machine Learning", "description": "..."}
```

**Response (201):**

```json
{"created": {"slug": "...", "name": "...", "description": "..."}}
```

Failures: `validation_error` (missing/empty slug or name),
`conflict` (slug already exists in this tenant), `rate_limited`.

### `POST /api/bookmarks/{id}/summary` — store a summary

**Body:** `{"summary": "..."}`. Failures: `validation_error`,
`not_found`, `rate_limited`.

### `POST /api/bookmarks/{id}/tags` — assign tags

**Body:** `{"tag_slugs": ["a", "b"]}`. Failures: `validation_error`,
`not_found`, `invalid_request` (unknown tag slug), `rate_limited`.

### `GET /api/tags` — list all tags

Tags are scoped to the authenticated tenant.

### `GET /api/usage` — monthly usage counter

```json
{
  "tenant_id": "...",
  "events_this_month": 1234,
  "monthly_limit": 5000,
  "limit_enforced": true
}
```

### `POST /api/ensemble` — multi-model + LLM judge (experimental)

Requires `ENSEMBLE_ENABLED=true`. **Body:**

```json
{"task": "...", "models": ["a", "b"], "judge_model": "..."}
```

Failures: `forbidden` (disabled), `validation_error`, `rate_limited`. See
[`docs/ai-gateway-ensemble.md`](ai-gateway-ensemble.md).

### `GET /api/ai-gateway/status` — gateway metadata

Returns safe metadata only (no secrets) for the
`/ai-gateway` browser dashboard.

### `POST /webhooks/stripe` — billing webhook

Verifies `Stripe-Signature` against `STRIPE_WEBHOOK_SECRET`. Failures:
`service_unavailable` (no secret), `invalid_signature`, `invalid_json`.
On success, returns `{"received": true, "type": "..."}` and persists
subscription state.

## Rate limiting

When `MCP_MONTHLY_USAGE_LIMIT > 0`, every write endpoint and most MCP
tools call `usage_meter.check_quota_for_backend(...)` before executing.
Over-quota requests return `429 rate_limited` with `details: {used,
limit}` so clients can surface a precise message.

Usage rows are stored in the SQLite `usage_events` table by default; set
`DYNAMODB_USAGE_TABLE` to route them to DynamoDB instead. See
[`src/mcp_bookmarks/usage_meter.py`](../src/mcp_bookmarks/usage_meter.py).

## Boundary-adapter logging

The scraper (`scraper.py`) is the one boundary adapter that intentionally
swallows broad exceptions — the `POST /api/save` path falls back to an
empty `OGMetadata` and `word_count=0` when the upstream URL is unreachable
or unparseable. Failures are emitted via the standard `logging` module
(`og_metadata_extraction_failed`, `article_extraction_failed`) so they
surface in any structured-logging setup without short-circuiting the save.

## Related documents

- [`docs/architecture.md`](architecture.md) — layer boundaries + `BookmarkBackend` protocol
- [`docs/production-readiness.md`](production-readiness.md) — wired vs. unwired in production
- [`docs/production-smoke.md`](production-smoke.md) — smoke-test commands
- [`docs/dynamodb-rag-design.md`](dynamodb-rag-design.md) — design behind the DynamoDB semantic-search gap
