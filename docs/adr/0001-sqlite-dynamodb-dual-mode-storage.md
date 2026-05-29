# ADR-0001: SQLite + DynamoDB dual-mode storage

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Marco Gonzalez Junior
- **Related:** [`src/mcp_bookmarks/backend.py`](../../src/mcp_bookmarks/backend.py), [`docs/architecture.md`](../architecture.md), [`docs/product-positioning.md`](../product-positioning.md), [`docs/dynamodb-rag-design.md`](../dynamodb-rag-design.md), [ADR-0004 — Backend capability divergence](0004-backend-capability-divergence.md)

## Context

mcp-bookmarks needs to serve two very different deployments without
forking the codebase:

1. **A single user on their laptop.** Zero cloud setup, zero dependencies
   beyond Python. The MCP server is started, Claude Code / Cursor
   connect, bookmarks get saved. The bottleneck is "minutes to first
   bookmark," not concurrent-user throughput.
2. **A multi-user hosted deployment.** ECS Fargate behind an ALB,
   per-tenant isolation, durable storage that survives task replacement,
   and operational hygiene (alarms, PITR, IAM).

The two extremes have almost no overlap in their storage requirements
beyond *"persist tags and bookmarks scoped to a tenant"*. A single
storage choice would either over-engineer the laptop case (force
DynamoDB on a single user) or under-deliver the cloud case (ship SQLite
on a Fargate task with no shared state).

## Decision

We ship **two backends behind one `BookmarkBackend` protocol**
([`src/mcp_bookmarks/backend.py`](../../src/mcp_bookmarks/backend.py)):

- **SQLite via `aiosqlite`** — default. Selected when `DYNAMODB_MODE` is
  unset or false. Single-file DB at `~/.mcp-bookmarks/bookmarks.db`.
  Full feature set including OpenAI-embedded semantic search.
- **DynamoDB via `boto3`** — opt-in. Selected when
  `DYNAMODB_MODE=true`. Multi-table, tenant-scoped, per-request user
  isolation. Vector pipeline deferred (see
  [ADR-0005](0005-vector-search-roadmap.md)).

Both concrete classes (`Database`, `DynamoDBDatabase`) declare a
`capabilities: BackendCapabilities` attribute and conform to
`BookmarkBackend` at runtime via `@runtime_checkable`. The application
layer never imports the concrete classes; it accepts the protocol type
and consults `capabilities` for divergence (see
[ADR-0004](0004-backend-capability-divergence.md)).

## Consequences

- **Good:**
  - A new contributor clones, runs `uv sync && uv run mcp-bookmarks`,
    and is saving bookmarks in under a minute. No AWS account, no
    boto3 credentials, no Docker.
  - The hosted path is genuinely production-shaped: PITR on every
    DynamoDB table, per-request user filtering, IAM role per ECS task,
    alarms wired in [`terraform/alarms.tf`](../../terraform/alarms.tf).
  - The protocol gives mypy/pyright a single name to type against, and
    a reviewer a single file to read for the contract.

- **Bad:**
  - Two `upsert_bookmark` implementations to keep aligned. The shared
    contract test pins the method names but signatures can drift on
    keyword-only arguments (DynamoDB accepts `bookmark_type`, `flow_id`,
    `source`; SQLite doesn't).
  - The canonical wire shape is camelCase OG keys (`ogTitle`,
    `aiContent`, …) because that's what the DynamoDB path emits; SQLite
    speaks snake_case internally and converts at the edges. Easy to
    forget which side you're on.
  - Tests need both code paths to stay covered — see
    [`tests/integration/test_database.py`](../../tests/integration/test_database.py)
    and the mocked-boto3 portions of
    [`tests/unit/test_schema_compat.py`](../../tests/unit/test_schema_compat.py).

- **Operational:**
  - SQLite deployments need a persistent volume mounted at
    `BOOKMARKS_DB_PATH`; an ephemeral container loses every bookmark on
    restart.
  - DynamoDB deployments need the four table ARNs in the ECS task role
    (links, tags, usage_events, subscriptions) and PITR enabled —
    documented in [`docs/infra.md`](../infra.md).
  - The capability matrix on
    [`/api/capabilities`](../api.md#endpoints) reports which backend is
    live; clients should branch on that, not on `DYNAMODB_MODE`.

## Alternatives considered

- **DynamoDB only, with a local-DDB Docker image for dev.** Considered
  because it would collapse to one code path. Rejected: forcing every
  laptop user to run a Java-backed `dynamodb-local` container raises the
  bar for "I just want to try the MCP" by an order of magnitude, and
  the surface area we'd save (one backend file) is small.
- **SQLite only, with `litestream` for cloud durability.** Considered
  because litestream + S3 is operationally elegant. Rejected: the
  hosted offering also needs multi-task ECS deployments, and a single
  SQLite file pinned to one task doesn't let us horizontally scale the
  Fargate count past 1 without sticky-session ALB rules.
- **Postgres for both.** Considered because pgvector would unblock
  semantic search in cloud mode. Rejected for the laptop case: requires
  a running Postgres process, which kills the "open the terminal and
  go" experience. We leave Postgres available for vector storage only
  (see [`docs/dynamodb-rag-design.md`](../dynamodb-rag-design.md)) where
  it earns its keep.

## References

- [`src/mcp_bookmarks/backend.py`](../../src/mcp_bookmarks/backend.py) —
  `BookmarkBackend` protocol, `BackendCapabilities` dataclass,
  `SQLITE_CAPABILITIES`, `DYNAMODB_CAPABILITIES`.
- [`src/mcp_bookmarks/db.py`](../../src/mcp_bookmarks/db.py) — SQLite
  implementation; migrations in `Database._migrate`.
- [`src/mcp_bookmarks/dynamodb.py`](../../src/mcp_bookmarks/dynamodb.py) —
  DynamoDB implementation; camelCase canonical shape in `_to_bookmark`.
- [`docs/architecture.md`](../architecture.md) — layered diagram, capability
  matrix.
- [`docs/product-positioning.md`](../product-positioning.md) — why we keep
  the vertical-first hybrid framing.
- [ADR-0004 — Backend capability divergence](0004-backend-capability-divergence.md).
- [ADR-0005 — Vector search roadmap](0005-vector-search-roadmap.md).
