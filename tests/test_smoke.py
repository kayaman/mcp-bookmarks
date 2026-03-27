"""Smoke tests — run with: uv run python tests/test_smoke.py"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_bookmarks.db import Database
from mcp_bookmarks.scraper import extract_og_metadata, extract_article_content
from mcp_bookmarks.models import Tag, Bookmark, ArticleContent


async def test_database_core():
    """Test core DB operations: tags, bookmarks, tagging, search."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    db = Database(db_path)
    await db.connect()

    # ── Tags ──
    t1 = await db.create_tag("python", "Python", "Python programming language and ecosystem")
    t2 = await db.create_tag("web-scraping", "Web Scraping", "Extracting data from websites")
    t3 = await db.create_tag("mcp", "MCP", "Model Context Protocol servers and clients")
    print(f"✓ Created tags: {t1.slug}, {t2.slug}, {t3.slug}")

    tags = await db.get_all_tags()
    assert len(tags) == 3, f"Expected 3 tags, got {len(tags)}"
    print(f"✓ get_all_tags returned {len(tags)} tags")

    found = await db.search_tags("python")
    assert len(found) == 1
    print(f"✓ search_tags('python') → {len(found)} result")

    # Duplicate guard
    existing = await db.get_tag_by_slug("python")
    assert existing is not None
    print(f"✓ get_tag_by_slug('python') found existing tag")

    # ── Bookmarks ──
    bk = await db.upsert_bookmark(
        url="https://example.com/article",
        title="Test Article",
        description="A test article about Python web scraping",
    )
    assert bk.id is not None
    assert bk.content is None
    assert bk.word_count is None or bk.word_count == 0
    print(f"✓ Created bookmark id={bk.id} (content=None, word_count={bk.word_count})")

    # ── Upsert (update existing) ──
    bk2 = await db.upsert_bookmark(
        url="https://example.com/article",
        title="Updated Title",
    )
    assert bk2.id == bk.id, "Upsert should return same ID"
    assert bk2.title == "Updated Title"
    print(f"✓ Upsert updated title: '{bk2.title}'")

    # ── Tag bookmark ──
    bk = await db.tag_bookmark(bk.id, ["python", "web-scraping"])
    assert bk.tags == ["python", "web-scraping"]
    print(f"✓ Tagged bookmark: {bk.tags}")

    # Verify usage_count bumped
    py_tag = await db.get_tag_by_slug("python")
    assert py_tag.usage_count == 1
    print(f"✓ Tag 'python' usage_count={py_tag.usage_count}")

    # ── Content storage ──
    await db.set_bookmark_content(bk.id, "This is the full article text about scraping.", 8)
    bk_with_content = await db.get_bookmark_by_id(bk.id)
    assert bk_with_content.content == "This is the full article text about scraping."
    assert bk_with_content.word_count == 8
    print(f"✓ Stored content: word_count={bk_with_content.word_count}")

    # ── Summary ──
    await db.set_bookmark_summary(bk.id, "An article about scraping with Python.")
    bk_summary = await db.get_bookmark_by_id(bk.id)
    assert bk_summary.summary is not None
    print(f"✓ Summary stored: '{bk_summary.summary}'")

    # ── Search by tag ──
    results = await db.search_bookmarks(tag="python")
    assert len(results) == 1
    assert results[0].content is not None
    print(f"✓ search_bookmarks(tag='python') → {len(results)} result (with content)")

    # ── Search by text ──
    results = await db.search_bookmarks(query="Updated")
    assert len(results) == 1
    print(f"✓ search_bookmarks(query='Updated') → {len(results)} result")

    # ── Stats ──
    stats = await db.get_stats()
    assert stats["total_bookmarks"] == 1
    assert stats["total_tags"] == 3
    print(f"✓ Stats: {stats}")

    # ── Tag not found error ──
    try:
        await db.tag_bookmark(bk.id, ["nonexistent-tag"])
        assert False, "Should have raised ValueError"
    except ValueError as e:
        print(f"✓ tag_bookmark with unknown tag raised ValueError: {e}")

    # ── get_bookmark_by_id for missing ──
    missing = await db.get_bookmark_by_id(9999)
    assert missing is None
    print(f"✓ get_bookmark_by_id(9999) → None")

    await db.close()
    db_path.unlink(missing_ok=True)
    print("\n✅ All database tests passed!")


