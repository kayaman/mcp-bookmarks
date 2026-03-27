"""Usage events and monthly quota (SQLite; optional DynamoDB table)."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Any

import asyncio

# Monthly limit of MCP-style events (0 = disabled)
_MONTHLY_LIMIT = int(os.environ.get("MCP_MONTHLY_USAGE_LIMIT", "0"))


def monthly_limit_enabled() -> bool:
    return _MONTHLY_LIMIT > 0


def _month_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def check_quota_sqlite(db_path, tenant_id: str | None) -> tuple[bool, int, int]:
    """Under quota, count this month, limit. If limit 0, always ok."""
    if not monthly_limit_enabled():
        return True, 0, _MONTHLY_LIMIT
    import aiosqlite

    tenant = tenant_id or "default"
    prefix = _month_prefix()
    async with aiosqlite.connect(str(db_path)) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE tenant_id = ? AND created_at LIKE ?",
            (tenant, f"{prefix}%"),
        )
        row = await cur.fetchone()
        n = int(row[0]) if row else 0
    return n < _MONTHLY_LIMIT, n, _MONTHLY_LIMIT


async def record_usage_sqlite(
    db_path,
    event_type: str,
    tenant_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    import aiosqlite

    now = datetime.now(timezone.utc).isoformat()
    tid = tenant_id or "default"
    meta = json.dumps(metadata or {}, default=str)
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "INSERT INTO usage_events (created_at, event_type, tenant_id, metadata) VALUES (?, ?, ?, ?)",
            (now, event_type, tid, meta),
        )
        await conn.commit()


def _dynamo_usage_table() -> str | None:
    t = os.environ.get("DYNAMODB_USAGE_TABLE", "").strip()
    return t or None


async def record_usage_dynamo(
    event_type: str,
    tenant_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    table = _dynamo_usage_table()
    if not table:
        return
    import boto3

    def _put():
        db = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        db.Table(table).put_item(
            Item={
                "id": str(uuid.uuid4()),
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "eventType": event_type,
                "tenantId": tenant_id or "default",
                "metadata": json.dumps(metadata or {}, default=str),
            }
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_put))


def dynamodb_mode() -> bool:
    return os.environ.get("DYNAMODB_MODE", "").lower() in ("1", "true", "yes")


async def record_usage_for_backend(
    db_path,
    event_type: str,
    tenant_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write usage to DynamoDB when ``DYNAMODB_USAGE_TABLE`` is set; else SQLite (non-Dynamo mode)."""
    await record_usage_dynamo(event_type, tenant_id, metadata)
    if not dynamodb_mode():
        await record_usage_sqlite(db_path, event_type, tenant_id, metadata)


async def check_quota_for_backend(db_path, tenant_id: str | None) -> tuple[bool, int, int]:
    """Enforce ``MCP_MONTHLY_USAGE_LIMIT`` using DynamoDB usage table or SQLite."""
    if not monthly_limit_enabled():
        return True, 0, _MONTHLY_LIMIT
    if dynamodb_mode():
        return await check_quota_dynamo(tenant_id)
    return await check_quota_sqlite(db_path, tenant_id)


async def check_quota_dynamo(tenant_id: str | None) -> tuple[bool, int, int]:
    if not monthly_limit_enabled():
        return True, 0, _MONTHLY_LIMIT
    table = _dynamo_usage_table()
    if not table:
        return True, 0, _MONTHLY_LIMIT
    import boto3
    from boto3.dynamodb.conditions import Attr

    tenant = tenant_id or "default"
    prefix = _month_prefix()

    def _scan_count():
        db = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        tbl = db.Table(table)
        # Filter scan — fine for low volume; use GSI + Query at scale
        resp = tbl.scan(
            FilterExpression=Attr("tenantId").eq(tenant) & Attr("createdAt").begins_with(prefix),
            Select="COUNT",
        )
        return int(resp.get("Count", 0))

    loop = asyncio.get_event_loop()
    n = await loop.run_in_executor(None, _scan_count)
    return n < _MONTHLY_LIMIT, n, _MONTHLY_LIMIT
