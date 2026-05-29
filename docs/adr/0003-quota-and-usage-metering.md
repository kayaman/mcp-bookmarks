# ADR-0003: Quota and usage metering live in the active backend

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Marco Gonzalez Junior
- **Related:** [`src/mcp_bookmarks/usage_meter.py`](../../src/mcp_bookmarks/usage_meter.py), [`docs/api.md`](../api.md), [`docs/runbook.md`](../runbook.md), [ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md), [ADR-0004](0004-backend-capability-divergence.md)

## Context

Two questions need answers at every write boundary (REST `POST /api/save`,
MCP `save_bookmark`, every other state-changing tool):

1. *"Has this tenant exceeded their monthly quota?"* — if yes, return
   `429 rate_limited` with `{used, limit}` so the client can surface a
   specific message.
2. *"Record that this event happened."* — for billing reconciliation,
   capacity planning, and the `GET /api/usage` endpoint.

The naïve implementation puts a Redis counter in front of every
handler. We don't have Redis. We also don't want to introduce a third
storage system just for this (Redis as primary persistence would itself
need PITR / backups / IAM scoping).

## Decision

We **co-locate quota state with the active backend**. The
[`usage_meter`](../../src/mcp_bookmarks/usage_meter.py) module exposes
two top-level functions that dispatch on `_dynamo_usage_table()` to
pick the implementation:

- `check_quota_for_backend(db_path, tenant_id) → (ok, used, limit)`
- `record_usage_for_backend(db_path, event_type, tenant_id, metadata)`

The SQLite path reads/writes a `usage_events` table created by
`Database`'s schema. The DynamoDB path reads/writes the
`DYNAMODB_USAGE_TABLE` env-configured table; when that env var is
unset, quota checks **always pass** and usage recording is a no-op
(this is documented in
[`docs/production-readiness.md`](../production-readiness.md) — clouds
without a configured table get "no quota enforcement" rather than
"quota enforcement that silently doesn't work").

`MCP_MONTHLY_USAGE_LIMIT` controls the per-tenant ceiling globally;
when it's `0` (the default), all quota checks return ok regardless of
backend.

Handlers call `_check_rest_quota` (REST) or `_mcp_quota_block` (MCP) at
the top, then `_record_rest_usage` / `_mcp_record` after success.
Quota denials emit a single `quota_denied` structured log event
([`docs/runbook.md` § Canonical event names](../runbook.md#canonical-event-names))
with `tenant_id`, `used`, `limit`, and `surface`.

## Consequences

- **Good:**
  - One storage system per deployment. Laptop users get quota inside
    their SQLite file; cloud deployments get quota inside DynamoDB
    when they configure the optional table.
  - Quota state shares the same backup story as bookmarks themselves —
    SQLite snapshot or DynamoDB PITR — without an extra Redis instance
    to back up.
  - The capability flag `usage_metering`
    ([ADR-0004](0004-backend-capability-divergence.md)) communicates
    to clients that `GET /api/usage` works on this deployment; when
    `DYNAMODB_USAGE_TABLE` is unset, the endpoint returns a
    structured `forbidden` envelope instead of silently lying.

- **Bad:**
  - The DynamoDB path is **append-only count via filtered scan**, not
    a counter increment, because we wanted to keep
    `record_usage_for_backend` idempotent and avoid `UpdateItem`
    contention on a hot key. At very high write rates this becomes
    expensive; today the corpus is small enough that it doesn't
    matter.
  - There's no global rate-limit headroom — if `MCP_MONTHLY_USAGE_LIMIT
    = 5000`, the 5001st event is the one that gets rejected, with no
    smoothing. Clients should treat the 429 as a hard stop, not a
    backoff hint.
  - The `usage_meter._MONTHLY_LIMIT` constant is read at module import
    time, so unit tests need `monkeypatch.setattr(usage_meter,
    "_MONTHLY_LIMIT", N)` rather than env var manipulation. Documented
    in [`tests/integration/test_quota.py`](../../tests/integration/test_quota.py).

- **Operational:**
  - To verify quota end-to-end, follow the curl matrix in
    [`docs/runbook.md` § Quota verification](../runbook.md#quota-verification).
  - Disabling quota at runtime: set `MCP_MONTHLY_USAGE_LIMIT=0` and
    restart the task. Hot reload is not supported.

## Alternatives considered

- **Redis or DynamoDB counter table per tenant.** Considered for
  atomic increments and O(1) reads. Rejected for two reasons: (1)
  a global counter on a per-tenant key creates a hot partition under
  growth; (2) it introduces a third backup story (counters) that's
  orthogonal to bookmark durability.
- **Reset-on-month-boundary with a stored "month → count" record.**
  Considered to avoid the scan cost. Rejected because the existing
  scan-with-filter-on-`created_at` is fast at our scale and avoids the
  "did the boundary cron run?" failure mode.
- **Push usage to CloudWatch metrics instead of persisting it.**
  Considered to outsource the storage problem. Rejected because we
  need to read the count back via `GET /api/usage` for tenant-facing
  status — CloudWatch Metrics has a query API but it's slow and
  paginated.

## References

- [`src/mcp_bookmarks/usage_meter.py`](../../src/mcp_bookmarks/usage_meter.py) —
  full implementation; `check_quota_for_backend` and
  `record_usage_for_backend` are the public entry points.
- [`docs/api.md` § Rate limiting](../api.md#rate-limiting) — wire shape and
  envelope for `rate_limited`.
- [`docs/runbook.md` § Quota verification](../runbook.md#quota-verification) —
  reproduction recipe.
- [ADR-0001 — SQLite + DynamoDB dual-mode storage](0001-sqlite-dynamodb-dual-mode-storage.md).
- [ADR-0004 — Backend capability divergence](0004-backend-capability-divergence.md).