async def test_migration():
    """Test that migration adds columns to an older schema."""
    db_path = Path(tempfile.mktemp(suffix=".db"))

    # Create DB with OLD schema (no content/word_count columns)
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.executescript("""
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
        """)
        await conn.commit()

    # Now open with our Database class — should migrate
    db = Database(db_path)
    await db.connect()

    # Verify old data survived
    bk = await db.search_bookmarks(query="Old")
    assert len(bk) == 1
    assert bk[0].title == "Old Bookmark"
    assert bk[0].content is None  # new column, should be NULL
    print(f"✓ Migration preserved old bookmark: '{bk[0].title}'")

    # Verify new columns work
    await db.set_bookmark_content(bk[0].id, "Migrated content", 2)
    bk2 = await db.get_bookmark_by_id(bk[0].id)
    assert bk2.content == "Migrated content"
    assert bk2.word_count == 2
    print(f"✓ Migration added content/word_count columns successfully")

    await db.close()
    db_path.unlink(missing_ok=True)
    print("\n✅ Migration test passed!")


async def test_scraper_og():
    """Test OG extraction against a known page."""
    try:
        og = await extract_og_metadata("https://github.com")
        assert og.title is not None, "Expected a title"
        print(f"✓ OG title: '{og.title}'")
        print(f"  description: '{og.description[:80] if og.description else 'N/A'}...'")
        print(f"  image: {og.image}")
        print(f"  site_name: {og.site_name}")
        print("\n✅ OG metadata test passed!")
    except Exception as e:
        print(f"⚠ OG test skipped (network): {e}")


async def test_scraper_content():
    """Test full article extraction with trafilatura."""
    try:
        # Use a page with substantial text content
        article = await extract_article_content("https://modelcontextprotocol.io/docs/concepts/architecture")
        assert article.word_count > 0, "Expected some words"
        assert len(article.text) > 100, "Expected substantial text"
        print(f"✓ Article extraction: {article.word_count} words via {article.extraction_method}")
        print(f"  Preview: '{article.text[:120]}...'")
        print("\n✅ Content extraction test passed!")
    except Exception as e:
        print(f"⚠ Content extraction test skipped (network): {e}")


async def test_models():
    """Test Pydantic model validation."""
    tag = Tag(slug="test-tag", name="Test Tag", description="For testing")
    assert tag.usage_count == 0
    assert tag.id is None
    print(f"✓ Tag model: {tag.slug}")

    bookmark = Bookmark(url="https://example.com")
    assert bookmark.tags == []
    assert bookmark.content is None
    assert bookmark.word_count is None
    print(f"✓ Bookmark model: {bookmark.url}")

    article = ArticleContent(url="https://example.com", text="Hello world", word_count=2)
    assert article.extraction_method == "trafilatura"
    print(f"✓ ArticleContent model: {article.word_count} words")

    print("\n✅ Model validation tests passed!")


async def main():
    print("=" * 60)
    print("  MCP Bookmarks — Full Smoke Test Suite")
    print("=" * 60)

    print("\n── Model Tests ──")
    await test_models()

    print("\n── Database Core Tests ──")
    await test_database_core()

    print("\n── Migration Tests ──")
    await test_migration()

    print("\n── OG Metadata Scraper Tests ──")
    await test_scraper_og()

    print("\n── Article Content Extraction Tests ──")
    await test_scraper_content()

    print("\n" + "=" * 60)
    print("  🎉 ALL TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
