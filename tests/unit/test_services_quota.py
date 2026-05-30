"""QuotaService — monthly limit enforcement entry points.

Covers the three quota outcomes (disabled / over / under) plus the two
block-response helpers. ``usage_meter`` is mocked so no SQLite or
DynamoDB connection is opened.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_bookmarks.services import quota

# ── check() ────────────────────────────────────────────────────────


async def test_check_returns_ok_when_limit_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(quota, "monthly_limit_enabled", lambda: False)

    async def _should_not_be_called(*a, **kw):  # pragma: no cover - safety net
        raise AssertionError("check_quota_for_backend should be skipped when disabled")

    monkeypatch.setattr(quota, "check_quota_for_backend", _should_not_be_called)

    ok, used, limit = await quota.check(
        db_path=Path("/tmp/quota.db"), tenant_id="t1", surface="rest"
    )
    assert ok is True
    assert used == 0
    assert limit == 0


async def test_check_returns_block_when_quota_reached(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(quota, "monthly_limit_enabled", lambda: True)

    async def _check(_db_path, tenant_id):
        return False, 100, 100

    monkeypatch.setattr(quota, "check_quota_for_backend", _check)

    ok, used, limit = await quota.check(
        db_path=Path("/tmp/quota.db"), tenant_id="t1", surface="mcp"
    )
    assert ok is False
    assert used == 100
    assert limit == 100


async def test_check_happy_path_returns_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(quota, "monthly_limit_enabled", lambda: True)

    async def _check(_db_path, tenant_id):
        return True, 3, 100

    monkeypatch.setattr(quota, "check_quota_for_backend", _check)

    ok, used, limit = await quota.check(
        db_path=Path("/tmp/quota.db"), tenant_id="t1", surface="rest"
    )
    assert ok is True
    assert used == 3
    assert limit == 100


# ── block-response helpers ─────────────────────────────────────────


def test_rest_block_response_is_429_with_details():
    resp = quota.rest_block_response(used=5, limit=3)
    assert resp.status_code == 429
    body = json.loads(resp.body)
    # Envelope shape from error_response — at least surfaces details.
    assert body["error"]["details"] == {"used": 5, "limit": 3}


def test_mcp_block_string_is_serializable_json():
    s = quota.mcp_block_string(used=7, limit=5)
    payload = json.loads(s)
    assert payload == {"error": "monthly_quota_exceeded", "used": 7, "limit": 5}


# ── record() ───────────────────────────────────────────────────────


async def test_record_forwards_to_usage_meter(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple] = []

    async def _record(db_path, event_type, tenant_id, metadata):
        calls.append((db_path, event_type, tenant_id, metadata))

    monkeypatch.setattr(quota, "record_usage_for_backend", _record)

    await quota.record(
        db_path=Path("/tmp/q.db"),
        event_type="search",
        tenant_id="t1",
        metadata={"k": "v"},
    )
    assert calls == [(Path("/tmp/q.db"), "search", "t1", {"k": "v"})]
