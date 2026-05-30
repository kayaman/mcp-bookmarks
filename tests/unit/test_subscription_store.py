"""subscription_store — Stripe subscription persistence helpers.

Covers ``extract_subscription_fields`` (pure parsing) thoroughly, the
SQLite-backed branch of ``upsert_from_stripe_event`` via an in-memory DB,
and the DynamoDB branch via a moto v5 ``mock_aws`` fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_bookmarks import subscription_store

# ── extract_subscription_fields ────────────────────────────────────


def test_extract_all_fields_with_dict_customer():
    obj = {
        "customer": {"id": "cus_123"},
        "status": "active",
        "items": {
            "data": [
                {"price": {"id": "price_abc", "nickname": "Pro"}},
            ]
        },
        "current_period_end": 1735689600,
    }
    customer_id, status, plan_sku, cpe = subscription_store.extract_subscription_fields(obj)
    assert customer_id == "cus_123"
    assert status == "active"
    assert plan_sku == "price_abc"
    assert cpe == "1735689600"


def test_extract_all_fields_with_string_customer():
    obj = {
        "customer": "cus_456",
        "status": "trialing",
        "items": {"data": [{"price": {"id": "price_xyz"}}]},
        "current_period_end": 9999999999,
    }
    customer_id, status, plan_sku, cpe = subscription_store.extract_subscription_fields(obj)
    assert customer_id == "cus_456"
    assert status == "trialing"
    assert plan_sku == "price_xyz"
    assert cpe == "9999999999"


def test_extract_plan_falls_back_to_nickname_when_price_id_missing():
    obj = {
        "customer": "cus_n",
        "items": {"data": [{"price": {"nickname": "Premium"}}]},
    }
    _, _, plan_sku, _ = subscription_store.extract_subscription_fields(obj)
    assert plan_sku == "Premium"


def test_extract_handles_missing_items():
    obj = {"customer": "cus_only"}
    customer_id, status, plan_sku, cpe = subscription_store.extract_subscription_fields(obj)
    assert customer_id == "cus_only"
    assert status is None
    assert plan_sku is None
    assert cpe is None


def test_extract_handles_empty_items_data():
    obj = {"customer": "cus_x", "items": {"data": []}}
    _, _, plan_sku, _ = subscription_store.extract_subscription_fields(obj)
    assert plan_sku is None


def test_extract_handles_non_dict_items():
    """Stripe could return items as a non-dict shape in malformed payloads — handle gracefully."""
    obj = {"customer": "cus_y", "items": "unexpected"}
    _, _, plan_sku, _ = subscription_store.extract_subscription_fields(obj)
    assert plan_sku is None


def test_extract_handles_dict_customer_without_id():
    obj = {"customer": {"name": "no id here"}}
    customer_id, _, _, _ = subscription_store.extract_subscription_fields(obj)
    # No id => None
    assert customer_id is None


def test_extract_returns_none_when_customer_missing():
    obj: dict = {}
    customer_id, status, plan_sku, cpe = subscription_store.extract_subscription_fields(obj)
    assert customer_id is None
    assert status is None
    assert plan_sku is None
    assert cpe is None


# ── _subs_table ────────────────────────────────────────────────────


def test_subs_table_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DYNAMODB_SUBSCRIPTIONS_TABLE", raising=False)
    assert subscription_store._subs_table() is None


def test_subs_table_empty_string(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DYNAMODB_SUBSCRIPTIONS_TABLE", "   ")
    assert subscription_store._subs_table() is None


def test_subs_table_set(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DYNAMODB_SUBSCRIPTIONS_TABLE", "blogmarks-subs")
    assert subscription_store._subs_table() == "blogmarks-subs"


# ── upsert_from_stripe_event (SQLite path) ─────────────────────────


async def test_upsert_writes_to_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """End-to-end through a real on-disk SQLite Database."""
    monkeypatch.delenv("DYNAMODB_SUBSCRIPTIONS_TABLE", raising=False)
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)
    db_path = tmp_path / "subs.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))

    await subscription_store.upsert_from_stripe_event(
        customer_id="cus_test",
        status="active",
        plan_sku="price_abc",
        current_period_end="1735689600",
        raw_obj={"id": "sub_test", "object": "subscription"},
    )

    # Verify the row landed via a fresh Database read
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        row = await db.get_subscription_state("cus_test")
    finally:
        await db.close()
    assert row is not None
    assert row["customer_id"] == "cus_test"
    assert row["status"] == "active"
    assert row["plan_sku"] == "price_abc"


async def test_upsert_handles_missing_optional_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("DYNAMODB_SUBSCRIPTIONS_TABLE", raising=False)
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)
    db_path = tmp_path / "subs.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))

    await subscription_store.upsert_from_stripe_event(
        customer_id="cus_minimal",
        status=None,
        plan_sku=None,
        current_period_end=None,
        raw_obj={},
    )
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        row = await db.get_subscription_state("cus_minimal")
    finally:
        await db.close()
    assert row is not None
    assert row["customer_id"] == "cus_minimal"


# ── upsert_from_stripe_event (DynamoDB path, moto-backed) ──────────


async def test_upsert_writes_to_dynamo_when_table_set_and_mode_on(
    monkeypatch: pytest.MonkeyPatch,
):
    """With DYNAMODB_SUBSCRIPTIONS_TABLE + DYNAMODB_MODE set, the SQLite branch is skipped."""
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DYNAMODB_SUBSCRIPTIONS_TABLE", "test-subs")
    monkeypatch.setenv("DYNAMODB_MODE", "true")

    with mock_aws():
        import boto3

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-subs",
            KeySchema=[{"AttributeName": "customerId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "customerId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        await subscription_store.upsert_from_stripe_event(
            customer_id="cus_dynamo",
            status="active",
            plan_sku="price_dyn",
            current_period_end="9999",
            raw_obj={"id": "sub_dyn"},
        )

        # Read back via boto3 directly to verify the item shape.
        table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-subs")
        item = table.get_item(Key={"customerId": "cus_dynamo"})["Item"]
        assert item["customerId"] == "cus_dynamo"
        assert item["status"] == "active"
        assert item["planSku"] == "price_dyn"
        assert item["currentPeriodEnd"] == "9999"
        # rawJson is JSON-encoded
        import json as _json

        assert _json.loads(item["rawJson"]) == {"id": "sub_dyn"}
        assert "updatedAt" in item


async def test_upsert_dynamo_branch_also_writes_sqlite_when_mode_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """DYNAMODB_SUBSCRIPTIONS_TABLE set BUT DYNAMODB_MODE off → both backends written."""
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("DYNAMODB_SUBSCRIPTIONS_TABLE", "test-subs")
    monkeypatch.delenv("DYNAMODB_MODE", raising=False)
    db_path = tmp_path / "subs_hybrid.db"
    monkeypatch.setenv("BOOKMARKS_DB_PATH", str(db_path))

    with mock_aws():
        import boto3

        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-subs",
            KeySchema=[{"AttributeName": "customerId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "customerId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        await subscription_store.upsert_from_stripe_event(
            customer_id="cus_both",
            status="active",
            plan_sku="price_x",
            current_period_end="1",
            raw_obj={},
        )

        # DynamoDB has the item
        table = boto3.resource("dynamodb", region_name="us-east-1").Table("test-subs")
        assert table.get_item(Key={"customerId": "cus_both"}).get("Item") is not None

    # SQLite was also written (the `if dynamo_app: return` short-circuit is OFF)
    from mcp_bookmarks.db import Database

    db = Database(db_path)
    await db.connect()
    try:
        row = await db.get_subscription_state("cus_both")
    finally:
        await db.close()
    assert row is not None
    assert row["customer_id"] == "cus_both"
