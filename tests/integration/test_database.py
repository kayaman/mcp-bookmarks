"""SQLite database core operations + migration from older schema.

Converted from the original tests/test_smoke.py smoke script (WDN-395).
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from mcp_bookmarks.db import Database

pytestmark = pytest.mark.asyncio


# ── Core flow ──────────────────────────────────────────────────────


async def test_database_core_flow(db):
    """End-to-end: tags → upsert bookmark → tag → content → summary → search."""
    t1 = await db.create_tag("python", "Python", "Python programming language and ecosystem")
    t2 = await db.create_tag("web-scraping", "Web Scraping", "Extracting data from websites")
    t3 = await db.create_tag("mcp", "MCP", "Model Context Protocol servers and clients")
    assert {t1.slug, t2.slug, t3.slug} == {"python", "web-scraping", "mcp"}

    tags = await db.get_all_tags()
    assert len(tags) == 3

    found = await db.search_tags("python")
    assert len(found) == 1
    assert (await db.get_tag_by_slug("python")) is not None

    bk = await db.upsert_bookmark(
        url="https://example.com/article",
        title="Test Article",
        description="A test article about Python web scraping",
    )
    assert bk.id is not None
    assert bk.content is None
    assert bk.word_count is None or bk.word_count == 0

    # Upsert with the same URL updates rather than inserts.
    bk2 = await db.upsert_bookmark(url="https://example.com/article", title="Updated Title")
    assert bk2.id == bk.id
    assert bk2.title == "Updated Title"

    bk = await db.tag_bookmark(bk.id, ["python", "web-scraping"])
    assert bk.tags == ["python", "web-scraping"]
    assert (await db.get_tag_by_slug("python")).usage_count == 1

    await db.set_bookmark_content(bk.id, "This is the full article text about scraping.", 8)
    bk_with_content = await db.get_bookmark_by_id(bk.id)
    assert bk_with_content.content == "This is the full article text about scraping."
    assert bk_with_content.word_count == 8

    await db.set_bookmark_summary(bk.id, "An article about scraping with Python.")
    bk_summary = await db.get_bookmark_by_id(bk.id)
    assert bk_summary.summary is not None

    by_tag = await db.search_bookmarks(tag="python")
    assert len(by_tag) == 1
    assert by_tag[0].content is not None

    by_query = await db.search_bookmarks(query="Updated")
    assert len(by_query) == 1

    stats = await db.get_stats()
    assert stats["total_bookmarks"] == 1
    assert stats["total_tags"] == 3


async def test_tag_bookmark_with_unknown_tag_raises(db):
    bk = await db.upsert_bookmark(url="https://example.com/a", title="A")
    with pytest.raises(ValueError):
        await db.tag_bookmark(bk.id, ["nonexistent-tag"])


async def test_get_bookmark_by_id_returns_none_for_missing(db):
    assert await db.get_bookmark_by_id(9999) is None


# ── Migration ──────────────────────────────────────────────────────


async def test_migration_adds_content_and_word_count_columns(tmp_db_path: Path):
    """An older schema without content/word_count columns is migrated in place."""
    async with aiosqlite.connect(str(tmp_db_path)) as conn:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                usage_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                title TEXT,
                description TEXT,
                image_url TEXT,
                site_name TEXT,
                summary TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bookmark_tags (
                bookmark_id INTEGER NOT NULL REFERENCES bookmarks(id),
                tag_id INTEGER NOT NULL REFERENCES tags(id),
                PRIMARY KEY (bookmark_id, tag_id)
            );
            INSERT INTO bookmarks (url, title) VALUES ('https://old.com', 'Old Bookmark');
            """
        )
        await conn.commit()

    db = Database(tmp_db_path)
    await db.connect()
    try:
        # Old data survives the connect-time migration.
        results = await db.search_bookmarks(query="Old")
        assert len(results) == 1
        assert results[0].title == "Old Bookmark"
        assert results[0].content is None

        # New columns are writable.
        await db.set_bookmark_content(results[0].id, "Migrated content", 2)
        migrated = await db.get_bookmark_by_id(results[0].id)
        assert migrated.content == "Migrated content"
        assert migrated.word_count == 2
    finally:
        await db.close()
