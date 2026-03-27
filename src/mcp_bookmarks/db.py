"""Async SQLite database layer for bookmarks and tags."""

import aiosqlite
from pathlib import Path
from datetime import datetime, timezone

from .models import Bookmark, Tag

DEFAULT_DB_PATH = Path.home() / ".mcp-bookmarks" / "bookmarks.db"


def _coerce_sqlite_bookmark_id(bookmark_id: int | str) -> int | None:
    """SQLite bookmarks use integer PK; accept str digits from MCP JSON."""
    if isinstance(bookmark_id, int):
        return bookmark_id
    s = str(bookmark_id).strip()
    return int(s) if s.isdigit() else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    usage_count INTEGER DEFAULT 0,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    UNIQUE NOT NULL,
    title       TEXT,
    description TEXT,
    image_url   TEXT,
    site_name   TEXT,
    summary     TEXT,
    content     TEXT,
    word_count  INTEGER DEFAULT 0,
    created_at  TEXT    DEFAULT (datetime('now')),
    updated_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookmark_tags (
    bookmark_id INTEGER NOT NULL REFERENCES bookmarks(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)      ON DELETE CASCADE,
    PRIMARY KEY (bookmark_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_bookmarks_url ON bookmarks(url);
CREATE INDEX IF NOT EXISTS idx_tags_slug     ON tags(slug);
"""


class Database:
    """Async wrapper around the SQLite bookmark store."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Add columns that may not exist in older databases."""
        cursor = await self.db.execute("PRAGMA table_info(bookmarks)")
        columns = {row["name"] for row in await cursor.fetchall()}
        if "content" not in columns:
            await self.db.execute("ALTER TABLE bookmarks ADD COLUMN content TEXT")
        if "word_count" not in columns:
            await self.db.execute("ALTER TABLE bookmarks ADD COLUMN word_count INTEGER DEFAULT 0")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    # ── Tags ──────────────────────────────────────────────────────────

    async def get_all_tags(self) -> list[Tag]:
        """Return every canonical tag, ordered by usage."""
        cursor = await self.db.execute(
            "SELECT * FROM tags ORDER BY usage_count DESC, name ASC"
        )
        rows = await cursor.fetchall()
        return [
            Tag(
                id=r["id"],
                slug=r["slug"],
                name=r["name"],
                description=r["description"],
                usage_count=r["usage_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def search_tags(self, query: str) -> list[Tag]:
        """Search tags by slug or name (partial match)."""
        cursor = await self.db.execute(
            """SELECT * FROM tags
               WHERE slug LIKE ? OR name LIKE ? OR description LIKE ?
               ORDER BY usage_count DESC""",
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        )
        rows = await cursor.fetchall()
        return [
            Tag(
                id=r["id"],
                slug=r["slug"],
                name=r["name"],
                description=r["description"],
                usage_count=r["usage_count"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def create_tag(self, slug: str, name: str, description: str = "") -> Tag:
        """Create a new canonical tag. Raises if slug already exists."""
        cursor = await self.db.execute(
            "INSERT INTO tags (slug, name, description) VALUES (?, ?, ?)",
            (slug, name, description),
        )
        await self.db.commit()
        return Tag(
            id=cursor.lastrowid,
            slug=slug,
            name=name,
            description=description,
            usage_count=0,
        )

    async def get_tag_by_slug(self, slug: str) -> Tag | None:
        cursor = await self.db.execute("SELECT * FROM tags WHERE slug = ?", (slug,))
        r = await cursor.fetchone()
        if not r:
            return None
        return Tag(
            id=r["id"],
            slug=r["slug"],
            name=r["name"],
            description=r["description"],
            usage_count=r["usage_count"],
            created_at=r["created_at"],
        )

    # ── Bookmarks ─────────────────────────────────────────────────────

    async def _row_to_bookmark(self, r) -> Bookmark:
        """Convert a DB row to a Bookmark model, including tags."""
        tags = await self._get_bookmark_tags(r["id"])
        return Bookmark(
            id=r["id"],
            url=r["url"],
            title=r["title"],
            description=r["description"],
            image_url=r["image_url"],
            site_name=r["site_name"],
            summary=r["summary"],
            content=r["content"],
            word_count=r["word_count"],
            tags=tags,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    async def upsert_bookmark(
        self,
        url: str,
        title: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        site_name: str | None = None,
    ) -> Bookmark:
        """Insert or update a bookmark by URL."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self.db.execute(
            """INSERT INTO bookmarks (url, title, description, image_url, site_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                   title       = COALESCE(excluded.title, bookmarks.title),
                   description = COALESCE(excluded.description, bookmarks.description),
                   image_url   = COALESCE(excluded.image_url, bookmarks.image_url),
                   site_name   = COALESCE(excluded.site_name, bookmarks.site_name),
                   updated_at  = excluded.updated_at
               RETURNING *""",
            (url, title, description, image_url, site_name, now),
        )
        r = await cursor.fetchone()
        await self.db.commit()
        return await self._row_to_bookmark(r)

    async def get_bookmark_by_id(self, bookmark_id: int | str) -> Bookmark | None:
        """Retrieve a single bookmark by ID."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return None
        cursor = await self.db.execute(
            "SELECT * FROM bookmarks WHERE id = ?", (bid,)
        )
        r = await cursor.fetchone()
        if not r:
            return None
        return await self._row_to_bookmark(r)

    async def set_bookmark_content(
        self, bookmark_id: int | str, content: str, word_count: int
    ) -> None:
        """Store extracted article content for a bookmark."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return
        await self.db.execute(
            "UPDATE bookmarks SET content = ?, word_count = ?, updated_at = ? WHERE id = ?",
            (content, word_count, datetime.now(timezone.utc).isoformat(), bid),
        )
        await self.db.commit()

    async def tag_bookmark(self, bookmark_id: int | str, tag_slugs: list[str]) -> Bookmark:
        """Assign tags to a bookmark. Creates the association and bumps usage_count."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            raise ValueError("Invalid bookmark_id for SQLite (expected integer).")
        for slug in tag_slugs:
            tag = await self.get_tag_by_slug(slug)
            if not tag or not tag.id:
                raise ValueError(f"Tag '{slug}' does not exist. Create it first.")

            await self.db.execute(
                """INSERT OR IGNORE INTO bookmark_tags (bookmark_id, tag_id)
                   VALUES (?, ?)""",
                (bid, tag.id),
            )
            await self.db.execute(
                """UPDATE tags SET usage_count = (
                       SELECT COUNT(*) FROM bookmark_tags WHERE tag_id = ?
                   ) WHERE id = ?""",
                (tag.id, tag.id),
            )
        await self.db.commit()

        cursor = await self.db.execute(
            "SELECT * FROM bookmarks WHERE id = ?", (bid,)
        )
        r = await cursor.fetchone()
        return await self._row_to_bookmark(r)

    async def set_bookmark_summary(self, bookmark_id: int | str, summary: str) -> None:
        """Store an AI-generated summary for a bookmark."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return
        await self.db.execute(
            "UPDATE bookmarks SET summary = ?, updated_at = ? WHERE id = ?",
            (summary, datetime.now(timezone.utc).isoformat(), bid),
        )
        await self.db.commit()

    async def search_bookmarks(
        self, query: str | None = None, tag: str | None = None, limit: int = 20
    ) -> list[Bookmark]:
        """Search bookmarks by text or tag."""
        if tag:
            cursor = await self.db.execute(
                """SELECT b.* FROM bookmarks b
                   JOIN bookmark_tags bt ON b.id = bt.bookmark_id
                   JOIN tags t ON bt.tag_id = t.id
                   WHERE t.slug = ?
                   ORDER BY b.updated_at DESC LIMIT ?""",
                (tag, limit),
            )
        elif query:
            cursor = await self.db.execute(
                """SELECT * FROM bookmarks
                   WHERE title LIKE ? OR description LIKE ? OR url LIKE ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM bookmarks ORDER BY updated_at DESC LIMIT ?", (limit,)
            )

        rows = await cursor.fetchall()
        return [await self._row_to_bookmark(r) for r in rows]

    async def get_stats(self) -> dict:
        """Return knowledge base stats."""
        bk = await self.db.execute("SELECT COUNT(*) as c FROM bookmarks")
        tg = await self.db.execute("SELECT COUNT(*) as c FROM tags")
        bk_row = await bk.fetchone()
        tg_row = await tg.fetchone()
        return {
            "total_bookmarks": bk_row["c"],
            "total_tags": tg_row["c"],
        }

    # ── Management operations ─────────────────────────────────────────

    async def delete_bookmark(self, bookmark_id: int | str) -> bool:
        """Delete a bookmark and its tag associations. Returns True if found."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return False
        cursor = await self.db.execute(
            "SELECT id FROM bookmarks WHERE id = ?", (bid,)
        )
        if not await cursor.fetchone():
            return False

        # Get associated tag IDs before deleting to update usage counts
        tag_cursor = await self.db.execute(
            "SELECT tag_id FROM bookmark_tags WHERE bookmark_id = ?", (bid,)
        )
        tag_ids = [r["tag_id"] for r in await tag_cursor.fetchall()]

        await self.db.execute(
            "DELETE FROM bookmark_tags WHERE bookmark_id = ?", (bid,)
        )
        await self.db.execute(
            "DELETE FROM bookmarks WHERE id = ?", (bid,)
        )

        # Recalculate usage counts for affected tags
        for tag_id in tag_ids:
            await self.db.execute(
                """UPDATE tags SET usage_count = (
                       SELECT COUNT(*) FROM bookmark_tags WHERE tag_id = ?
                   ) WHERE id = ?""",
                (tag_id, tag_id),
            )
        await self.db.commit()
        return True

    async def update_tag(
        self,
        slug: str,
        new_name: str | None = None,
        new_description: str | None = None,
    ) -> Tag | None:
        """Update a tag's name and/or description. Returns updated tag or None."""
        tag = await self.get_tag_by_slug(slug)
        if not tag:
            return None

        updates = []
        params = []
        if new_name is not None:
            updates.append("name = ?")
            params.append(new_name)
        if new_description is not None:
            updates.append("description = ?")
            params.append(new_description)

        if not updates:
            return tag

        params.append(slug)
        await self.db.execute(
            f"UPDATE tags SET {', '.join(updates)} WHERE slug = ?",
            params,
        )
        await self.db.commit()
        return await self.get_tag_by_slug(slug)

    async def delete_tag(self, slug: str) -> bool:
        """Delete a tag and remove it from all bookmarks. Returns True if found."""
        tag = await self.get_tag_by_slug(slug)
        if not tag or not tag.id:
            return False

        await self.db.execute(
            "DELETE FROM bookmark_tags WHERE tag_id = ?", (tag.id,)
        )
        await self.db.execute("DELETE FROM tags WHERE id = ?", (tag.id,))
        await self.db.commit()
        return True

    async def merge_tags(self, source_slug: str, target_slug: str) -> dict:
        """Merge source tag into target: reassign all bookmarks, delete source.

        Returns a summary of what happened.
        """
        source = await self.get_tag_by_slug(source_slug)
        target = await self.get_tag_by_slug(target_slug)

        if not source or not source.id:
            raise ValueError(f"Source tag '{source_slug}' not found.")
        if not target or not target.id:
            raise ValueError(f"Target tag '{target_slug}' not found.")

        # Find bookmarks with source tag
        cursor = await self.db.execute(
            "SELECT bookmark_id FROM bookmark_tags WHERE tag_id = ?",
            (source.id,),
        )
        bookmark_ids = [r["bookmark_id"] for r in await cursor.fetchall()]

        reassigned = 0
        for bk_id in bookmark_ids:
            # Add target tag if not already present
            await self.db.execute(
                "INSERT OR IGNORE INTO bookmark_tags (bookmark_id, tag_id) VALUES (?, ?)",
                (bk_id, target.id),
            )
            reassigned += 1

        # Remove source tag associations and delete it
        await self.db.execute(
            "DELETE FROM bookmark_tags WHERE tag_id = ?", (source.id,)
        )
        await self.db.execute("DELETE FROM tags WHERE id = ?", (source.id,))

        # Recalculate target usage count
        await self.db.execute(
            """UPDATE tags SET usage_count = (
                   SELECT COUNT(*) FROM bookmark_tags WHERE tag_id = ?
               ) WHERE id = ?""",
            (target.id, target.id),
        )
        await self.db.commit()

        return {
            "source_deleted": source_slug,
            "target": target_slug,
            "bookmarks_reassigned": reassigned,
        }

    async def untag_bookmark(self, bookmark_id: int | str, tag_slugs: list[str]) -> Bookmark | None:
        """Remove specific tags from a bookmark."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return None
        bk = await self.get_bookmark_by_id(bid)
        if not bk:
            return None

        for slug in tag_slugs:
            tag = await self.get_tag_by_slug(slug)
            if tag and tag.id:
                await self.db.execute(
                    "DELETE FROM bookmark_tags WHERE bookmark_id = ? AND tag_id = ?",
                    (bid, tag.id),
                )
                await self.db.execute(
                    """UPDATE tags SET usage_count = (
                           SELECT COUNT(*) FROM bookmark_tags WHERE tag_id = ?
                       ) WHERE id = ?""",
                    (tag.id, tag.id),
                )
        await self.db.commit()
        return await self.get_bookmark_by_id(bid)

    # ── Export operations ─────────────────────────────────────────────

    async def get_all_bookmarks(self) -> list[Bookmark]:
        """Return every bookmark with tags (for export)."""
        cursor = await self.db.execute(
            "SELECT * FROM bookmarks ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [await self._row_to_bookmark(r) for r in rows]

    async def get_full_export(self) -> dict:
        """Export the entire knowledge base as a structured dict."""
        bookmarks = await self.get_all_bookmarks()
        tags = await self.get_all_tags()
        stats = await self.get_stats()

        return {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "tags": [t.model_dump(mode="json") for t in tags],
            "bookmarks": [
                {
                    "id": b.id,
                    "url": b.url,
                    "title": b.title,
                    "description": b.description,
                    "site_name": b.site_name,
                    "image_url": b.image_url,
                    "summary": b.summary,
                    "word_count": b.word_count,
                    "tags": b.tags,
                    "created_at": str(b.created_at),
                    "updated_at": str(b.updated_at),
                }
                for b in bookmarks
            ],
        }

    # ── Private ───────────────────────────────────────────────────────

    async def _get_bookmark_tags(self, bookmark_id: int) -> list[str]:
        cursor = await self.db.execute(
            """SELECT t.slug FROM tags t
               JOIN bookmark_tags bt ON t.id = bt.tag_id
               WHERE bt.bookmark_id = ?
               ORDER BY t.slug""",
            (bookmark_id,),
        )
        rows = await cursor.fetchall()
        return [r["slug"] for r in rows]
