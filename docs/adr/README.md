# Architecture Decision Records

Conscious decisions about how mcp-bookmarks is put together — the *why*
behind choices a reader would otherwise have to reverse-engineer from
code or git archeology.

Each ADR is short, dated, and final once accepted. New context goes in a
**new** ADR that supersedes the old one (link them via the `Status:`
field). The template lives at [`0000-template.md`](0000-template.md).

## Index

| # | Title | Status | Date |
|---|---|---|---|
| [0001](0001-sqlite-dynamodb-dual-mode-storage.md) | SQLite + DynamoDB dual-mode storage | Accepted | 2026-05-29 |
| [0002](0002-mcp-rest-coexistence-on-single-starlette-app.md) | MCP + REST coexistence on a single Starlette app | Accepted | 2026-05-29 |
| [0003](0003-quota-and-usage-metering.md) | Quota and usage metering live in the active backend | Accepted | 2026-05-29 |
| [0004](0004-backend-capability-divergence.md) | Backend capability divergence as a typed flag set | Accepted | 2026-05-29 |
| [0005](0005-vector-search-roadmap.md) | Vector search roadmap (SQLite today, cloud deferred) | Accepted | 2026-05-29 |
| [0006](0006-lambda-vs-ecs-deployment-boundary.md) | Lambda enrichment is a sample template; ECS is production | Accepted | 2026-05-29 |
| [0007](0007-multi-tenancy-via-api-keys.md) | Multi-tenancy via `MCP_API_KEYS` static key:org mapping | Accepted | 2026-05-29 |

## How to add a new ADR

1. Copy [`0000-template.md`](0000-template.md) to
   `NNNN-kebab-case-title.md`, where `NNNN` is the next free number.
2. Fill in **Context → Decision → Consequences → Alternatives →
   References**. Keep it under ~200 lines; cross-link existing docs
   instead of repeating their content.
3. Status: `Proposed` while under discussion, `Accepted` once merged. If
   a new ADR overrides a prior one, set the prior one's status to
   `Superseded by ADR-NNNN`.
4. Add the new row to the **Index** table above.
5. Cross-link from any larger design doc the ADR clarifies (e.g.
   [`docs/architecture.md`](../architecture.md)'s "Related ADRs and
   tickets" section).

## Reading order

The records build on each other but each is self-contained. If you're
reading top to bottom for the first time, **0001 → 0002 → 0004 → 0003 →
0007 → 0005 → 0006** is the path that minimizes back-references.
