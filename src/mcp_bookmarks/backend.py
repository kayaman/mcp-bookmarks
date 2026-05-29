"""Storage backend protocol shared by SQLite (``Database``) and DynamoDB
(``DynamoDBDatabase``).

Phase 1 of WDN-393 (OSS-3). This module replaces the *implicit* duck-typing
between the two backend classes with a *documented* contract:

- :class:`BookmarkBackend` — Protocol covering every operation both backends
  implement (tags, bookmarks, search, stats, export).
- :class:`BackendCapabilities` — a frozen flag set describing optional
  capabilities (semantic search, paged search, native usage metering,
  native subscription storage, integer bookmark IDs).

Phase 2 (handler/service extraction) is tracked separately on WDN-393 and
will move quota/billing/transport-specific logic out of MCP and REST handlers
and into application services that depend on this protocol.

Why a ``Protocol`` and not an ABC: both ``Database`` and ``DynamoDBDatabase``
predate this module and already implement every method by duck typing. A
runtime-checkable ``Protocol`` adds the typing contract without forcing an
inheritance chain or refactor of existing instantiation sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .models import Bookmark, Tag


# ── Capability flags ───────────────────────────────────────────────


@dataclass(frozen=True)
class BackendCapabilities:
    """What an individual backend supports beyond the shared protocol surface.

    Callers (MCP tools, REST handlers, future application services) inspect
    these flags to decide whether to expose a capability-specific code path
    or short-circuit with a structured "not supported on this backend" error.

    Add a new flag here when you add a capability that's optional across
    backends. Keep methods that are *always* available out of the flag set —
    they belong in the :class:`BookmarkBackend` protocol directly.
    """

    # Title+description+content embeddings (currently SQLite + OpenAI only).
    # Implementation methods: ``upsert_bookmark_embedding``,
    # ``get_all_embeddings`` on the concrete backend.
    semantic_search: bool = False

    # Cursor-based paged search (DynamoDB returns next_cursor for large
    # result sets). Implementation method: ``search_bookmarks_paged``.
    paged_search: bool = False

    # Integer auto-increment bookmark IDs (SQLite). When False, IDs are
    # opaque strings (UUIDs in DynamoDB). Affects ID-bearing tool responses.
    integer_bookmark_ids: bool = True

    # Backend natively stores usage_events rows (SQLite). When False, the
    # caller routes usage to the optional ``DYNAMODB_USAGE_TABLE``
    # (see usage_meter.record_usage_for_backend).
    usage_metering: bool = False

    # Backend natively stores Stripe subscription state (SQLite). When False,
    # the caller routes subscriptions to ``DYNAMODB_SUBSCRIPTIONS_TABLE``.
    subscription_storage: bool = False


# ── Protocol ───────────────────────────────────────────────────────


@runtime_checkable
class BookmarkBackend(Protocol):
    """Storage operations every backend implements.

    Signatures cover only the *shared* surface. A concrete backend may accept
    additional keyword arguments (DynamoDB's ``upsert_bookmark`` takes
    ``bookmark_type``, ``flow_id``, ``source``) — callers using the protocol
    type should pass only what's listed here.

    Capability-gated methods (semantic search, paged search, native usage
    metering, native subscription storage) live on the concrete classes and
    must be guarded by ``backend.capabilities.X`` checks.

    .. note::

       ``@runtime_checkable`` enables ``isinstance(obj, BookmarkBackend)`` but
       only verifies attribute presence, not signature compatibility. The
       contract here is the source of truth; mypy / pyright will catch real
       signature drift.
    """

    capabilities: BackendCapabilities

    # ── Lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open backend connections, run any schema migrations."""
        ...

    async def close(self) -> None:
        """Release backend connections; called on shutdown."""
        ...

    # ── Tag taxonomy ───────────────────────────────────────────────

    async def get_all_tags(self) -> list["Tag"]:
        ...

    async def search_tags(self, query: str) -> list["Tag"]:
        ...

    async def create_tag(self, slug: str, name: str, description: str = "") -> "Tag":
        ...

    async def get_tag_by_slug(self, slug: str) -> "Tag | None":
        ...

    async def update_tag(
        self,
        slug: str,
        new_name: str | None = None,
        new_description: str | None = None,
    ) -> "Tag | None":
        ...

    async def delete_tag(self, slug: str) -> bool:
        ...

    async def merge_tags(self, source_slug: str, target_slug: str) -> dict:
        ...

    # ── Bookmarks ──────────────────────────────────────────────────

    async def upsert_bookmark(
        self,
        url: str,
        title: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        site_name: str | None = None,
    ) -> "Bookmark":
        ...

    async def get_bookmark_by_id(self, bookmark_id: int | str) -> "Bookmark | None":
        ...

    async def set_bookmark_content(
        self, bookmark_id: int | str, content: str, word_count: int
    ) -> None:
        ...

    async def set_bookmark_summary(self, bookmark_id: int | str, summary: str) -> None:
        ...

    async def tag_bookmark(
        self, bookmark_id: int | str, tag_slugs: list[str]
    ) -> "Bookmark | None":
        ...

    async def untag_bookmark(
        self, bookmark_id: int | str, tag_slugs: list[str]
    ) -> "Bookmark | None":
        ...

    async def search_bookmarks(
        self,
        query: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list["Bookmark"]:
        ...

    async def delete_bookmark(self, bookmark_id: int | str) -> bool:
        ...

    # ── Aggregate / export ─────────────────────────────────────────

    async def get_stats(self) -> dict:
        ...

    async def get_all_bookmarks(self) -> list["Bookmark"]:
        ...

    async def get_full_export(self) -> dict:
        ...


# ── Convenience: declared capabilities for the two known backends ──────
#
# These constants give the application layer a way to reason about
# capabilities WITHOUT importing the concrete backend modules (which is
# important because the backend layer is the lowest layer and shouldn't be
# pulled into transport-layer code at import time).

SQLITE_CAPABILITIES = BackendCapabilities(
    semantic_search=True,
    paged_search=False,
    integer_bookmark_ids=True,
    usage_metering=True,
    subscription_storage=True,
)

DYNAMODB_CAPABILITIES = BackendCapabilities(
    semantic_search=False,  # blocked on the cloud vector pipeline (docs/dynamodb-rag-design.md)
    paged_search=True,
    integer_bookmark_ids=False,  # UUID strings
    usage_metering=False,  # delegated to optional DYNAMODB_USAGE_TABLE
    subscription_storage=False,  # delegated to optional DYNAMODB_SUBSCRIPTIONS_TABLE
)


__all__ = [
    "BackendCapabilities",
    "BookmarkBackend",
    "DYNAMODB_CAPABILITIES",
    "SQLITE_CAPABILITIES",
]
