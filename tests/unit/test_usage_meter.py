"""usage_meter — monthly quota + usage recording (SQLite + DynamoDB).

The SQLite path uses real ``aiosqlite`` against a tmp database; the
DynamoDB path uses ``moto``'s in-memory mock. Retry / throttling paths
are out of scope — see CONTRIBUTING.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_bookmarks import usage_meter

# ── pure helpers ───────────────────────────────────────────────────


def test_monthly_limit_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    """Module-level _MONTHLY_LIMIT is read at import time; force-set for the test."""
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 0)
    assert usage_meter.monthly_limit_enabled() is False


def test_monthly_limit_enabled_when_positive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 100)
    assert usage_meter.monthly_limit_enabled() is True


def test_month_prefix_is_yyyy_mm():
    prefix = usage_meter._month_prefix()
    assert len(prefix) == 7
    assert prefix[4] == "-"


def test_dynamo_usage_table_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DYNAMODB_USAGE_TABLE", raising=False)
    assert usage_meter._dynamo_usage_table() is None


def test_dynamo_usage_table_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DYNAMODB_USAGE_TABLE", "blogmarks-usage")
    assert usage_meter._dynamo_usage_table() == "blogmarks-usage"


@pytest.mark.parametrize(
    "val,expected", [("1", True), ("true", True), ("yes", True), ("", False), ("no", False)]
)
def test_dynamodb_mode_parses_env(monkeypatch: pytest.MonkeyPatch, val: str, expected: bool):
    monkeypatch.setenv("DYNAMODB_MODE", val)
    assert usage_meter.dynamodb_mode() is expected


# ── SQLite path ────────────────────────────────────────────────────


async def _seed_schema(db_path: Path) -> None:
    """Bootstrap the usage_events table that the SQLite path expects."""
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            """
            CREATE TABLE usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                metadata TEXT NOT NULL
            )
            """
        )
        await conn.commit()


async def test_check_quota_sqlite_returns_disabled_when_limit_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 0)
    db_path = tmp_path / "u.db"
    ok, n, limit = await usage_meter.check_quota_sqlite(db_path, "tenant-a")
    assert ok is True
    assert n == 0
    assert limit == 0


async def test_record_then_check_quota_sqlite_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 5)
    db_path = tmp_path / "u.db"
    await _seed_schema(db_path)

    # Record 3 events for tenant-a
    for _ in range(3):
        await usage_meter.record_usage_sqlite(db_path, "save", "tenant-a", {"k": "v"})

    ok, n, limit = await usage_meter.check_quota_sqlite(db_path, "tenant-a")
    assert ok is True  # 3 < 5
    assert n == 3
    assert limit == 5


async def test_check_quota_sqlite_blocks_when_at_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 2)
    db_path = tmp_path / "u.db"
    await _seed_schema(db_path)
    await usage_meter.record_usage_sqlite(db_path, "save", "tenant-b")
    await usage_meter.record_usage_sqlite(db_path, "save", "tenant-b")

    ok, n, _limit = await usage_meter.check_quota_sqlite(db_path, "tenant-b")
    assert ok is False  # 2 < 2 is False
    assert n == 2


async def test_check_quota_for_backend_routes_to_sqlite_when_dynamo_mode_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 10)
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)
    db_path = tmp_path / "u.db"
    await _seed_schema(db_path)

    ok, n, limit = await usage_meter.check_quota_for_backend(db_path, "t")
    assert ok is True
    assert n == 0
    assert limit == 10


async def test_record_usage_for_backend_uses_sqlite_when_dynamo_mode_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)
    monkeypatch.delenv("DYNAMODB_USAGE_TABLE", raising=False)
    db_path = tmp_path / "u.db"
    await _seed_schema(db_path)

    await usage_meter.record_usage_for_backend(db_path, "save", "t1", {"k": "v"})

    # SQLite row written
    import aiosqlite

    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute(
            "SELECT event_type, tenant_id FROM usage_events WHERE tenant_id = ?", ("t1",)
        )
        row = await cur.fetchone()
    assert row == ("save", "t1")


# ── DynamoDB path (moto) ───────────────────────────────────────────


async def test_record_usage_dynamo_writes_item(monkeypatch: pytest.MonkeyPatch):
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DYNAMODB_USAGE_TABLE", "test-usage")

    with mock_aws():
        import boto3

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-usage",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        await usage_meter.record_usage_dynamo("save", "t-dyn", {"k": "v"})

        # Confirm exactly one item landed
        table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-usage")
        scan = table.scan()
        assert scan["Count"] == 1
        item = scan["Items"][0]
        assert item["eventType"] == "save"
        assert item["tenantId"] == "t-dyn"


async def test_record_usage_dynamo_noop_when_table_not_set(monkeypatch: pytest.MonkeyPatch):
    """Without DYNAMODB_USAGE_TABLE, the function silently returns."""
    monkeypatch.delenv("DYNAMODB_USAGE_TABLE", raising=False)
    # Should not raise even though boto3 is never called.
    await usage_meter.record_usage_dynamo("save", "t", None)


async def test_check_quota_dynamo_counts_matching_events(monkeypatch: pytest.MonkeyPatch):
    from moto import mock_aws

    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 5)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DYNAMODB_USAGE_TABLE", "test-usage")

    with mock_aws():
        import boto3

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-usage",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Seed 2 events for tenant-c, 1 for tenant-d
        for _ in range(2):
            await usage_meter.record_usage_dynamo("save", "tenant-c")
        await usage_meter.record_usage_dynamo("save", "tenant-d")

        ok, n, limit = await usage_meter.check_quota_dynamo("tenant-c")
        assert ok is True  # 2 < 5
        assert n == 2
        assert limit == 5


async def test_check_quota_dynamo_returns_disabled_when_limit_zero(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 0)
    ok, n, limit = await usage_meter.check_quota_dynamo("t")
    assert ok is True
    assert n == 0
    assert limit == 0


async def test_check_quota_dynamo_returns_disabled_when_table_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 10)
    monkeypatch.delenv("DYNAMODB_USAGE_TABLE", raising=False)
    ok, n, limit = await usage_meter.check_quota_dynamo("t")
    assert ok is True
    assert n == 0
    assert limit == 10
