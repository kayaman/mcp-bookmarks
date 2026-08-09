"""moto-backed tests for DynamoDB replace-set tag editing (Phase 1).

Follows tests/integration/test_dynamodb_moto.py: module-level table names in
dynamodb.py are frozen at import time, so we monkeypatch.setattr them; env
creds/tables set before entering mock_aws.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

_LINKS_TABLE = "test-tagedit-links"
_TAGS_TABLE = "test-tagedit-tags"
_EDITS_TABLE = "test-tagedit-edits"


@pytest.fixture
def tagedit_setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("TAG_EDITS_TABLE", _EDITS_TABLE)
    monkeypatch.setenv("DYNAMODB_USER_ID", "u-test")
    monkeypatch.delenv("DYNAMODB_ORG_ID", raising=False)

    with mock_aws():
        import boto3

        from mcp_bookmarks import dynamodb as dynamo_mod

        monkeypatch.setattr(dynamo_mod, "_LINKS_TABLE", _LINKS_TABLE)
        monkeypatch.setattr(dynamo_mod, "_TAGS_TABLE", _TAGS_TABLE)
        monkeypatch.setattr(dynamo_mod, "_USER_INDEX", "userId-savedAt-index")

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=_LINKS_TABLE,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "id", "AttributeType": "S"},
                {"AttributeName": "userId", "AttributeType": "S"},
                {"AttributeName": "savedAt", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "userId-savedAt-index",
                    "KeySchema": [
                        {"AttributeName": "userId", "KeyType": "HASH"},
                        {"AttributeName": "savedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.create_table(
            TableName=_TAGS_TABLE,
            KeySchema=[{"AttributeName": "slug", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "slug", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.create_table(
            TableName=_EDITS_TABLE,
            KeySchema=[
                {"AttributeName": "userId", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "userId", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


@pytest_asyncio.fixture
async def ddb(tagedit_setup):
    from mcp_bookmarks.dynamodb import DynamoDBDatabase

    return DynamoDBDatabase()


def _table(name: str):
    import boto3

    return boto3.resource("dynamodb", region_name="us-east-1").Table(name)


def _seed_link(bk_id: str, tags: list[str], saved_at: str = "2026-08-01T00:00:00+00:00", **extra):
    _table(_LINKS_TABLE).put_item(
        Item={
            "id": bk_id,
            "url": f"https://example.com/{bk_id}",
            "ogTitle": bk_id.upper(),
            "userId": "u-test",
            "savedAt": saved_at,
            "aiTags": tags,
            **extra,
        }
    )


def _seed_tag(slug: str, usage_count: int):
    _table(_TAGS_TABLE).put_item(Item={"slug": slug, "name": slug, "usage_count": usage_count})


async def test_replace_writes_snapshot_and_reviewed_at(ddb):
    _seed_link("bk-1", ["python", "web"])
    result = await ddb.replace_bookmark_tags("bk-1", ["python", "rust-lang"])
    assert result == {
        "bookmark_id": "bk-1",
        "before": ["python", "web"],
        "after": ["python", "rust-lang"],
        "added": ["rust-lang"],
        "removed": ["web"],
    }
    item = _table(_LINKS_TABLE).get_item(Key={"id": "bk-1"})["Item"]
    assert list(item["aiTags"]) == ["python", "rust-lang"]
    assert list(item["aiTagsOriginal"]) == ["python", "web"]
    assert item["tagsReviewedAt"]


async def test_snapshot_and_reviewed_at_written_once(ddb):
    _seed_link("bk-1", ["python"])
    await ddb.replace_bookmark_tags("bk-1", ["web"])
    first = _table(_LINKS_TABLE).get_item(Key={"id": "bk-1"})["Item"]["tagsReviewedAt"]
    await ddb.replace_bookmark_tags("bk-1", ["rust-lang"])
    item = _table(_LINKS_TABLE).get_item(Key={"id": "bk-1"})["Item"]
    assert list(item["aiTagsOriginal"]) == ["python"]  # attribute_not_exists guard held
    assert item["tagsReviewedAt"] == first


async def test_recalibrate_actor_snapshots_without_reviewed_at(ddb):
    _seed_link("bk-1", ["python"])
    await ddb.replace_bookmark_tags("bk-1", ["web"], actor="recalibrate")
    item = _table(_LINKS_TABLE).get_item(Key={"id": "bk-1"})["Item"]
    assert list(item["aiTagsOriginal"]) == ["python"]
    assert "tagsReviewedAt" not in item


async def test_replace_reconciles_usage_counts(ddb):
    _seed_link("bk-1", ["python", "web"])
    _seed_tag("python", 5)
    _seed_tag("web", 2)
    await ddb.replace_bookmark_tags("bk-1", ["python", "brand-new"])
    assert (await ddb.get_tag_by_slug("python")).usage_count == 5  # unchanged
    assert (await ddb.get_tag_by_slug("web")).usage_count == 1  # -1
    created = await ddb.get_tag_by_slug("brand-new")  # net-new row
    assert created is not None and created.usage_count == 1


async def test_edit_event_row_matches_pinned_contract(ddb):
    _seed_link("bk-1", ["python"])
    await ddb.replace_bookmark_tags("bk-1", ["web"])
    items = _table(_EDITS_TABLE).scan()["Items"]
    assert len(items) == 1
    ev = items[0]
    assert ev["userId"] == "u-test"
    assert ev["sk"] == f"{ev['ts']}#bk-1"
    assert ev["bookmarkId"] == "bk-1"
    assert list(ev["before"]) == ["python"]
    assert list(ev["after"]) == ["web"]
    assert list(ev["added"]) == ["web"]
    assert list(ev["removed"]) == ["python"]
    assert ev["actor"] == "human"


async def test_get_tag_edits_newest_first_single_pk_query(ddb):
    _seed_link("bk-1", ["python"])
    await ddb.replace_bookmark_tags("bk-1", ["web"])
    await ddb.replace_bookmark_tags("bk-1", ["rust-lang"])
    edits = await ddb.get_tag_edits()
    assert len(edits) == 2
    assert edits[0]["after"] == ["rust-lang"]
    assert edits[0]["ts"] > edits[1]["ts"]


async def test_get_recent_bookmarks_contract_fields_with_nulls(ddb):
    _seed_link("bk-old", ["python"], saved_at="2026-08-01T00:00:00+00:00")
    _seed_link("bk-new", [], saved_at="2026-08-02T00:00:00+00:00")
    rows = await ddb.get_recent_bookmarks(limit=50)
    assert [r["id"] for r in rows] == ["bk-new", "bk-old"]  # newest savedAt first
    assert set(rows[0]) == {"id", "url", "title", "aiTags", "aiTagsOriginal", "tagsReviewedAt"}
    assert rows[0]["aiTagsOriginal"] is None
    assert rows[0]["tagsReviewedAt"] is None


async def test_replace_missing_bookmark_returns_none(ddb):
    assert await ddb.replace_bookmark_tags("nope", ["a"]) is None
