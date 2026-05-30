"""SearchService — capability-aware bookmark search.

Exercises both branches:
- ``paged_search=True`` → backend's ``search_bookmarks_paged`` returns
  ``(bookmarks, next_cursor)``.
- ``paged_search=False`` → falls back to ``search_bookmarks`` and returns
  ``(bookmarks, None)``.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_bookmarks.backend import (
    DYNAMODB_CAPABILITIES,
    SQLITE_CAPABILITIES,
)
from mcp_bookmarks.models import Bookmark
from mcp_bookmarks.services import search as search_service


def _b(id_: int, url: str = "https://example.com") -> Bookmark:
    return Bookmark(id=id_, url=url)


class _FakePagedBackend:
    """Stand-in for DynamoDB — paged_search=True."""

    capabilities = DYNAMODB_CAPABILITIES

    def __init__(self, bookmarks: list[Bookmark], next_cursor: str | None) -> None:
        self._bookmarks = bookmarks
        self._next_cursor = next_cursor
        self.calls: list[dict[str, Any]] = []

    async def search_bookmarks_paged(
        self,
        *,
        query: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[Bookmark], str | None]:
        self.calls.append({"query": query, "tag": tag, "limit": limit, "cursor": cursor})
        return self._bookmarks, self._next_cursor


class _FakeUnpagedBackend:
    """Stand-in for SQLite — paged_search=False."""

    capabilities = SQLITE_CAPABILITIES

    def __init__(self, bookmarks: list[Bookmark]) -> None:
        self._bookmarks = bookmarks
        self.calls: list[dict[str, Any]] = []

    async def search_bookmarks(
        self,
        query: str | None = None,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[Bookmark]:
        self.calls.append({"query": query, "tag": tag, "limit": limit})
        return self._bookmarks


async def test_paged_backend_returns_cursor():
    db = _FakePagedBackend(bookmarks=[_b(1), _b(2)], next_cursor="cursor-xyz")
    bookmarks, cursor = await search_service.search_bookmarks(
        db=db,
        query="rust",
        tag="lang",
        limit=20,
        cursor=None,  # type: ignore[arg-type]
    )
    assert [b.id for b in bookmarks] == [1, 2]
    assert cursor == "cursor-xyz"
    assert db.calls == [{"query": "rust", "tag": "lang", "limit": 20, "cursor": None}]


async def test_paged_backend_propagates_existing_cursor():
    db = _FakePagedBackend(bookmarks=[], next_cursor=None)
    bookmarks, cursor = await search_service.search_bookmarks(
        db=db,
        cursor="opaque-token",
        limit=5,  # type: ignore[arg-type]
    )
    assert bookmarks == []
    assert cursor is None
    # Caller's cursor was passed through to the backend
    assert db.calls[0]["cursor"] == "opaque-token"


async def test_unpaged_backend_returns_none_cursor():
    db = _FakeUnpagedBackend(bookmarks=[_b(1), _b(2), _b(3)])
    bookmarks, cursor = await search_service.search_bookmarks(
        db=db,
        query="ai",  # type: ignore[arg-type]
    )
    assert [b.id for b in bookmarks] == [1, 2, 3]
    assert cursor is None
    assert db.calls == [{"query": "ai", "tag": None, "limit": 50}]


async def test_unpaged_backend_ignores_cursor_arg():
    """SQLite doesn't paginate, so even if the handler passes a cursor we just
    ignore it and return cursor=None — the contract is uniform from the caller's
    perspective."""
    db = _FakeUnpagedBackend(bookmarks=[_b(1)])
    _, cursor = await search_service.search_bookmarks(
        db=db,
        cursor="should-be-ignored",
        limit=10,  # type: ignore[arg-type]
    )
    assert cursor is None
    # cursor was NOT forwarded to search_bookmarks
    assert "cursor" not in db.calls[0]


@pytest.mark.parametrize("limit", [1, 50, 200])
async def test_limit_is_forwarded(limit: int):
    db = _FakePagedBackend(bookmarks=[], next_cursor=None)
    await search_service.search_bookmarks(db=db, limit=limit)  # type: ignore[arg-type]
    assert db.calls[0]["limit"] == limit
