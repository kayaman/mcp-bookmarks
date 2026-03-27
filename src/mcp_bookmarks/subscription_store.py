"""Persist Stripe subscription snapshots (SQLite and/or DynamoDB)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from .db import Database, DEFAULT_DB_PATH


def _subs_table() -> str | None:
    t = os.environ.get("DYNAMODB_SUBSCRIPTIONS_TABLE", "").strip()
    return t or None


async def upsert_from_stripe_event(
    customer_id: str,
    status: str | None,
    plan_sku: str | None,
    current_period_end: str | None,
    raw_obj: dict[str, Any],
) -> None:
    raw_json = json.dumps(raw_obj, default=str)
    dynamo_app = os.environ.get("DYNAMODB_MODE", "").lower() in ("1", "true", "yes")
    if _subs_table():
        await _upsert_dynamo(customer_id, status, plan_sku, current_period_end, raw_json)
        if dynamo_app:
            return
    db_path = Path(os.environ.get("BOOKMARKS_DB_PATH", str(DEFAULT_DB_PATH)))
    db = Database(db_path)
    await db.connect()
    try:
        await db.upsert_subscription_state(
            customer_id, status, plan_sku, current_period_end, raw_json
        )
    finally:
        await db.close()


async def _upsert_dynamo(
    customer_id: str,
    status: str | None,
    plan_sku: str | None,
    current_period_end: str | None,
    raw_json: str,
) -> None:
    import boto3

    table = _subs_table()
    if not table:
        return

    def _put():
        r = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        r.Table(table).put_item(
            Item={
                "customerId": customer_id,
                "status": status or "",
                "planSku": plan_sku or "",
                "currentPeriodEnd": current_period_end or "",
                "rawJson": raw_json,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_put))


def extract_subscription_fields(obj: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (customer_id, status, plan_sku, current_period_end) from Stripe objects."""
    customer = obj.get("customer")
    if isinstance(customer, dict):
        customer_id = customer.get("id")
    else:
        customer_id = customer
    status = obj.get("status")
    plan_sku = None
    items = obj.get("items", {})
    data = items.get("data") if isinstance(items, dict) else None
    if data and isinstance(data, list) and data:
        price = data[0].get("price") or {}
        plan_sku = price.get("id") or price.get("nickname")
    cpe = obj.get("current_period_end")
    current_period_end = str(cpe) if cpe is not None else None
    return (
        str(customer_id) if customer_id else None,
        str(status) if status else None,
        str(plan_sku) if plan_sku else None,
        current_period_end,
    )
