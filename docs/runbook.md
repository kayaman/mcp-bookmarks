# Operational runbook

> Status: introduces the WDN-397 / OSS-7 baseline. Pairs with
> [`docs/api.md`](api.md), [`docs/infra.md`](infra.md), and
> [`docs/production-readiness.md`](production-readiness.md).

## Health + readiness

| Endpoint | Purpose | Suitable for |
|---|---|---|
| `GET /health` | **Liveness** — returns 200 as long as the process is up | ECS task health, k8s livenessProbe |
| `GET /ready`  | **Readiness** — returns 200 only when the active backend is reachable; 503 with `{status, reason}` on failure | ALB target health, k8s readinessProbe |

Wire the ALB target group's health check to `GET /ready` (not `/health`)
so a backend outage drains the task instead of black-holing requests.

Correlation IDs are echoed back via `X-Correlation-ID`. Inject your own
to thread the id across multiple services:

```bash
curl -s -H 'X-Correlation-ID: my-debug-trace' https://$HOST/api/stats
# → response carries X-Correlation-ID: my-debug-trace
# → every log line under this request carries correlation_id="my-debug-trace"
```

## Structured logs

`LOG_FORMAT` controls the wire shape:

| Value | When used | Output |
|---|---|---|
| `json`   | `ENV=prod` (auto) or explicit | One JSON object per record on stdout |
| `pretty` | `ENV=dev` (auto) or explicit | Human-readable `HH:MM:SS LEVEL logger [cid=...] message  (extras...)` |

`LOG_LEVEL` (default `INFO`) tunes the root logger.

### Canonical event names

The server emits these named events with structured `extra` fields. Grep
your CloudWatch / Loki / Datadog for these in incident triage:

| Event | When | Key fields |
|---|---|---|
| `backend_initialized` | At server lifespan startup | `backend`, `tenant_id`, `db_path` |
| `backend_shutdown` | At server lifespan teardown | `backend` |
| `ready_check_failed` | `GET /ready` returned 503 | `reason` (`open_timeout`, `open_error`, `ping_error`), `error` |
| `quota_denied` | Tenant exceeded `MCP_MONTHLY_USAGE_LIMIT` | `tenant_id`, `used`, `limit`, `surface` (`rest` / `mcp`) |
| `og_metadata_extraction_failed` | Scraper boundary fallback in `POST /api/save` | `url`, `error` |
| `article_extraction_failed` | Scraper boundary fallback after OG | `url`, `error` |
| `stripe_webhook_processed` | Signature-verified Stripe event handled | `type`, `customer_id`, `status`, `plan` |
| `stripe_webhook_ignored` | Event arrived but no action taken | `type`, `reason` |

Every record additionally carries `level`, `logger`, `ts`, and
`correlation_id` from the inbound request.

## Startup verification

After `terraform apply` or a container redeploy:

```bash
HOST="<your-mcp-host>"
KEY="$MCP_API_KEY"

# 1. Liveness — proves the process boot
curl -fsS "https://$HOST/health"
# → {"status":"ok"}

# 2. Readiness — proves the backend is reachable
curl -fsS "https://$HOST/ready"
# → {"status":"ready"}

# 3. Capabilities — confirms which backend is active
curl -fsS -H "Authorization: Bearer $KEY" "https://$HOST/api/capabilities"
# → {"backend":"dynamodb","capabilities":{...}}

# 4. Stats — proves the DB actually answers a real query
curl -fsS -H "Authorization: Bearer $KEY" "https://$HOST/api/stats"
# → {"total_bookmarks":...,"total_tags":...}
```

If any of these fail, check the named event in the previous section to
narrow the failure mode before paging.

## Quota verification

```bash
# Set a tight limit locally
MCP_MONTHLY_USAGE_LIMIT=2 \
  uv run mcp-bookmarks &

# Burn two save events
curl -s -X POST "http://localhost:8000/api/save" -d 'url=https://example.com/a'
curl -s -X POST "http://localhost:8000/api/save" -d 'url=https://example.com/b'

# Third should 429 with the standard envelope
curl -sv -X POST "http://localhost:8000/api/save" -d 'url=https://example.com/c'
# → HTTP/1.1 429
# → {"error":{"code":"rate_limited","message":"Monthly usage quota exceeded for this tenant","details":{"used":2,"limit":2}}}
```

