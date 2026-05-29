# ADR-0004: Backend capability divergence as a typed flag set

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Marco Gonzalez Junior
- **Related:** [`src/mcp_bookmarks/backend.py`](../../src/mcp_bookmarks/backend.py), [`src/mcp_bookmarks/api.py`](../../src/mcp_bookmarks/api.py), [`docs/architecture.md`](../architecture.md), [ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md)

## Context

The two backends from [ADR-0001](0001-sqlite-dynamodb-dual-mode-storage.md)
do not have identical surfaces:

- SQLite supports OpenAI-embedded semantic search; DynamoDB doesn't
  (the cloud vector pipeline is design-stage — see
  [ADR-0005](0005-vector-search-roadmap.md)).
- DynamoDB exposes `search_bookmarks_paged` with a `next_cursor` for
  large result sets; SQLite returns a single page.
- DynamoDB uses opaque UUID strings as bookmark IDs; SQLite uses
  auto-increment integers.
- SQLite records usage events and Stripe subscription state natively;
  DynamoDB delegates both to optional sidecar tables
  (`DYNAMODB_USAGE_TABLE`, `DYNAMODB_SUBSCRIPTIONS_TABLE`).

The "obvious" responses to this — duck typing with `hasattr` checks,
runtime sniffs on `os.environ["DYNAMODB_MODE"]`, or sentinel return
values — all rely on **prose comments** to tell callers when things
work. That's exactly what a reviewer flagged as fragile.

## Decision

We encode the asymmetry as a **typed flag set**
([`BackendCapabilities`](../../src/mcp_bookmarks/backend.py)), a
`@dataclass(frozen=True)` with five boolean attributes:

| Flag | SQLite | DynamoDB |
|---|:---:|:---:|
| `semantic_search` | ✅ | ❌ |
| `paged_search` | ❌ | ✅ |
| `integer_bookmark_ids` | ✅ | ❌ |
| `usage_metering` | ✅ | ❌ |
| `subscription_storage` | ✅ | ❌ |

Each backend class declares a `capabilities` class attribute pointing
at `SQLITE_CAPABILITIES` or `DYNAMODB_CAPABILITIES`. Callers gate
capability-specific code paths via
[`require_capability(backend, "semantic_search", method="...")`](../../src/mcp_bookmarks/backend.py),
which raises `UnsupportedCapability` if the flag is off. The exception
serializes to the standard `unsupported` error envelope
([`docs/api.md` § Error envelope](../api.md#error-envelope)) so REST
and MCP handlers can surface a single, machine-readable shape.

Clients discover the active backend's flags via
[`GET /api/capabilities`](../api.md#endpoints), so they can branch
ahead of calling instead of round-tripping for a 403.

## Consequences

- **Good:**
  - Every divergence has a name. New flags are added by editing one
    dataclass; the drift-guard test
    [`tests/unit/test_capability_enforcement.py`](../../tests/unit/test_capability_enforcement.py)
    asserts the payload keys equal `dataclasses.fields(BackendCapabilities)`,
    so a forgotten payload entry fails CI.
  - The `unsupported` envelope means a DynamoDB caller invoking
    `semantic_search_bookmarks` gets a structured 403 with
    `details.capability = "semantic_search"` instead of a 500
    `AttributeError`.
  - Adding a new backend (Postgres for cloud vector — see
    [ADR-0005](0005-vector-search-roadmap.md)) is a matter of writing a
    new `*_CAPABILITIES` constant and a new class; no caller code
    changes for the supported paths.

- **Bad:**
  - The protocol contract only lists *shared* methods; capability-gated
    methods (`upsert_bookmark_embedding`, `search_bookmarks_paged`)
    live on the concrete class. Static analysis on the protocol type
    can't see them, so callers either pyright-cast or accept the
    runtime guard.
  - `@runtime_checkable` Protocol only verifies attribute presence at
    runtime, not signature compatibility. The conformance test is a
    thin guardrail; the real safety net is mypy/pyright (currently
    unconfigured — tracked on WDN-400).

- **Operational:**
  - Treat `GET /api/capabilities` as part of the public API. Changing
    a flag's name is a breaking change. New flags are additive.
  - When a deployment unsets `DYNAMODB_USAGE_TABLE`, the
    `usage_metering` flag stays `False` on the DynamoDB backend, so
    `/api/usage` returns `forbidden` instead of `500`.

## Alternatives considered

- **Lowest-common-denominator surface.** Strip `semantic_search_bookmarks`
  and `search_bookmarks_paged` from MCP / REST so both backends look
  identical. Rejected because the SQLite single-user experience would
  lose its best feature (semantic search) to make the DynamoDB
  multi-user one symmetric.
- **`hasattr` checks at every call site.** Considered for simplicity.
  Rejected because we'd have N places where the "what does this backend
  do?" question is implicitly answered, and any drift between them is
  silent.
- **`isinstance` against the concrete class.** Considered because it's
  type-safe at the call site. Rejected because it pulls the concrete
  backend modules into transport-layer code that should depend only on
  the protocol — circular-import territory and a fragile coupling.
- **Feature flags via env vars only.** Considered for runtime
  flexibility. Rejected because the divergence reflects *what the
  backend can do*, not *what we chose to enable*; an env-var flag could
  produce nonsense states like `semantic_search=true` on DynamoDB.

## References

- [`src/mcp_bookmarks/backend.py`](../../src/mcp_bookmarks/backend.py) —
  `BackendCapabilities`, `BookmarkBackend`, `require_capability`,
  `UnsupportedCapability`, `backend_capabilities_payload`.
- [`src/mcp_bookmarks/api.py`](../../src/mcp_bookmarks/api.py) —
  `api_capabilities` REST handler; `/api/usage` capability gate.
- [`tests/unit/test_capability_enforcement.py`](../../tests/unit/test_capability_enforcement.py) —
  conformance + drift-guard tests.
- [`docs/architecture.md`](../architecture.md) — capability matrix.
- [`docs/api.md`](../api.md) — `/api/capabilities`, error envelope.
- [ADR-0001 — SQLite + DynamoDB dual-mode storage](0001-sqlite-dynamodb-dual-mode-storage.md).
