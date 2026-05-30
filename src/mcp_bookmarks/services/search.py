"""SearchService — capability-aware bookmark search.

Handlers were doing::

    if db.capabilities.paged_search:
        bookmarks, next_cursor = await cast(Any, db).search_bookmarks_paged(...)
    else:
        bookmarks = await db.search_bookmarks(...)
        next_cursor = None

Centralized here so:
- the cast(Any, db) acknowledgement of the capability/protocol gap
  lives in one file;
- handlers return ``(bookmarks, next_cursor)`` uniformly and don't
  branch on backend type;
- the protocol gap is documented in one place — the day we add
  ``search_bookmarks_paged`` to the protocol (with SQLite returning
  cursor=None), this file becomes a one-liner.
"""

from __future__ import annotations

from typing import Any, cast

from ..backend import BookmarkBackend
from ..models import Bookmark


async def search_bookmarks(
    *,
    db: BookmarkBackend,
    query: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[Bookmark], str | None]:
    """Return ``(bookmarks, next_cursor)``.

    Uses the backend's paged search when ``capabilities.paged_search`` is
    set; falls back to ``search_bookmarks`` (returning ``cursor=None``)
    for SQLite-style backends that don't paginate.
    """
    if db.capabilities.paged_search:
        # search_bookmarks_paged lives only on DynamoDBDatabase; gated above.
        bookmarks, next_cursor = await cast(Any, db).search_bookmarks_paged(
            query=query, tag=tag, limit=limit, cursor=cursor
        )
        return bookmarks, next_cursor
    bookmarks = await db.search_bookmarks(query=query, tag=tag, limit=limit)
    return bookmarks, None
