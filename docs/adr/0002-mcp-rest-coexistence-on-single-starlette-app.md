# ADR-0002: MCP + REST coexistence on a single Starlette app

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Marco Gonzalez Junior
- **Related:** [`src/mcp_bookmarks/server.py`](../../src/mcp_bookmarks/server.py), [`docs/architecture.md`](../architecture.md), [`docs/api.md`](../api.md), [ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md)

## Context

Three transports talk to the same bookmark corpus:

1. **MCP SSE** at `/sse` — Claude Code, Cursor IDE, `mcp-remote`.
2. **MCP Streamable HTTP** at `/mcp` — ChatGPT custom connectors and any
   HTTP-native MCP client.
3. **REST** at `/api/*` — the bookmarklet, browser tools, CrewAI agents,
   and Stripe (`/webhooks/stripe`).

Each transport could be a separate service: three processes, three
container images, three Terraform targets. That's the conservative
microservices instinct. The reality of this codebase is that **all
three call the same backend instance via the same protocol**
([ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md)) and want
identical auth / tenant resolution / quota enforcement.

## Decision

We build **one Starlette app** in
[`server.py::create_combined_app`](../../src/mcp_bookmarks/server.py)
that mounts MCP SSE, MCP Streamable HTTP, and REST behind a shared
middleware stack:

```
GZip → Correlation → SecurityHeaders → CORS → BearerAuth
```

The combined app handles:
- `/sse` + `/messages/` (SSE GET + POST) from `FastMCP.sse_app()`.
- `/mcp` from `FastMCP.streamable_http_app()`.
- `/api/*` mounted from a sub-`Starlette` built in
  `create_api_app()`.
- `/webhooks/stripe`, `/bookmarklet`, `/ai-gateway`, `/health`,
  `/ready`, `/static/jetbrains-mono.woff2` registered as top-level
  routes.

Auth gates `/mcp`, `/sse`, `/messages` via `BearerAuthMiddleware`; the
REST routes use the static-key `TenantAuthMiddleware` mounted inside
the sub-app. Public paths (`/health`, `/ready`, `/`, `/bookmarklet`,
`/ai-gateway`, `/webhooks/stripe`) bypass auth.

## Consequences

- **Good:**
  - One container image, one Terraform `ecs.tf` definition, one set of
    alarms in [`terraform/alarms.tf`](../../terraform/alarms.tf). The
    Lighthouse work (GZip + security headers + correlation IDs) ships
    once and covers every transport.
  - Cross-transport invariants — tenant resolution, structured logging
    with `correlation_id`, the `unsupported`-envelope shape from
    [ADR-0004](0004-backend-capability-divergence.md) — are enforced by
    one middleware stack instead of three.
  - Local development is one process, one port, one log stream. The
    runbook's startup verification curl matrix
    ([`docs/runbook.md`](../runbook.md#startup-verification)) hits all
    three transports against `localhost:8000`.

- **Bad:**
  - SSE is sensitive to GZip; we rely on Starlette's
    `GZipMiddleware` auto-skipping `text/event-stream` (verified —
    see the Lighthouse PR's contract test). If a future framework
    upgrade changed that behavior we'd need a transport-aware
    bypass.
  - One process means one blast radius. A FastMCP bug that crashes the
    Streamable HTTP path also drops the SSE clients and the REST API.
    Mitigation: `/ready` proves the backend is reachable and ECS
    rolls bad tasks automatically.
  - Bearer auth is duplicated (per-transport JWT + scoped tokens for
    `/mcp` + `/sse`; static key for `/api/*`) because the two
    surfaces have different client populations. Documented in
    [`docs/api.md` § Authentication](../api.md#authentication).

- **Operational:**
  - The middleware order in `create_combined_app` is load-bearing —
    GZip must be outermost so it sees the final byte stream;
    Correlation must be next so every log record carries the
    correlation id; SecurityHeaders must precede CORS so preflight
    responses also get the security headers.

## Alternatives considered

- **Three services behind an ALB path-routing rule.** Considered for
  clean blast-radius isolation. Rejected because the three transports
  share more than they diverge (same backend, same tenant, same quota,
  same logs); splitting them would triple our Terraform surface and
  require cross-service auth replication for negligible benefit.
- **MCP-only server, REST as a separate Lambda.** Considered because
  the REST surface is mostly Stripe + bookmarklet, both episodic.
  Rejected because the bookmarklet path needs the same scraper /
  quota / tenant resolution as the MCP `save_bookmark` tool — pulling
  it into Lambda would just duplicate that logic.
- **Two Starlette apps in one process, mounted at different prefixes.**
  Considered as a smaller step. Rejected because Starlette's
  middleware composition doesn't cleanly compose across nested apps;
  having one root app with one middleware list is operationally
  simpler.

## References

- [`src/mcp_bookmarks/server.py`](../../src/mcp_bookmarks/server.py) —
  `create_combined_app()` is the entire integration point.
- [`src/mcp_bookmarks/correlation.py`](../../src/mcp_bookmarks/correlation.py),
  [`src/mcp_bookmarks/security_headers.py`](../../src/mcp_bookmarks/security_headers.py),
  [`src/mcp_bookmarks/bearer_auth.py`](../../src/mcp_bookmarks/bearer_auth.py) —
  the middleware that the combined app wires together.
- [`docs/architecture.md`](../architecture.md) — layered diagram.
- [`docs/api.md`](../api.md) — REST contract, error envelope, auth.
- [`docs/runbook.md`](../runbook.md) — per-transport verification commands.
