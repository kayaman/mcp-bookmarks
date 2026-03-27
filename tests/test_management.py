"""Integration tests for management operations — run with: python tests/test_management.py"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_bookmarks.db import Database


async def test_delete_bookmark():
    """Test deleting a bookmark recalculates tag usage."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    db = Database(db_path)
    await db.connect()

    # Setup
    await db.create_tag("python", "Python", "Python lang")
    await db.create_tag("testing", "Testing", "Testing practices")
    bk1 = await db.upsert_bookmark(url="https://a.com", title="Article A")
    bk2 = await db.upsert_bookmark(url="https://b.com", title="Article B")
    await db.tag_bookmark(bk1.id, ["python", "testing"])
    await db.tag_bookmark(bk2.id, ["python"])

    # Verify initial state
    py_tag = await db.get_tag_by_slug("python")
    assert py_tag.usage_count == 2
    print(f"✓ Initial state: 'python' usage_count=2")

    # Delete bookmark A
    deleted = await db.delete_bookmark(bk1.id)
    assert deleted is True
    print(f"✓ Deleted bookmark A")

    # Verify usage counts updated
    py_tag = await db.get_tag_by_slug("python")
    assert py_tag.usage_count == 1, f"Expected 1, got {py_tag.usage_count}"
    print(f"✓ 'python' usage_count dropped to {py_tag.usage_count}")

    test_tag = await db.get_tag_by_slug("testing")
    assert test_tag.usage_count == 0
    print(f"✓ 'testing' usage_count dropped to {test_tag.usage_count}")

    # Bookmark B still exists
    bk2_check = await db.get_bookmark_by_id(bk2.id)
    assert bk2_check is not None
    print(f"✓ Bookmark B still exists")

    # Delete nonexistent
    deleted = await db.delete_bookmark(9999)
    assert deleted is False
    print(f"✓ Deleting nonexistent bookmark returns False")

    await db.close()
    db_path.unlink(missing_ok=True)
    print("\n✅ delete_bookmark tests passed!")


async def test_update_tag():
    """Test updating tag name and description."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    db = Database(db_path)
    await db.connect()

    await db.create_tag("ml", "ML", "Machine learning stuff")

    # Update both
    updated = await db.update_tag("ml", new_name="Machine Learning", new_description="General ML concepts and algorithms")
    assert updated is not None
    assert updated.name == "Machine Learning"
    assert updated.description == "General ML concepts and algorithms"
    assert updated.slug == "ml"  # slug unchanged
    print(f"✓ Updated tag: name='{updated.name}', desc='{updated.description[:40]}...'")

    # Update only description
    updated = await db.update_tag("ml", new_description="Updated scope")
    assert updated.name == "Machine Learning"  # name preserved
    assert updated.description == "Updated scope"
    print(f"✓ Partial update preserved name: '{updated.name}'")

    # Update nonexistent
    result = await db.update_tag("nonexistent", new_name="Nope")
    assert result is None
    print(f"✓ Updating nonexistent tag returns None")

    await db.close()
    db_path.unlink(missing_ok=True)
    print("\n✅ update_tag tests passed!")


async def test_delete_tag():
    """Test deleting a tag removes it from bookmarks."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    db = Database(db_path)
    await db.connect()

    await db.create_tag("deprecated", "Deprecated", "Old tag")
    await db.create_tag("keeper", "Keeper", "This stays")
    bk = await db.upsert_bookmark(url="https://a.com", title="Article")
    await db.tag_bookmark(bk.id, ["deprecated", "keeper"])

    # Verify both tags assigned
    bk_check = await db.get_bookmark_by_id(bk.id)
    assert "deprecated" in bk_check.tags
    assert "keeper" in bk_check.tags
    print(f"✓ Bookmark has both tags: {bk_check.tags}")

    # Delete tag
    deleted = await db.delete_tag("deprecated")
    assert deleted is True
    print(f"✓ Deleted tag 'deprecated'")

    # Verify bookmark lost the tag
    bk_check = await db.get_bookmark_by_id(bk.id)
    assert "deprecated" not in bk_check.tags
    assert "keeper" in bk_check.tags
    print(f"✓ Bookmark now has: {bk_check.tags}")

    # Tag gone from DB
    gone = await db.get_tag_by_slug("deprecated")
    assert gone is None
    print(f"✓ Tag 'deprecated' no longer in DB")

    await db.close()
    db_path.unlink(missing_ok=True)
    print("\n✅ delete_tag tests passed!")


