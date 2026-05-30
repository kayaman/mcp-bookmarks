"""Async SQLite database layer for bookmarks and tags."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .backend import SQLITE_CAPABILITIES
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
    slug        TEXT    NOT NULL,
    tenant_id   TEXT    NOT NULL DEFAULT 'default',
    name        TEXT    NOT NULL,
    description TEXT    DEFAULT '',
    usage_count INTEGER DEFAULT 0,
    created_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE (slug, tenant_id)
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL,
    tenant_id   TEXT    NOT NULL DEFAULT 'default',
    title       TEXT,
    description TEXT,
    image_url   TEXT,
    site_name   TEXT,
    summary     TEXT,
    content     TEXT,
    word_count  INTEGER DEFAULT 0,
    created_at  TEXT    DEFAULT (datetime('now')),
    updated_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE (url, tenant_id)
);

CREATE TABLE IF NOT EXISTS bookmark_tags (
    bookmark_id INTEGER NOT NULL REFERENCES bookmarks(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id)      ON DELETE CASCADE,
    PRIMARY KEY (bookmark_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_bookmarks_url ON bookmarks(url);
CREATE INDEX IF NOT EXISTS idx_tags_slug     ON tags(slug);

CREATE TABLE IF NOT EXISTS usage_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT    DEFAULT (datetime('now')),
    event_type   TEXT    NOT NULL,
    tenant_id    TEXT    DEFAULT 'default',
    metadata     TEXT    DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_usage_tenant_time ON usage_events(tenant_id, created_at);

CREATE TABLE IF NOT EXISTS subscription_state (
    customer_id         TEXT PRIMARY KEY,
    status              TEXT,
    plan_sku            TEXT,
    current_period_end  TEXT,
    raw_json            TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS bookmark_embeddings (
    bookmark_id  INTEGER NOT NULL,
    model        TEXT    NOT NULL,
    vector_json  TEXT    NOT NULL,
    updated_at   TEXT    DEFAULT (datetime('now')),
    PRIMARY KEY (bookmark_id, model),
    FOREIGN KEY (bookmark_id) REFERENCES bookmarks(id) ON DELETE CASCADE
);
"""


