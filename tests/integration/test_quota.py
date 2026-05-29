"""Monthly quota enforcement on the SQLite backend (WDN-395 coverage target).

Exercises usage_meter.{check_quota_sqlite, record_usage_sqlite} directly so
we don't need to spin up an HTTP server. The module-level ``_MONTHLY_LIMIT``
is patched per-test because it's read at import time.
"""

from __future__ import annotations

import pytest

from mcp_bookmarks import usage_meter

pytestmark = pytest.mark.asyncio


async def test_quota_disabled_when_limit_zero(monkeypatch, db, tmp_db_path):
    """With limit 0 (default), check_quota_sqlite always returns (True, 0, 0)."""
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 0)
    ok, _used, limit = await usage_meter.check_quota_sqlite(tmp_db_path, "tenant-a")
    assert ok is True
    assert limit == 0


async def test_quota_under_limit(monkeypatch, db, tmp_db_path):
    """Tenant under quota → ok=True with the running count surfaced."""
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 3)
    await usage_meter.record_usage_sqlite(tmp_db_path, "mcp_save", "tenant-a")
    await usage_meter.record_usage_sqlite(tmp_db_path, "mcp_save", "tenant-a")

    ok, used, limit = await usage_meter.check_quota_sqlite(tmp_db_path, "tenant-a")
    assert ok is True
    assert used == 2
    assert limit == 3


async def test_quota_blocks_at_limit(monkeypatch, db, tmp_db_path):
    """At limit → ok=False; the (N+1)th event must be rejected."""
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 2)
    await usage_meter.record_usage_sqlite(tmp_db_path, "mcp_save", "tenant-a")
    await usage_meter.record_usage_sqlite(tmp_db_path, "mcp_save", "tenant-a")

    ok, used, limit = await usage_meter.check_quota_sqlite(tmp_db_path, "tenant-a")
    assert ok is False
    assert used == 2
    assert limit == 2


async def test_quota_isolated_per_tenant(monkeypatch, db, tmp_db_path):
    """Hitting tenant-a's quota must not throttle tenant-b."""
    monkeypatch.setattr(usage_meter, "_MONTHLY_LIMIT", 1)
    await usage_meter.record_usage_sqlite(tmp_db_path, "mcp_save", "tenant-a")

    a_ok, *_ = await usage_meter.check_quota_sqlite(tmp_db_path, "tenant-a")
    b_ok, *_ = await usage_meter.check_quota_sqlite(tmp_db_path, "tenant-b")
    assert a_ok is False
    assert b_ok is True
