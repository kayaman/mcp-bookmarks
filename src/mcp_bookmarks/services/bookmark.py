"""BookmarkService — save / extract / set-summary / delete orchestration.

The handlers in `server.py` (MCP tools) and `api.py` (REST) used to call
`db.upsert_bookmark`, the scraper, and `db.set_bookmark_content` inline.
That duplicated the OG-fallback boundary log.

Centralized here:

- :func:`save_with_metadata` — scraper boundary, OG fallback, persists.
- :func:`extract_and_persist_content` — second-half of the save flow
  used by `POST /api/save` and the MCP `extract_content` tool.
- :func:`set_summary` and :func:`delete` — thin pass-throughs so the
  handler-side surface is uniform.
"""

from __future__ import annotations

import logging

from ..backend import BookmarkBackend
from ..models import Bookmark, OGMetadata
from ..scraper import extract_article_content, extract_og_metadata

log = logging.getLogger(__name__)


async def save_with_metadata(
    *,
    db: BookmarkBackend,
    url: str,
    title: str | None = None,
    bookmark_type: str | None = None,
    flow_id: str | None = None,
    source: str | None = None,
) -> tuple[Bookmark, OGMetadata]:
    """Fetch OG metadata, persist the bookmark.

    Returns the saved :class:`Bookmark` plus the (possibly fallback)
    :class:`OGMetadata` so the caller can include the title etc. in the
    response without a second read.
    """
    try:
        og = await extract_og_metadata(url)
    except (OSError, ValueError) as exc:
        # Boundary adapter — log structured warning and persist with just the URL.
        log.warning("og_metadata_extraction_failed", extra={"url": url, "error": str(exc)})
        og = OGMetadata(url=url)

    # The protocol now declares the three optional kwargs; SQLite accepts-and-
    # ignores them, DynamoDB persists them as camelCase columns. No cast needed.
    bookmark = await db.upsert_bookmark(
        url=og.url,
        title=title or og.title,
        description=og.description,
        image_url=og.image,
        site_name=og.site_name,
        bookmark_type=bookmark_type,
        flow_id=flow_id,
        source=source,
    )
    return bookmark, og


async def extract_and_persist_content(
    *, db: BookmarkBackend, bookmark_id: int | str, url: str
) -> int:
    """Run trafilatura on ``url``, persist on ``bookmark_id``, return word count.

    Returns 0 on extraction failure (boundary log emitted). Callers
    decide whether 0 is acceptable in the response shape; the REST
    ``POST /api/save`` path returns it as-is.
    """
    try:
        article = await extract_article_content(url)
    except (OSError, ValueError) as exc:
        log.warning("article_extraction_failed", extra={"url": url, "error": str(exc)})
        return 0
    await db.set_bookmark_content(bookmark_id, article.text, article.word_count)
    return article.word_count


async def set_summary(*, db: BookmarkBackend, bookmark_id: int | str, summary: str) -> None:
    """Persist an AI-generated summary on an existing bookmark."""
    await db.set_bookmark_summary(bookmark_id, summary)


async def get_or_none(*, db: BookmarkBackend, bookmark_id: int | str) -> Bookmark | None:
    """Read a bookmark by id; returns None for 'not found' so handlers can 404 cleanly."""
    return await db.get_bookmark_by_id(bookmark_id)


async def delete(*, db: BookmarkBackend, bookmark_id: int | str) -> bool:
    """Delete a bookmark; returns True if it existed and was removed."""
    return await db.delete_bookmark(bookmark_id)


async def set_body(*, db: BookmarkBackend, bookmark_id: int | str, text: str) -> int:
    """Persist already-fetched article body (from Bright Data, Tavily, etc.).

    Skips the scraper. Returns the word count counted from ``text.split()``.
    """
    word_count = len(text.split())
    await db.set_bookmark_content(bookmark_id, text, word_count)
    return word_count
