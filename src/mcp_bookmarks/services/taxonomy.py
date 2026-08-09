"""TaxonomyService — tag CRUD + merge + per-bookmark assignment.

Handlers previously called `db.create_tag`, `db.tag_bookmark`,
`db.get_tag_by_slug`, etc. directly. Centralizing here lets us:

- normalize slug/name strings in one place (the REST + MCP layers were
  re-implementing strip());
- surface the "tag exists" conflict as a typed return rather than the
  current "check then create" race;
- thread structured logs through tag-mutation events for the
  observability story (WDN-397).
"""

from __future__ import annotations

import logging
import re

from ..backend import BookmarkBackend
from ..models import Bookmark, Tag

log = logging.getLogger(__name__)


async def get_all(*, db: BookmarkBackend) -> list[Tag]:
    return await db.get_all_tags()


async def search(*, db: BookmarkBackend, query: str) -> list[Tag]:
    return await db.search_tags(query)


async def get_by_slug(*, db: BookmarkBackend, slug: str) -> Tag | None:
    return await db.get_tag_by_slug(slug)


async def create(
    *,
    db: BookmarkBackend,
    slug: str,
    name: str,
    description: str = "",
) -> tuple[Tag | None, bool]:
    """Create a new tag.

    Returns ``(tag, created)``: ``(existing_tag, False)`` if a tag with
    that slug already exists in the tenant, ``(new_tag, True)`` otherwise.
    Handlers branch on ``created`` to return 201 vs 409.
    """
    existing = await db.get_tag_by_slug(slug)
    if existing:
        return existing, False
    tag = await db.create_tag(slug=slug, name=name, description=description)
    log.info("tag_created", extra={"slug": slug, "tag_name": name})
    return tag, True


async def update(
    *,
    db: BookmarkBackend,
    slug: str,
    new_name: str | None = None,
    new_description: str | None = None,
) -> Tag | None:
    return await db.update_tag(slug, new_name=new_name, new_description=new_description)


async def delete(*, db: BookmarkBackend, slug: str) -> bool:
    deleted = await db.delete_tag(slug)
    if deleted:
        log.info("tag_deleted", extra={"slug": slug})
    return deleted


async def merge(*, db: BookmarkBackend, source_slug: str, target_slug: str) -> dict:
    result = await db.merge_tags(source_slug, target_slug)
    log.info(
        "tags_merged",
        extra={
            "source": source_slug,
            "target": target_slug,
            "reassigned": result.get("bookmarks_reassigned"),
        },
    )
    return result


async def tag_bookmark(
    *, db: BookmarkBackend, bookmark_id: int | str, tag_slugs: list[str]
) -> Bookmark | None:
    return await db.tag_bookmark(bookmark_id, tag_slugs)


async def untag_bookmark(
    *, db: BookmarkBackend, bookmark_id: int | str, tag_slugs: list[str]
) -> Bookmark | None:
    return await db.untag_bookmark(bookmark_id, tag_slugs)


# ── Admin tag editing (Phase 1): normalization + validation ────────

_TAG_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_TAG_LENGTH = 30
MAX_TAGS_PER_BOOKMARK = 10


def normalize_and_validate_tags(raw: list[str]) -> tuple[list[str] | None, str | None]:
    """Normalize submitted tags and validate the result.

    Normalization: strip leading '#', lowercase, trim, spaces→hyphens;
    dedupe preserving order. Validation: each slug matches
    ``^[a-z0-9]+(-[a-z0-9]+)*$`` and is ≤30 chars; ≤10 tags per bookmark.

    Returns ``(normalized, None)`` on success or ``(None, reason)`` on the
    first failure — callers map ``reason`` to a 400 invalid_request and
    write NOTHING.
    """
    normalized: list[str] = []
    for original in raw:
        slug = original.strip().lstrip("#").strip().lower().replace(" ", "-")
        if not slug:
            return None, f"Empty tag after normalization: {original!r}"
        if len(slug) > MAX_TAG_LENGTH:
            return None, f"Tag too long (>{MAX_TAG_LENGTH} chars): {slug!r}"
        if not _TAG_SLUG_RE.fullmatch(slug):
            return None, f"Invalid tag slug: {slug!r}"
        if slug not in normalized:
            normalized.append(slug)
    if len(normalized) > MAX_TAGS_PER_BOOKMARK:
        return None, f"Too many tags ({len(normalized)} > {MAX_TAGS_PER_BOOKMARK})"
    return normalized, None
