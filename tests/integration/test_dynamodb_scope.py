"""moto-backed tests for Knowledge GSI queries + scope enforcement.

Covers the parity port of read-mcp's ``buildScopeFilter``:
  - per-bookmark ``mcpExposed`` exposure gate,
  - ``{"type": "tags", ...}`` allowlist,
  - owner (scope=None) sees everything,
plus ``query_raw_by_type`` over the ``userId-type-savedAt-index`` GSI and its
scan fallback.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

_LINKS_TABLE = "test-links-scope"
_TAGS_TABLE = "test-tags-scope"
_TYPE_INDEX = "userId-type-savedAt-index"
_OWNER = "owner-123"


@pytest.fixture
def ddb_setup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from moto import mock_aws

    for k, v in {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DYNAMODB_ORG_ID", raising=False)

    with mock_aws():
        import boto3

        from mcp_bookmarks import dynamodb as dynamo_mod

        monkeypatch.setattr(dynamo_mod, "_LINKS_TABLE", _LINKS_TABLE)
        monkeypatch.setattr(dynamo_mod, "_TAGS_TABLE", _TAGS_TABLE)
        monkeypatch.setattr(dynamo_mod, "_TYPE_INDEX", _TYPE_INDEX)

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=_LINKS_TABLE,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "id", "AttributeType": "S"},
                {"AttributeName": "userId", "AttributeType": "S"},
                {"AttributeName": "bookmarkType", "AttributeType": "S"},
                {"AttributeName": "savedAt", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": _TYPE_INDEX,
                    "KeySchema": [
                        {"AttributeName": "userId", "KeyType": "HASH"},
                        {"AttributeName": "bookmarkType", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "userId-savedAt-index",
                    "KeySchema": [
                        {"AttributeName": "userId", "KeyType": "HASH"},
                        {"AttributeName": "savedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.create_table(
            TableName=_TAGS_TABLE,
            KeySchema=[{"AttributeName": "slug", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "slug", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        table = boto3.resource("dynamodb", region_name="us-east-1").Table(_LINKS_TABLE)
        for item in _seed_items():
            table.put_item(Item=item)
        yield


def _seed_items() -> list[dict]:
    return [
        # owner Knowledge, exposed, tagged rust
        {
            "id": "k1", "userId": _OWNER, "url": "https://ex.com/k1", "bookmarkType": "knowledge",
            "savedAt": "2026-01-01", "aiTags": ["rust", "systems"], "aiContent": "x",
        },
        # owner Knowledge, NOT exposed to agents
        {
            "id": "k2", "userId": _OWNER, "url": "https://ex.com/k2", "bookmarkType": "knowledge",
            "savedAt": "2026-01-02", "aiTags": ["python"], "mcpExposed": False, "aiContent": "x",
        },
        # owner Knowledge, exposed, tagged python
        {
            "id": "k3", "userId": _OWNER, "url": "https://ex.com/k3", "bookmarkType": "knowledge",
            "savedAt": "2026-01-03", "aiTags": ["python"], "aiContent": "x",
        },
        # owner read_later (not knowledge)
        {
            "id": "r1", "userId": _OWNER, "url": "https://ex.com/r1", "bookmarkType": "read_later",
            "savedAt": "2026-01-04", "aiTags": ["misc"], "aiContent": "x",
        },
        # a different user's knowledge (must never leak)
        {
            "id": "o1", "userId": "someone-else", "url": "https://ex.com/o1",
            "bookmarkType": "knowledge", "savedAt": "2026-01-05", "aiTags": ["rust"],
        },
    ]


@pytest_asyncio.fixture
async def ddb(ddb_setup):
    from mcp_bookmarks.dynamodb import DynamoDBDatabase

    return DynamoDBDatabase()


def _as_owner(scope):
    from mcp_bookmarks.request_context import reset_request_identity, set_request_identity

    return set_request_identity(_OWNER, "default", scope), reset_request_identity


# ── query_raw_by_type ─────────────────────────────────────────────────


async def test_query_raw_by_type_uses_gsi(ddb):
    items = await ddb.query_raw_by_type("knowledge", user_id=_OWNER)
    ids = {i["id"] for i in items}
    assert ids == {"k1", "k2", "k3"}  # owner's knowledge only, all of it


async def test_query_raw_by_type_uses_user_index(ddb, monkeypatch):
    from mcp_bookmarks import dynamodb as dynamo_mod

    # No type index → query the userId GSI and filter bookmarkType server-side.
    monkeypatch.setattr(dynamo_mod, "_TYPE_INDEX", "")
    monkeypatch.setattr(dynamo_mod, "_USER_INDEX", "userId-savedAt-index")
    items = await ddb.query_raw_by_type("knowledge", user_id=_OWNER)
    assert {i["id"] for i in items} == {"k1", "k2", "k3"}


async def test_query_raw_by_type_scan_fallback(ddb, monkeypatch):
    from mcp_bookmarks import dynamodb as dynamo_mod

    # Clear both indexes → force the full-table scan path.
    monkeypatch.setattr(dynamo_mod, "_TYPE_INDEX", "")
    monkeypatch.setattr(dynamo_mod, "_USER_INDEX", "")
    items = await ddb.query_raw_by_type("knowledge", user_id=_OWNER)
    assert {i["id"] for i in items} == {"k1", "k2", "k3"}


# ── scope enforcement on search ───────────────────────────────────────


async def test_owner_scope_none_sees_all_own(ddb):
    tokens, reset = _as_owner(None)
    try:
        found = {b.dynamo_id for b in await ddb.search_bookmarks()}
    finally:
        reset(tokens)
    # owner sees every own bookmark incl. mcpExposed=False; never another user's
    assert {"k1", "k2", "k3", "r1"} <= found
    assert "o1" not in found


async def test_all_private_scope_excludes_unexposed(ddb):
    tokens, reset = _as_owner({"type": "all_private"})
    try:
        found = {b.dynamo_id for b in await ddb.search_bookmarks()}
    finally:
        reset(tokens)
    assert "k2" not in found  # mcpExposed=False hidden from agent
    assert {"k1", "k3", "r1"} <= found


async def test_tags_scope_allowlists(ddb):
    tokens, reset = _as_owner({"type": "tags", "tags": ["python"]})
    try:
        found = {b.dynamo_id for b in await ddb.search_bookmarks()}
    finally:
        reset(tokens)
    # only exposed python-tagged rows: k3 (k2 is python but unexposed)
    assert found == {"k3"}


async def test_get_by_id_respects_exposure_under_scope(ddb):
    tokens, reset = _as_owner({"type": "all_private"})
    try:
        assert await ddb.get_bookmark_by_id("k2") is None  # unexposed → hidden
        assert (await ddb.get_bookmark_by_id("k1")).dynamo_id == "k1"
    finally:
        reset(tokens)


async def test_get_by_id_visible_to_owner_without_scope(ddb):
    tokens, reset = _as_owner(None)
    try:
        assert (await ddb.get_bookmark_by_id("k2")).dynamo_id == "k2"
    finally:
        reset(tokens)
