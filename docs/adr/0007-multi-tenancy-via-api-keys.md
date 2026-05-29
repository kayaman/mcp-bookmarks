# ADR-0007: Multi-tenancy via `MCP_API_KEYS` static key:org mapping

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Marco Gonzalez Junior
- **Related:** [`src/mcp_bookmarks/auth.py`](../../src/mcp_bookmarks/auth.py), [`src/mcp_bookmarks/bearer_auth.py`](../../src/mcp_bookmarks/bearer_auth.py), [`docs/api.md`](../api.md), [ADR-0002](0002-mcp-rest-coexistence-on-single-starlette-app.md)

## Context

mcp-bookmarks needs **tenant isolation** in two scenarios:

1. **REST `/api/*`** — the bookmarklet and any first-party tool the
   operator uses. The auth pattern needs to scale to multiple devices,
   ideally without each device knowing about Cognito user pools or
   JWKS rotation.
2. **MCP transports (`/sse`, `/mcp`, `/messages`)** — external MCP
   clients (Claude Code, Cursor, ChatGPT) and first-party browser/mobile
   clients that already have a Cognito JWT.

The full SaaS answer here is OAuth2 + per-tenant Cognito User Pools +
JWT rotation + scoped tokens. That's a meaningful amount of
infrastructure for what is, today, a single-organization-per-process
deployment model. We don't have multi-org SaaS customers; we have one
operator running the server for themselves and (optionally) a small
number of devices.

## Decision

We split the auth path by transport and pick the smallest mechanism
that meets the bar:

### REST `/api/*` — static `MCP_API_KEYS` with optional `key:org`

When `MCP_API_KEYS` is set, every `/api/*` request must carry a bearer
token in `Authorization: Bearer <key>` or `X-API-Key: <key>`.
[`auth.require_api_key`](../../src/mcp_bookmarks/auth.py) parses the
config string:

```
MCP_API_KEYS="devkey1,devkey2:tenant-7,devkey3:tenant-8"
```

Each entry is either `<key>` (resolves to tenant `"default"`) or
`<key>:<org-id>`. The resolved tenant lands on
`request.state.tenant_id` via `TenantAuthMiddleware` and scopes every
backend call. Tenant resolution order:

1. `request.state.tenant_id` (set by `TenantAuthMiddleware` or
   `BearerAuthMiddleware`)
2. `DYNAMODB_ORG_ID` env var
3. The literal string `"default"`

### MCP transports — Cognito JWT or `bm_v1_*` scoped tokens

When `MCP_BEARER_AUTH=true`,
[`BearerAuthMiddleware`](../../src/mcp_bookmarks/bearer_auth.py) accepts
either:

1. **Cognito ID tokens** — validated against the pool's JWKS via
   `COGNITO_USER_POOL_ID` + `COGNITO_CLIENT_ID`. Used by first-party
   browser/mobile clients.
2. **`bm_v1_*` scoped tokens** — opaque strings minted by an upstream
   provisioner, looked up by SHA-256 hash in `MCP_CONNECTIONS_TABLE`
   (default `mcp-bookmarks-connections`). Used by external MCP clients.

Both middlewares are documented in
[`docs/api.md` § Authentication](../api.md#authentication).

## Consequences

- **Good:**
  - The static-key path is one env var. A new device gets a new key,
    optionally with its own tenant suffix. No IAM, no Cognito, no
    OAuth dance.
  - Tenant scoping is the same for both paths: it lands on
    `request.state.tenant_id` and propagates to every backend call.
    Adding a new transport just needs middleware that sets that
    attribute.
  - The bearer auth surface for MCP transports is layered: Cognito for
    "I'm a real user" tokens, `bm_v1_*` for "I'm a programmatic agent
    with this scope" tokens. Each can be rotated independently.

- **Bad:**
  - `MCP_API_KEYS` lives in `Secrets Manager` and is read into env at
    ECS task startup. Rotating it requires task restart, not a
    runtime reload.
  - There's no admin UI; tenant configuration is operator-edited.
    Onboarding a new device is a one-line tfvars edit + `terraform
    apply` + task restart.
  - Bearer-auth `bm_v1_*` tokens require an upstream provisioner that
    isn't part of this repo. The `mcp-connections` DynamoDB table is
    optional (see
    [ADR-0001 — SQLite + DynamoDB dual-mode storage](0001-sqlite-dynamodb-dual-mode-storage.md));
    when absent, only Cognito tokens are accepted.

- **Operational:**
  - To verify auth end-to-end, follow the curl matrix in
    [`docs/runbook.md` § Startup verification](../runbook.md#startup-verification).
  - The static-key path returns `{"error": {"code": "unauthorized", ...}}`
    with the standard envelope on a missing/invalid key — same shape as
    every other error response, no special-casing.

## Alternatives considered

- **Full OAuth2 with per-tenant Cognito User Pools.** Considered as the
  "real" SaaS answer. Rejected for the static-key path because the
  current deployment model is single-organization-per-process; setting
  up a User Pool per tenant for the bookmarklet adds substantial
  AWS surface for no functional gain over a string in Secrets Manager.
  Cognito is in scope for MCP transports because those tokens already
  exist on first-party browser/mobile clients.
- **Single shared API key (no tenant suffix).** Considered for
  simplicity. Rejected because we wanted to support per-device or
  per-team isolation without redeploying — adding a `:tenant-id`
  suffix on a key is the minimum-viable multi-tenancy.
- **mTLS instead of bearer tokens.** Considered for low-overhead
  device-to-server auth. Rejected because the bookmarklet runs in a
  browser, which can't ship a client certificate. mTLS would
  bifurcate the auth path for no clear benefit.
- **Sessions via cookies instead of bearer tokens.** Considered for
  browser ergonomics. Rejected because the REST API is consumed by
  agents as well as browsers; bearer tokens work uniformly across
  both client types without `SameSite` / CSRF complications.

## References

- [`src/mcp_bookmarks/auth.py`](../../src/mcp_bookmarks/auth.py) —
  `require_api_key` parses `MCP_API_KEYS`, returns `(ok, tenant_id)`.
- [`src/mcp_bookmarks/bearer_auth.py`](../../src/mcp_bookmarks/bearer_auth.py) —
  Cognito JWT validation + `bm_v1_*` scoped-token lookup.
- [`docs/api.md` § Authentication + § Tenant resolution](../api.md#authentication) —
  full wire shape and example envelopes.
- [`docs/runbook.md`](../runbook.md) — verification commands.
- [ADR-0002 — MCP + REST coexistence on a single Starlette app](0002-mcp-rest-coexistence-on-single-starlette-app.md).
