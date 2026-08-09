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


# ── Tombstones (Phase 2) ──────────────────────────────────────────────


async def test_merge_tombstones_source_instead_of_deleting(db):
    bid = await _seed(db, tags=("machine-learning", "ml-engineering"))
    await db.merge_tags("machine-learning", "ml-engineering")
    src = await db.get_tag_by_slug("machine-learning")
    assert src is not None  # row still exists — never hard-deleted by merge
    assert src.deprecated_as == "ml-engineering"
    assert src.usage_count == 0
    bm = await db.get_bookmark_by_id(bid)
    assert "ml-engineering" in bm.tags and "machine-learning" not in bm.tags


async def test_get_all_tags_and_search_filter_tombstoned(db):
    await db.create_tag("live-tag", "live-tag")
    await db.create_tag("dead-tag", "dead-tag")
    await db.tombstone_tag("dead-tag", "live-tag")
    assert [t.slug for t in await db.get_all_tags()] == ["live-tag"]
    assert [t.slug for t in await db.search_tags("tag")] == ["live-tag"]


async def test_get_tag_by_slug_still_returns_tombstoned_row(db):
    await db.create_tag("old-name", "old-name")
    await db.tombstone_tag("old-name", "new-name")
    tag = await db.get_tag_by_slug("old-name")
    assert tag is not None and tag.deprecated_as == "new-name"
    live = await db.get_tag_by_slug("live-missing")
    assert live is None  # never-existed stays None


async def test_delete_tag_still_hard_deletes(db):
    await db.create_tag("doomed-tag", "doomed-tag")
    assert await db.delete_tag("doomed-tag") is True
    assert await db.get_tag_by_slug("doomed-tag") is None


async def test_migrate_adds_deprecated_as_to_legacy_db(tmp_path):
    """Old DBs created before Phase 2 lack the column; _migrate adds it."""
    import sqlite3

    from mcp_bookmarks.db import Database

    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT NOT NULL,"
        " tenant_id TEXT NOT NULL DEFAULT 'default', name TEXT NOT NULL,"
        " description TEXT DEFAULT '', usage_count INTEGER DEFAULT 0,"
        " created_at TEXT, UNIQUE (slug, tenant_id))"
    )
    conn.commit()
    conn.close()
    db = Database(p)
    await db.connect()
    try:
        await db.create_tag("fresh-tag", "fresh-tag")
        assert [t.slug for t in await db.get_all_tags()] == ["fresh-tag"]
    finally:
        await db.close()


# ── Recalibrate sweep helpers (Phase 2) ───────────────────────────────


async def test_count_bookmarks_with_tag(db):
    await _seed(db, url="https://example.com/1", tags=("python",))
    await _seed(db, url="https://example.com/2", tags=("web",))
    b3 = await db.upsert_bookmark(url="https://example.com/3", title="T")
    await db.tag_bookmark(b3.id, ["python"])
    assert await db.count_bookmarks_with_tag("python") == 2
    assert await db.count_bookmarks_with_tag("web") == 1
    assert await db.count_bookmarks_with_tag("never-existed") == 0


async def test_get_bookmarks_with_any_tag_returns_id_and_tags(db):
    b1 = await _seed(db, url="https://example.com/1", tags=("python", "web"))
    await _seed(db, url="https://example.com/2", tags=("rust-lang",))
    rows = await db.get_bookmarks_with_any_tag(["python"])
    assert rows == [{"id": b1, "tags": ["python", "web"]}]  # tags ordered by slug
    assert await db.get_bookmarks_with_any_tag([]) == []
    both = await db.get_bookmarks_with_any_tag(["python", "rust-lang"])
    assert len(both) == 2
