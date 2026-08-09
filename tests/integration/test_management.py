"""Tag and bookmark management operations against the SQLite backend.

Converted from the original tests/test_management.py smoke script (WDN-395).
Uses the shared ``db`` fixture from tests/conftest.py.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# ── delete_bookmark ────────────────────────────────────────────────


async def test_delete_bookmark_recalculates_tag_usage(db):
    """Deleting a bookmark drops the per-tag usage counts that referenced it."""
    await db.create_tag("python", "Python", "Python lang")
    await db.create_tag("testing", "Testing", "Testing practices")
    bk1 = await db.upsert_bookmark(url="https://a.com", title="Article A")
    bk2 = await db.upsert_bookmark(url="https://b.com", title="Article B")
    await db.tag_bookmark(bk1.id, ["python", "testing"])
    await db.tag_bookmark(bk2.id, ["python"])

    assert (await db.get_tag_by_slug("python")).usage_count == 2

    deleted = await db.delete_bookmark(bk1.id)
    assert deleted is True

    assert (await db.get_tag_by_slug("python")).usage_count == 1
    assert (await db.get_tag_by_slug("testing")).usage_count == 0
    assert await db.get_bookmark_by_id(bk2.id) is not None


async def test_delete_bookmark_returns_false_when_missing(db):
    assert await db.delete_bookmark(9999) is False


# ── update_tag ─────────────────────────────────────────────────────


async def test_update_tag_full(db):
    await db.create_tag("ml", "ML", "Machine learning stuff")
    updated = await db.update_tag(
        "ml",
        new_name="Machine Learning",
        new_description="General ML concepts and algorithms",
    )
    assert updated is not None
    assert updated.name == "Machine Learning"
    assert updated.description == "General ML concepts and algorithms"
    assert updated.slug == "ml"  # slug never changes on update


async def test_update_tag_preserves_unset_fields(db):
    await db.create_tag("ml", "Machine Learning", "Initial scope")
    updated = await db.update_tag("ml", new_description="Updated scope")
    assert updated.name == "Machine Learning"
    assert updated.description == "Updated scope"


async def test_update_tag_returns_none_when_missing(db):
    assert await db.update_tag("nonexistent", new_name="Nope") is None


# ── delete_tag ─────────────────────────────────────────────────────


async def test_delete_tag_removes_from_bookmarks(db):
    await db.create_tag("deprecated", "Deprecated", "Old tag")
    await db.create_tag("keeper", "Keeper", "This stays")
    bk = await db.upsert_bookmark(url="https://a.com", title="Article")
    await db.tag_bookmark(bk.id, ["deprecated", "keeper"])

    assert await db.delete_tag("deprecated") is True

    bk_check = await db.get_bookmark_by_id(bk.id)
    assert "deprecated" not in bk_check.tags
    assert "keeper" in bk_check.tags
    assert await db.get_tag_by_slug("deprecated") is None


# ── merge_tags ─────────────────────────────────────────────────────


async def test_merge_tags_reassigns_bookmarks(db):
    await db.create_tag("ml", "ML", "Shorthand")
    await db.create_tag("machine-learning", "Machine Learning", "Full name")
    bk1 = await db.upsert_bookmark(url="https://a.com", title="Article A")
    bk2 = await db.upsert_bookmark(url="https://b.com", title="Article B")
    bk3 = await db.upsert_bookmark(url="https://c.com", title="Article C")
    await db.tag_bookmark(bk1.id, ["ml"])
    await db.tag_bookmark(bk2.id, ["ml", "machine-learning"])  # double-tagged
    await db.tag_bookmark(bk3.id, ["machine-learning"])

    result = await db.merge_tags("ml", "machine-learning")
    assert result["source_deleted"] == "ml"
    assert result["target"] == "machine-learning"
    assert result["bookmarks_reassigned"] == 2  # bk1 + bk2

    # Phase 2: merge tombstones the source row instead of hard-deleting it
    # (deprecated_as records the redirect target) — never None afterward.
    src = await db.get_tag_by_slug("ml")
    assert src is not None
    assert src.deprecated_as == "machine-learning"
    assert src.usage_count == 0
    for bk_id in (bk1.id, bk2.id, bk3.id):
        bk = await db.get_bookmark_by_id(bk_id)
        assert "machine-learning" in bk.tags
        assert "ml" not in bk.tags

    target = await db.get_tag_by_slug("machine-learning")
    assert target.usage_count == 3


async def test_merge_tags_raises_when_source_missing(db):
    await db.create_tag("machine-learning", "Machine Learning", "Full")
    with pytest.raises(ValueError):
        await db.merge_tags("nonexistent", "machine-learning")


async def test_merge_tags_raises_when_target_tombstoned(db):
    """Merging into an already-tombstoned target must error like an unknown target."""
    await db.create_tag("source-ok", "Source", "")
    await db.create_tag("old-target", "Old Target", "")
    await db.tombstone_tag("old-target", "new-target")
    with pytest.raises(ValueError, match="Target tag 'old-target' not found"):
        await db.merge_tags("source-ok", "old-target")


# ── untag_bookmark ─────────────────────────────────────────────────


async def test_untag_bookmark_drops_only_named_tags(db):
    await db.create_tag("python", "Python", "")
    await db.create_tag("web", "Web", "")
    await db.create_tag("api", "API", "")
    bk = await db.upsert_bookmark(url="https://a.com", title="Article")
    await db.tag_bookmark(bk.id, ["python", "web", "api"])

    result = await db.untag_bookmark(bk.id, ["web"])
    assert "web" not in result.tags
    assert "python" in result.tags
    assert "api" in result.tags
    assert (await db.get_tag_by_slug("web")).usage_count == 0


async def test_untag_bookmark_no_op_for_unknown_tag(db):
    await db.create_tag("python", "Python", "")
    bk = await db.upsert_bookmark(url="https://a.com", title="Article")
    await db.tag_bookmark(bk.id, ["python"])

    result = await db.untag_bookmark(bk.id, ["nonexistent"])
    assert result.tags == ["python"]


async def test_untag_bookmark_returns_none_for_missing_bookmark(db):
    assert await db.untag_bookmark(9999, ["python"]) is None