async def test_merge_tags():
    """Test merging source tag into target."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    db = Database(db_path)
    await db.connect()

    await db.create_tag("ml", "ML", "Shorthand")
    await db.create_tag("machine-learning", "Machine Learning", "Full name")
    bk1 = await db.upsert_bookmark(url="https://a.com", title="Article A")
    bk2 = await db.upsert_bookmark(url="https://b.com", title="Article B")
    bk3 = await db.upsert_bookmark(url="https://c.com", title="Article C")

    await db.tag_bookmark(bk1.id, ["ml"])
    await db.tag_bookmark(bk2.id, ["ml", "machine-learning"])  # has both
    await db.tag_bookmark(bk3.id, ["machine-learning"])

    print(f"  Setup: bk1=[ml], bk2=[ml, machine-learning], bk3=[machine-learning]")

    # Merge ml → machine-learning
    result = await db.merge_tags("ml", "machine-learning")
    assert result["source_deleted"] == "ml"
    assert result["target"] == "machine-learning"
    assert result["bookmarks_reassigned"] == 2  # bk1 and bk2
    print(f"✓ Merge result: {result}")

    # Source tag gone
    gone = await db.get_tag_by_slug("ml")
    assert gone is None
    print(f"✓ Source tag 'ml' deleted")

    # All bookmarks now have machine-learning
    for bk_id, name in [(bk1.id, "A"), (bk2.id, "B"), (bk3.id, "C")]:
        bk = await db.get_bookmark_by_id(bk_id)
        assert "machine-learning" in bk.tags, f"Bookmark {name} missing tag"
        assert "ml" not in bk.tags, f"Bookmark {name} still has 'ml'"
    print(f"✓ All bookmarks now tagged with 'machine-learning'")

    # Usage count correct
    target = await db.get_tag_by_slug("machine-learning")
    assert target.usage_count == 3
    print(f"✓ Target usage_count={target.usage_count}")

    # Error on nonexistent source
    try:
        await db.merge_tags("nonexistent", "machine-learning")
        assert False, "Should have raised"
    except ValueError:
        print(f"✓ Merging nonexistent source raises ValueError")

    await db.close()
    db_path.unlink(missing_ok=True)
    print("\n✅ merge_tags tests passed!")


async def test_untag_bookmark():
    """Test removing tags from a bookmark."""
    db_path = Path(tempfile.mktemp(suffix=".db"))
    db = Database(db_path)
    await db.connect()

    await db.create_tag("python", "Python", "Python lang")
    await db.create_tag("web", "Web", "Web dev")
    await db.create_tag("api", "API", "APIs and services")
    bk = await db.upsert_bookmark(url="https://a.com", title="Article")
    await db.tag_bookmark(bk.id, ["python", "web", "api"])

    bk_check = await db.get_bookmark_by_id(bk.id)
    assert len(bk_check.tags) == 3
    print(f"✓ Initial tags: {bk_check.tags}")

    # Remove one tag
    result = await db.untag_bookmark(bk.id, ["web"])
    assert "web" not in result.tags
    assert "python" in result.tags
    assert "api" in result.tags
    print(f"✓ After removing 'web': {result.tags}")

    # Usage count updated
    web_tag = await db.get_tag_by_slug("web")
    assert web_tag.usage_count == 0
    print(f"✓ 'web' usage_count dropped to 0")

    # Remove nonexistent tag (no error, just no-op)
    result = await db.untag_bookmark(bk.id, ["nonexistent"])
    assert len(result.tags) == 2
    print(f"✓ Removing nonexistent tag is a no-op")

    # Untag nonexistent bookmark
    result = await db.untag_bookmark(9999, ["python"])
    assert result is None
    print(f"✓ Untagging nonexistent bookmark returns None")

    await db.close()
    db_path.unlink(missing_ok=True)
    print("\n✅ untag_bookmark tests passed!")


async def main():
    print("=" * 60)
    print("  MCP Bookmarks — Management Operations Tests")
    print("=" * 60)

    print("\n── delete_bookmark ──")
    await test_delete_bookmark()

    print("\n── update_tag ──")
    await test_update_tag()

    print("\n── delete_tag ──")
    await test_delete_tag()

    print("\n── merge_tags ──")
    await test_merge_tags()

    print("\n── untag_bookmark ──")
    await test_untag_bookmark()

    print("\n" + "=" * 60)
    print("  🎉 ALL MANAGEMENT TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
