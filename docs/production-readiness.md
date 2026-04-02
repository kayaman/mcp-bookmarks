# Production readiness audit

Static audit of this repository (April 2026). **blogmarks.dev** (PWA, API, Cognito, Lambdas) lives in **another repo and deploy pipeline**; validate that stack separately.

## MCP server (`uv run mcp-bookmarks`)

| Area | Behavior | Verify in prod |
|------|----------|----------------|
| **SQLite default** | Full tools including `index_bookmark_embedding` / `semantic_search_bookmarks` | `BOOKMARKS_DB_PATH` persistent volume if containerized |
| **DynamoDB mode** | Embedding tools return JSON errors; use `search_bookmarks` + `read_bookmark` | AWS creds, table names, `DYNAMODB_ORG_ID` if multi-tenant |
| **REST auth** | `MCP_API_KEYS` unset → `/api/*` open (dev default) | Set keys for any public host |
| **Monthly quota** | `MCP_MONTHLY_USAGE_LIMIT` > 0 enforces limits on MCP tools + some REST paths | **DynamoDB:** requires `DYNAMODB_USAGE_TABLE`; if unset, quota check **always passes** (see `check_quota_dynamo` in `usage_meter.py`) |
| **Usage events** | SQLite `usage_events` when not DynamoDB-only path; optional `DYNAMODB_USAGE_TABLE` for cloud | Inspect table or DB for expected `event_type` rows |
| **Stripe** | `POST /webhooks/stripe` verifies `Stripe-Signature` with `STRIPE_WEBHOOK_SECRET` | Dashboard webhook URL, secret, event types (`customer.subscription.*`) |
| **Subscriptions** | `subscription_store` writes SQLite and/or `DYNAMODB_SUBSCRIPTIONS_TABLE` | Confirm table/schema matches Terraform if used |
| **Ensemble** | `ENSEMBLE_ENABLED=false` by default | Enable only when gateway spend is acceptable |

## Code vs “product complete”

- **Billing:** Stripe webhook persists subscription snapshots; **plan → quota mapping** in application code is not a full SaaS entitlement engine—treat as **hooks + storage** unless you extend it.
- **Terraform:** Describes optional AWS pieces (ALB, ECS, Lambda, RDS, DynamoDB). **Apply state** in your account determines what is actually live, not this doc.
- **Lambda sample** in `terraform/`: README already warns schema may differ from production blogmarks unless aligned.

## Recommended smoke checks

1. **Local:** `uv run python tests/test_smoke.py` and `tests/test_api.py` (with env your CI uses).
2. **Hosted MCP:** Hit `GET /api/usage` with a valid API key after a few tool calls; count should increase.
3. **Stripe:** Send a test event from Stripe CLI or dashboard and confirm DB/Dynamo row updates.
4. **Quota:** With limit set and usage table configured (DynamoDB mode), exceed limit and confirm MCP returns `monthly_quota_exceeded`.

## RAG in production

- **SQLite:** Semantic search is only as durable as the DB file; no HA story in-app.
- **DynamoDB:** No vector index in this repo yet—see [dynamodb-rag-design.md](dynamodb-rag-design.md).
