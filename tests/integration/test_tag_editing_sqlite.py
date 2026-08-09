"""SQLite backend: replace-set tag editing, snapshot fields, tag_edits log (Phase 1).

SQLite parity exists BECAUSE the endpoint test suite runs on SQLite — shapes
here must mirror the DynamoDB backend exactly (camelCase wire keys).
"""

from __future__ import annotations


async def _seed(db, url="https://example.com/a", tags=()):
    bm = await db.upsert_bookmark(url=url, title="T")
    for slug in tags:
        await db.create_tag(slug, slug)
    if tags:
        await db.tag_bookmark(bm.id, list(tags))
    return bm.id


async def test_replace_returns_diff(db):
    bid = await _seed(db, tags=("python", "web"))
    result = await db.replace_bookmark_tags(bid, ["python", "rust-lang"])
    assert result == {
        "bookmark_id": bid,
        "before": ["python", "web"],  # _get_bookmark_tags orders by slug
        "after": ["python", "rust-lang"],
        "added": ["rust-lang"],
        "removed": ["web"],
    }


async def test_replace_missing_bookmark_returns_none(db):
    assert await db.replace_bookmark_tags(9999, ["a"]) is None


async def test_snapshot_written_once_by_first_mutation(db):
    bid = await _seed(db, tags=("python",))
    await db.replace_bookmark_tags(bid, ["web"])
    await db.replace_bookmark_tags(bid, ["rust-lang"])
    row = next(r for r in await db.get_recent_bookmarks() if r["id"] == bid)
    assert row["aiTagsOriginal"] == ["python"]  # immutable first snapshot
    assert row["aiTags"] == ["rust-lang"]


async def test_tags_reviewed_at_first_human_edit_only(db):
    bid = await _seed(db, tags=("python",))
    await db.replace_bookmark_tags(bid, ["web"], actor="human")
    first = (await db.get_recent_bookmarks())[0]["tagsReviewedAt"]
    assert first
    await db.replace_bookmark_tags(bid, ["rust-lang"], actor="human")
    assert (await db.get_recent_bookmarks())[0]["tagsReviewedAt"] == first


async def test_non_human_actor_snapshots_without_reviewed_at(db):
    bid = await _seed(db, tags=("python",))
    await db.replace_bookmark_tags(bid, ["web"], actor="recalibrate")
    row = (await db.get_recent_bookmarks())[0]
    assert row["aiTagsOriginal"] == ["python"]  # snapshot: ANY first mutation
    assert row["tagsReviewedAt"] is None  # reviewed-at: human edits only


async def test_replace_creates_missing_tags_and_recounts_usage(db):
    bid = await _seed(db, tags=("python",))
    await db.replace_bookmark_tags(bid, ["brand-new"])
    created = await db.get_tag_by_slug("brand-new")
    assert created is not None and created.usage_count == 1
    old = await db.get_tag_by_slug("python")
    assert old is not None and old.usage_count == 0


async def test_edit_log_newest_first_with_full_shape(db):
    bid = await _seed(db, tags=("python",))
    await db.replace_bookmark_tags(bid, ["web"])
    await db.replace_bookmark_tags(bid, ["rust-lang"])
    edits = await db.get_tag_edits()
    assert len(edits) == 2
    assert edits[0]["after"] == ["rust-lang"]  # newest first
    oldest = edits[1]
    assert oldest["bookmarkId"] == str(bid)
    assert oldest["before"] == ["python"]
    assert oldest["after"] == ["web"]
    assert oldest["added"] == ["web"]
    assert oldest["removed"] == ["python"]
    assert oldest["actor"] == "human"
    assert oldest["ts"]


async def test_recent_bookmarks_shape_and_limit(db):
    for i in range(3):
        await db.upsert_bookmark(url=f"https://example.com/{i}", title=f"B{i}")
    rows = await db.get_recent_bookmarks(limit=2)
    assert len(rows) == 2
    assert set(rows[0]) == {"id", "url", "title", "aiTags", "aiTagsOriginal", "tagsReviewedAt"}
    assert rows[0]["aiTagsOriginal"] is None  # null where absent