The corresponding log record:

```json
{"level":"WARNING","logger":"mcp_bookmarks.api","message":"quota_denied",
 "tenant_id":"default","used":2,"limit":2,"surface":"rest","correlation_id":"..."}
```

## Stripe verification

```bash
# Signature failure (no STRIPE_WEBHOOK_SECRET configured)
curl -sv -X POST "https://$HOST/webhooks/stripe" -d '{}'
# → 503  {"error":{"code":"service_unavailable",...}}

# Signature failure (bad sig)
curl -sv -X POST "https://$HOST/webhooks/stripe" \
  -H 'Stripe-Signature: t=1,v1=deadbeef' -d '{}'
# → 400  {"error":{"code":"invalid_signature",...}}

# A real event (Stripe CLI is the cleanest path)
stripe trigger customer.subscription.created --forward-to "https://$HOST/webhooks/stripe"
# → 200  {"received":true,"type":"customer.subscription.created"}
# → log: stripe_webhook_processed{type=...,customer_id=...,plan=...}
```

## Backend failure handling

### DynamoDB throttle

- Alarm: `${prefix}-ddb-<table>-read-throttle` /
  `${prefix}-ddb-<table>-write-throttle` (see `terraform/alarms.tf`).
- Diagnosis: `GET /ready` may still return 200 (one trivial GetItem
  succeeds); inspect CloudWatch metric `ReadThrottleEvents` /
  `WriteThrottleEvents` for the table named in the alarm.
- Action: the tables ship as `PAY_PER_REQUEST` (no provisioned capacity
  knob), so throttles indicate a per-second adaptive limit. Either pace
  the caller (back off + retry) or split the workload across tables.

### Backend unreachable at startup

- Alarm: `${prefix}-alb-target-5xx` fires when `/ready` flips a target to
  unhealthy and the next request 502/503s.
- Diagnosis: ECS task logs will show one of:
  - `ready_check_failed{reason=open_timeout}` — `connect()` exceeded 3s
  - `ready_check_failed{reason=open_error,error=...}` — explicit fail
  - `ready_check_failed{reason=ping_error,error=...}` — `get_stats()` failed
- Action: for DynamoDB mode, verify the task role has the table ARNs in
  `terraform/ecs.tf`. For SQLite mode, verify the persistent volume is
  mounted at `BOOKMARKS_DB_PATH`.

### OpenAI gateway down (ensemble + embeddings)

- These code paths are gated by `ENSEMBLE_ENABLED` and `OPENAI_API_KEY` —
  outages return `forbidden` / `service_unavailable` envelopes, not 500s.
- Action: leave `ENSEMBLE_ENABLED=false` and skip
  `index_bookmark_embedding` / `semantic_search_bookmarks` until the
  gateway recovers. Core save/search paths are unaffected.

## Deploy + rollback

1. **Image push**: `docker push <ecr>:vX.Y.Z`.
2. **Apply**: `terraform apply -var='mcp_container_image=<ecr>:vX.Y.Z' ...`
3. **Verify**: run the [Startup verification](#startup-verification) block
   against the new task once the ALB target is healthy.
4. **Rollback**: re-apply with the previous image URI. ECS will roll the
   task definition back; no state migration is needed (DynamoDB / RDS
   schemas are append-only at this layer).
5. **DLQ check**: after rollback, drain `${prefix}-dlq` so the
   `${prefix}-lambda-dlq-not-empty` alarm clears.

## Related documents

- [`docs/architecture.md`](architecture.md) — application-layer boundaries
- [`docs/infra.md`](infra.md) — cloud topology, trust boundaries, alarm thresholds
- [`docs/api.md`](api.md) — REST contract + error envelope + auth
- [`docs/production-readiness.md`](production-readiness.md) — what's wired vs unwired
- [`docs/production-smoke.md`](production-smoke.md) — automated post-deploy curl matrix
