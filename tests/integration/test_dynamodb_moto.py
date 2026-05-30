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

    # Source tag is gone, bookmark now carries target slug.
    assert await ddb.get_tag_by_slug("py") is None
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
