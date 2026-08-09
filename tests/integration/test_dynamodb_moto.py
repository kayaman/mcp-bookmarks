"""moto-backed DynamoDB integration tests for ``DynamoDBDatabase``.

Exercises the real ``DynamoDBDatabase`` class against an in-process moto
DynamoDB mock. moto v5 unifies all AWS service mocks under ``mock_aws``
(``mock_dynamodb`` was the v4 spelling and is gone). The mock is entered as
a context manager around each test via a fixture so the boto3 resource the
class creates in ``__init__`` is wired to moto's in-memory backend.

Module-level table names in ``dynamodb.py`` are captured at import time, so
we patch ``dynamodb._LINKS_TABLE`` / ``dynamodb._TAGS_TABLE`` on the imported
module rather than relying solely on env vars (env vars wouldn't be re-read
because the module was likely imported earlier in the test session).

Out of scope (see CONTRIBUTING.md anti-targets):

  * ``_item_org_visible`` per-request behaviour (needs request context fixtures)
  * boto3 retry / throttling code paths (would need ClientError injection)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


_LINKS_TABLE = "test-mcp-bookmarks-links"
_TAGS_TABLE = "test-mcp-bookmarks-tags"


@pytest.fixture
def dynamodb_setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Enter ``mock_aws``, create the tables ``DynamoDBDatabase`` expects."""
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("DYNAMODB_LINKS_TABLE", _LINKS_TABLE)
    monkeypatch.setenv("DYNAMODB_TAGS_TABLE", _TAGS_TABLE)
    # No org scoping → _org_id() returns None, _item_org_visible always True.
    monkeypatch.delenv("DYNAMODB_ORG_ID", raising=False)
    monkeypatch.delenv("DYNAMODB_ORG_INCLUDE_LEGACY", raising=False)

    with mock_aws():
        import boto3

        from mcp_bookmarks import dynamodb as dynamo_mod

        # Module-level constants were frozen at import time → re-point them.
        monkeypatch.setattr(dynamo_mod, "_LINKS_TABLE", _LINKS_TABLE)
        monkeypatch.setattr(dynamo_mod, "_TAGS_TABLE", _TAGS_TABLE)

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=_LINKS_TABLE,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.create_table(
            TableName=_TAGS_TABLE,
            KeySchema=[{"AttributeName": "slug", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "slug", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


@pytest_asyncio.fixture
async def ddb(dynamodb_setup):
    """Fresh ``DynamoDBDatabase`` bound to the moto-backed tables."""
    from mcp_bookmarks.dynamodb import DynamoDBDatabase

    return DynamoDBDatabase()


# ── Bookmarks: upsert + read-back ─────────────────────────────────────


async def test_upsert_and_get_round_trip_persists_all_fields(ddb):
    bk = await ddb.upsert_bookmark(
        url="https://example.com/post",
        title="A Post",
        description="A short description",
        image_url="https://example.com/cover.jpg",
        site_name="Example",
        bookmark_type="article",
        source="mcp",
    )
    assert bk.dynamo_id is not None

    fetched = await ddb.get_bookmark_by_id(bk.dynamo_id)
    assert fetched is not None
    # canonical OG (camelCase) keys round-trip
    assert fetched.og_title == "A Post"
    assert fetched.og_description == "A short description"
    assert fetched.og_image == "https://example.com/cover.jpg"
    assert fetched.og_site_name == "Example"
    # snake_case aliases populated for legacy consumers
    assert fetched.title == "A Post"
    assert fetched.description == "A short description"
    assert fetched.bookmark_type == "article"
    assert fetched.source == "mcp"


async def test_get_bookmark_by_id_returns_none_for_missing(ddb):
    assert await ddb.get_bookmark_by_id("does-not-exist") is None


async def test_get_bookmark_by_id_returns_none_for_empty_key(ddb):
    assert await ddb.get_bookmark_by_id("   ") is None


# ── delete_bookmark ───────────────────────────────────────────────────


async def test_delete_bookmark_returns_true_for_existing(ddb):
    bk = await ddb.upsert_bookmark(url="https://example.com/del", title="To Delete")
    assert (await ddb.delete_bookmark(bk.dynamo_id)) is True
    assert await ddb.get_bookmark_by_id(bk.dynamo_id) is None


async def test_delete_bookmark_returns_false_for_missing(ddb):
    assert (await ddb.delete_bookmark("missing-id")) is False


# ── content + summary mutators ────────────────────────────────────────


async def test_set_bookmark_content_persists_and_word_count(ddb):
    bk = await ddb.upsert_bookmark(url="https://example.com/c", title="C")
    body = "alpha beta gamma delta epsilon"
    await ddb.set_bookmark_content(bk.dynamo_id, body, 5)
    fetched = await ddb.get_bookmark_by_id(bk.dynamo_id)
    assert fetched.content == body
    assert fetched.word_count == 5


async def test_set_bookmark_content_recomputes_word_count_when_zero(ddb):
    bk = await ddb.upsert_bookmark(url="https://example.com/c2", title="C2")
    await ddb.set_bookmark_content(bk.dynamo_id, "one two three", 0)
    fetched = await ddb.get_bookmark_by_id(bk.dynamo_id)
    assert fetched.word_count == 3


async def test_set_bookmark_summary_round_trip(ddb):
    bk = await ddb.upsert_bookmark(url="https://example.com/s", title="S")
    await ddb.set_bookmark_summary(bk.dynamo_id, "Concise summary text.")
    fetched = await ddb.get_bookmark_by_id(bk.dynamo_id)
    assert fetched.summary == "Concise summary text."
    # ai_summary alias is the same field
    assert fetched.ai_summary == "Concise summary text."


# ── search_bookmarks_paged ────────────────────────────────────────────


async def test_search_paged_empty_cursor_returns_all(ddb):
    await ddb.upsert_bookmark(url="https://a.com/", title="Alpha")
    await ddb.upsert_bookmark(url="https://b.com/", title="Beta")
    results, cursor = await ddb.search_bookmarks_paged(limit=10)
    titles = {b.title for b in results}
    assert titles == {"Alpha", "Beta"}
    # All fit under limit → no continuation.
    assert cursor is None


async def test_search_paged_with_query_filter(ddb):
    await ddb.upsert_bookmark(url="https://a.com/", title="Alpha Python")
    await ddb.upsert_bookmark(url="https://b.com/", title="Beta Rust")
    results, _ = await ddb.search_bookmarks_paged(query="Python", limit=10)
    assert len(results) == 1
    assert results[0].title == "Alpha Python"


async def test_search_paged_with_tag_filter(ddb):
    await ddb.create_tag("python", "Python", "")
    bk = await ddb.upsert_bookmark(url="https://t.com/", title="Tagged")
    await ddb.tag_bookmark(bk.dynamo_id, ["python"])
    await ddb.upsert_bookmark(url="https://u.com/", title="Untagged")

    results, _ = await ddb.search_bookmarks_paged(tag="python", limit=10)
    assert len(results) == 1
    assert results[0].title == "Tagged"


async def test_search_paged_respects_limit_and_returns_cursor(ddb):
    for i in range(5):
        await ddb.upsert_bookmark(url=f"https://x.com/{i}", title=f"Item {i}")
    page1, cursor = await ddb.search_bookmarks_paged(limit=2)
    assert len(page1) == 2
    # moto returns LastEvaluatedKey when Limit is hit before exhausting.
    assert cursor is not None
    page2, _ = await ddb.search_bookmarks_paged(limit=10, cursor=cursor)
    # Continuation should surface the remaining items (3) — exact count varies
    # by moto's internal page boundary, so just assert progress is made.
    assert len(page2) >= 1


# ── Tags ──────────────────────────────────────────────────────────────


async def test_create_tag_and_get_all_round_trip(ddb):
    await ddb.create_tag("python", "Python", "Python language")
    await ddb.create_tag("rust", "Rust", "Rust language")
    all_tags = await ddb.get_all_tags()
    slugs = {t.slug for t in all_tags}
    assert slugs == {"python", "rust"}


async def test_merge_tags_reassigns_bookmarks(ddb):
    await ddb.create_tag("py", "py", "")
    await ddb.create_tag("python", "python", "")
    bk = await ddb.upsert_bookmark(url="https://m.com/", title="M")
    await ddb.tag_bookmark(bk.dynamo_id, ["py"])

    result = await ddb.merge_tags("py", "python")
    assert result["source_deleted"] == "py"
    assert result["target"] == "python"
    assert result["bookmarks_reassigned"] == 1

    # Phase 2: merge tombstones the source row instead of hard-deleting it
    # (deprecated_as records the redirect target) — never None afterward.
    src = await ddb.get_tag_by_slug("py")
    assert src is not None
    assert src.deprecated_as == "python"
    assert src.usage_count == 0
    fetched = await ddb.get_bookmark_by_id(bk.dynamo_id)
    assert "python" in fetched.tags
    assert "py" not in fetched.tags


async def test_delete_tag_removes_tag_and_untags_bookmarks(ddb):
    await ddb.create_tag("doomed", "Doomed", "")
    bk = await ddb.upsert_bookmark(url="https://d.com/", title="D")
    await ddb.tag_bookmark(bk.dynamo_id, ["doomed"])

    assert (await ddb.delete_tag("doomed")) is True
    assert await ddb.get_tag_by_slug("doomed") is None

    fetched = await ddb.get_bookmark_by_id(bk.dynamo_id)
    assert "doomed" not in fetched.tags


async def test_delete_tag_returns_false_for_missing(ddb):
    assert (await ddb.delete_tag("never-existed")) is False


# ── get_stats ─────────────────────────────────────────────────────────


async def test_get_stats_counts_bookmarks_and_tags(ddb):
    await ddb.upsert_bookmark(url="https://a.com/", title="A")
    await ddb.upsert_bookmark(url="https://b.com/", title="B")
    await ddb.create_tag("t1", "T1", "")
    stats = await ddb.get_stats()
    assert stats["total_bookmarks"] == 2
    assert stats["total_tags"] == 1


# ── untag_bookmark (round 3) ──────────────────────────────────────────


async def test_untag_bookmark_removes_one_of_three(ddb):
    await ddb.create_tag("a", "A", "")
    await ddb.create_tag("b", "B", "")
    await ddb.create_tag("c", "C", "")
    bk = await ddb.upsert_bookmark(url="https://x.com/multi", title="Multi")
    await ddb.tag_bookmark(bk.dynamo_id, ["a", "b", "c"])

    result = await ddb.untag_bookmark(bk.dynamo_id, ["b"])
    assert result is not None
    assert set(result.tags) == {"a", "c"}


async def test_untag_bookmark_removes_all_tags(ddb):
    await ddb.create_tag("a", "A", "")
    await ddb.create_tag("b", "B", "")
    bk = await ddb.upsert_bookmark(url="https://x.com/both", title="Both")
    await ddb.tag_bookmark(bk.dynamo_id, ["a", "b"])

    result = await ddb.untag_bookmark(bk.dynamo_id, ["a", "b"])
    assert result is not None
    assert result.tags == []


async def test_untag_bookmark_is_idempotent_for_unknown_tag(ddb):
    """Removing a tag the bookmark doesn't have is a no-op (no error)."""
    await ddb.create_tag("a", "A", "")
    bk = await ddb.upsert_bookmark(url="https://x.com/ind", title="Ind")
    await ddb.tag_bookmark(bk.dynamo_id, ["a"])

    result = await ddb.untag_bookmark(bk.dynamo_id, ["never-applied"])
    assert result is not None
    assert result.tags == ["a"]


async def test_untag_bookmark_returns_none_for_missing_bookmark(ddb):
    assert await ddb.untag_bookmark("00000000-0000-0000-0000-000000000000", ["x"]) is None


async def test_untag_bookmark_handles_int_id_gracefully(ddb):
    """_dynamo_key(int) returns None → untag returns None (defensive guard)."""
    assert await ddb.untag_bookmark(12345, ["x"]) is None  # type: ignore[arg-type]


# ── update_tag branches ────────────────────────────────────────────────


async def test_update_tag_name_only(ddb):
    await ddb.create_tag("orig", "Original", "desc")
    result = await ddb.update_tag("orig", new_name="Renamed")
    assert result is not None
    assert result.name == "Renamed"
    assert result.description == "desc"  # unchanged


async def test_update_tag_description_only(ddb):
    await ddb.create_tag("orig2", "Name2", "old desc")
    result = await ddb.update_tag("orig2", new_description="new desc")
    assert result is not None
    assert result.name == "Name2"  # unchanged
    assert result.description == "new desc"


async def test_update_tag_no_op_returns_existing(ddb):
    """Both updates None → early-return path (dynamodb.py:271-273)."""
    await ddb.create_tag("orig3", "Stable", "stable")
    result = await ddb.update_tag("orig3")  # neither arg provided
    assert result is not None
    assert result.name == "Stable"
    assert result.description == "stable"


async def test_update_tag_missing_returns_none(ddb):
    """update_tag on a non-existent slug returns None."""
    assert await ddb.update_tag("does-not-exist", new_name="x") is None


# ── merge_tags error paths ────────────────────────────────────────────


async def test_merge_tags_raises_when_source_missing(ddb):
    """dynamodb.py:331 — source slug missing → ValueError."""
    await ddb.create_tag("target-ok", "T", "")
    with pytest.raises(ValueError, match="Source tag 'missing-src' not found"):
        await ddb.merge_tags("missing-src", "target-ok")


async def test_merge_tags_raises_when_target_missing(ddb):
    """dynamodb.py:333 — target slug missing → ValueError."""
    await ddb.create_tag("source-ok", "S", "")
    with pytest.raises(ValueError, match="Target tag 'missing-tgt' not found"):
        await ddb.merge_tags("source-ok", "missing-tgt")


# ── search_tags empty result ──────────────────────────────────────────


async def test_search_tags_returns_empty_when_no_matches(ddb):
    """dynamodb.py:228-230 — empty result list path."""
    await ddb.create_tag("python", "Python", "")
    result = await ddb.search_tags("rust")
    assert result == []


async def test_search_tags_returns_empty_for_empty_taxonomy(ddb):
    result = await ddb.search_tags("anything")
    assert result == []


# ── search_bookmarks non-paged wrapper (dynamodb.py:577-578) ──────────


async def test_search_bookmarks_non_paged_wrapper(ddb):
    """Direct call to search_bookmarks (the non-paged variant)."""
    await ddb.upsert_bookmark(url="https://x.com/np1", title="NP1")
    await ddb.upsert_bookmark(url="https://x.com/np2", title="NP2")
    result = await ddb.search_bookmarks(limit=10)
    assert len(result) == 2


# ── get_full_export + get_all_bookmarks ───────────────────────────────


async def test_get_full_export_returns_shape(ddb):
    """dynamodb.py:696-700 — export wrapper around bookmarks + tags + stats."""
    await ddb.upsert_bookmark(url="https://x.com/e1", title="E1", description="d1")
    await ddb.upsert_bookmark(url="https://x.com/e2", title="E2")
    await ddb.create_tag("exp", "Exp", "tag")
    export = await ddb.get_full_export()
    assert export["version"] == "1.0"
    assert "exported_at" in export
    assert export["stats"]["total_bookmarks"] == 2
    assert export["stats"]["total_tags"] == 1
    assert len(export["bookmarks"]) == 2
    assert len(export["tags"]) == 1


async def test_get_all_bookmarks_direct_call(ddb):
    """dynamodb.py:689-694 — direct exercise of get_all_bookmarks."""
    await ddb.upsert_bookmark(url="https://x.com/all1", title="All1")
    await ddb.upsert_bookmark(url="https://x.com/all2", title="All2")
    bookmarks = await ddb.get_all_bookmarks()
    assert len(bookmarks) == 2
    urls = {b.url for b in bookmarks}
    assert urls == {"https://x.com/all1", "https://x.com/all2"}


# ── _dynamo_key(int) defensive guard ──────────────────────────────────


async def test_dynamo_key_returns_none_for_int(ddb):
    """dynamodb.py:449 — _dynamo_key(int) returns None.

    DynamoDB IDs are UUIDs (strings); ints are SQLite-only. The defensive
    guard returns None so callers handle "not found" cleanly.
    """
    assert ddb._dynamo_key(12345) is None  # type: ignore[arg-type]
    assert ddb._dynamo_key(None) is None
    assert ddb._dynamo_key("") is None


# ── Corrupt cursor in search_bookmarks_paged ──────────────────────────


async def test_search_paged_silently_ignores_corrupt_cursor(ddb):
    """dynamodb.py:622 contextlib.suppress on malformed base64/JSON cursor.

    The function should NOT raise; it just resets to start-of-scan.
    """
    await ddb.upsert_bookmark(url="https://x.com/c1", title="C1")
    bookmarks, _next = await ddb.search_bookmarks_paged(limit=10, cursor="!!!not_base64!!!")
    # Got back the bookmark (cursor was ignored, scan started fresh)
    assert len(bookmarks) == 1