class Database:
    """Async wrapper around the SQLite bookmark store.

    Pass ``tenant_id`` to scope all reads and writes to that tenant.
    Defaults to ``"default"`` for single-user / personal installs.

    Conforms to :class:`mcp_bookmarks.backend.BookmarkBackend` (see that
    module for the documented contract).
    """

    # Capability matrix for the SQLite backend.
    # See src/mcp_bookmarks/backend.py for the documented flags.
    capabilities = SQLITE_CAPABILITIES

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, tenant_id: str = "default"):
        self.db_path = db_path
        self.tenant_id = tenant_id
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
        bk_cols = {row["name"] for row in await cursor.fetchall()}
        if "content" not in bk_cols:
            await self.db.execute("ALTER TABLE bookmarks ADD COLUMN content TEXT")
        if "word_count" not in bk_cols:
            await self.db.execute("ALTER TABLE bookmarks ADD COLUMN word_count INTEGER DEFAULT 0")
        if "tenant_id" not in bk_cols:
            await self.db.execute(
                "ALTER TABLE bookmarks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )

        cursor = await self.db.execute("PRAGMA table_info(tags)")
        tag_cols = {row["name"] for row in await cursor.fetchall()}
        if "tenant_id" not in tag_cols:
            await self.db.execute(
                "ALTER TABLE tags ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )

        # Indices on tenant_id columns must be created after migration (column may be new).
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bookmarks_tenant ON bookmarks(tenant_id)"
        )
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_tags_tenant ON tags(tenant_id)")

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
        """Return every canonical tag for this tenant, ordered by usage."""
        cursor = await self.db.execute(
            "SELECT * FROM tags WHERE tenant_id = ? ORDER BY usage_count DESC, name ASC",
            (self.tenant_id,),
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
        """Search tags by slug or name (partial match) for this tenant."""
        cursor = await self.db.execute(
            """SELECT * FROM tags
               WHERE tenant_id = ?
                 AND (slug LIKE ? OR name LIKE ? OR description LIKE ?)
               ORDER BY usage_count DESC""",
            (self.tenant_id, f"%{query}%", f"%{query}%", f"%{query}%"),
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
        """Create a new canonical tag for this tenant. Raises if slug already exists."""
        cursor = await self.db.execute(
            "INSERT INTO tags (slug, tenant_id, name, description) VALUES (?, ?, ?, ?)",
            (slug, self.tenant_id, name, description),
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
        cursor = await self.db.execute(
            "SELECT * FROM tags WHERE slug = ? AND tenant_id = ?", (slug, self.tenant_id)
        )
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
        *,
        # The three kwargs below are DynamoDB-only metadata that the MCP
        # `save_bookmark` tool may pass through. SQLite has no native column
        # for them today; we accept-and-ignore so the protocol surface is
        # uniform (no more cast(Any, db) at the call site).
        bookmark_type: str | None = None,
        flow_id: str | None = None,
        source: str | None = None,
    ) -> Bookmark:
        """Insert or update a bookmark by URL within this tenant."""
        _ = (bookmark_type, flow_id, source)  # silently ignored on SQLite
        now = datetime.now(UTC).isoformat()
        cursor = await self.db.execute(
            """INSERT INTO bookmarks (url, tenant_id, title, description, image_url, site_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url, tenant_id) DO UPDATE SET
                   title       = COALESCE(excluded.title, bookmarks.title),
                   description = COALESCE(excluded.description, bookmarks.description),
                   image_url   = COALESCE(excluded.image_url, bookmarks.image_url),
                   site_name   = COALESCE(excluded.site_name, bookmarks.site_name),
                   updated_at  = excluded.updated_at
               RETURNING *""",
            (url, self.tenant_id, title, description, image_url, site_name, now),
        )
        r = await cursor.fetchone()
        await self.db.commit()
        return await self._row_to_bookmark(r)

    async def get_bookmark_by_id(self, bookmark_id: int | str) -> Bookmark | None:
        """Retrieve a single bookmark by ID within this tenant."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return None
        cursor = await self.db.execute(
            "SELECT * FROM bookmarks WHERE id = ? AND tenant_id = ?", (bid, self.tenant_id)
        )
        r = await cursor.fetchone()
        if not r:
            return None
        return await self._row_to_bookmark(r)

    async def set_bookmark_content(
        self, bookmark_id: int | str, content: str, word_count: int
    ) -> None:
        """Store extracted article content for a bookmark within this tenant."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return
        await self.db.execute(
            "UPDATE bookmarks SET content = ?, word_count = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
            (content, word_count, datetime.now(UTC).isoformat(), bid, self.tenant_id),
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

        cursor = await self.db.execute("SELECT * FROM bookmarks WHERE id = ?", (bid,))
        r = await cursor.fetchone()
        return await self._row_to_bookmark(r)

    async def set_bookmark_summary(self, bookmark_id: int | str, summary: str) -> None:
        """Store an AI-generated summary for a bookmark within this tenant."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return
        await self.db.execute(
            "UPDATE bookmarks SET summary = ?, updated_at = ? WHERE id = ? AND tenant_id = ?",
            (summary, datetime.now(UTC).isoformat(), bid, self.tenant_id),
        )
        await self.db.commit()

    async def search_bookmarks(
        self, query: str | None = None, tag: str | None = None, limit: int = 20
    ) -> list[Bookmark]:
        """Search bookmarks by text or tag within this tenant."""
        if tag:
            cursor = await self.db.execute(
                """SELECT b.* FROM bookmarks b
                   JOIN bookmark_tags bt ON b.id = bt.bookmark_id
                   JOIN tags t ON bt.tag_id = t.id
                   WHERE t.slug = ? AND b.tenant_id = ?
                   ORDER BY b.updated_at DESC LIMIT ?""",
                (tag, self.tenant_id, limit),
            )
        elif query:
            cursor = await self.db.execute(
                """SELECT * FROM bookmarks
                   WHERE tenant_id = ?
                     AND (title LIKE ? OR description LIKE ? OR url LIKE ?)
                   ORDER BY updated_at DESC LIMIT ?""",
                (self.tenant_id, f"%{query}%", f"%{query}%", f"%{query}%", limit),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM bookmarks WHERE tenant_id = ? ORDER BY updated_at DESC LIMIT ?",
                (self.tenant_id, limit),
            )

        rows = await cursor.fetchall()
        return [await self._row_to_bookmark(r) for r in rows]

    async def get_stats(self) -> dict:
        """Return knowledge base stats for this tenant."""
        bk = await self.db.execute(
            "SELECT COUNT(*) as c FROM bookmarks WHERE tenant_id = ?", (self.tenant_id,)
        )
        tg = await self.db.execute(
            "SELECT COUNT(*) as c FROM tags WHERE tenant_id = ?", (self.tenant_id,)
        )
        bk_row = await bk.fetchone()
        tg_row = await tg.fetchone()
        return {
            "total_bookmarks": bk_row["c"],
            "total_tags": tg_row["c"],
        }

    # ── Management operations ─────────────────────────────────────────

    async def delete_bookmark(self, bookmark_id: int | str) -> bool:
        """Delete a bookmark and its tag associations within this tenant. Returns True if found."""
        bid = _coerce_sqlite_bookmark_id(bookmark_id)
        if bid is None:
            return False
        cursor = await self.db.execute(
            "SELECT id FROM bookmarks WHERE id = ? AND tenant_id = ?", (bid, self.tenant_id)
        )
        if not await cursor.fetchone():
            return False

        # Get associated tag IDs before deleting to update usage counts
        tag_cursor = await self.db.execute(
            "SELECT tag_id FROM bookmark_tags WHERE bookmark_id = ?", (bid,)
        )
        tag_ids = [r["tag_id"] for r in await tag_cursor.fetchall()]

        await self.db.execute("DELETE FROM bookmark_tags WHERE bookmark_id = ?", (bid,))
        await self.db.execute("DELETE FROM bookmarks WHERE id = ?", (bid,))

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
        """Update a tag's name and/or description within this tenant. Returns updated tag or None."""
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

        params.extend([slug, self.tenant_id])
        await self.db.execute(
            f"UPDATE tags SET {', '.join(updates)} WHERE slug = ? AND tenant_id = ?",
            params,
        )
        await self.db.commit()
        return await self.get_tag_by_slug(slug)

    async def delete_tag(self, slug: str) -> bool:
        """Delete a tag within this tenant and remove it from all bookmarks. Returns True if found."""
        tag = await self.get_tag_by_slug(slug)
        if not tag or not tag.id:
            return False

        await self.db.execute("DELETE FROM bookmark_tags WHERE tag_id = ?", (tag.id,))
        await self.db.execute(
            "DELETE FROM tags WHERE id = ? AND tenant_id = ?", (tag.id, self.tenant_id)
        )
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
        await self.db.execute("DELETE FROM bookmark_tags WHERE tag_id = ?", (source.id,))
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
        """Return every bookmark with tags for this tenant (for export)."""
        cursor = await self.db.execute(
            "SELECT * FROM bookmarks WHERE tenant_id = ? ORDER BY created_at ASC",
            (self.tenant_id,),
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
            "exported_at": datetime.now(UTC).isoformat(),
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

    # ── Usage / subscriptions / RAG (SQLite) ──────────────────────────

    async def record_usage_event(
        self, event_type: str, tenant_id: str = "default", metadata: dict | None = None
    ) -> None:
        await self.db.execute(
            "INSERT INTO usage_events (event_type, tenant_id, metadata) VALUES (?, ?, ?)",
            (event_type, tenant_id, json.dumps(metadata or {}, default=str)),
        )
        await self.db.commit()

    async def count_usage_events_month(self, tenant_id: str = "default") -> int:
        prefix = datetime.now(UTC).strftime("%Y-%m")
        cursor = await self.db.execute(
            "SELECT COUNT(*) as c FROM usage_events WHERE tenant_id = ? AND created_at LIKE ?",
            (tenant_id, f"{prefix}%"),
        )
        row = await cursor.fetchone()
        return int(row["c"]) if row else 0

    async def upsert_subscription_state(
        self,
        customer_id: str,
        status: str | None,
        plan_sku: str | None,
        current_period_end: str | None,
        raw_json: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            """INSERT INTO subscription_state (customer_id, status, plan_sku, current_period_end, raw_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(customer_id) DO UPDATE SET
                 status = excluded.status,
                 plan_sku = excluded.plan_sku,
                 current_period_end = excluded.current_period_end,
                 raw_json = excluded.raw_json,
                 updated_at = excluded.updated_at""",
            (customer_id, status, plan_sku, current_period_end, raw_json, now),
        )
        await self.db.commit()

    async def get_subscription_state(self, customer_id: str) -> dict | None:
        cursor = await self.db.execute(
            "SELECT * FROM subscription_state WHERE customer_id = ?", (customer_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def upsert_bookmark_embedding(
        self, bookmark_id: int, model: str, vector: list[float]
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self.db.execute(
            """INSERT INTO bookmark_embeddings (bookmark_id, model, vector_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(bookmark_id, model) DO UPDATE SET
                 vector_json = excluded.vector_json,
                 updated_at = excluded.updated_at""",
            (bookmark_id, model, json.dumps(vector), now),
        )
        await self.db.commit()

    async def get_all_embeddings(self, model: str) -> list[tuple[int, list[float]]]:
        cursor = await self.db.execute(
            "SELECT bookmark_id, vector_json FROM bookmark_embeddings WHERE model = ?",
            (model,),
        )
        rows = await cursor.fetchall()
        return [(int(r["bookmark_id"]), json.loads(r["vector_json"])) for r in rows]

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
