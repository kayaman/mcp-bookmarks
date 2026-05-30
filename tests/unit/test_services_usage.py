"""UsageService — capability-gated monthly counter.

Uses the same ``_FakeBackend`` pattern as ``test_capability_enforcement.py``.
"""

from __future__ import annotations

import pytest

from mcp_bookmarks.backend import (
    DYNAMODB_CAPABILITIES,
    SQLITE_CAPABILITIES,
    BackendCapabilities,
    UnsupportedCapability,
)
from mcp_bookmarks.services import usage


class _FakeBackend:
    """Minimal backend with both ``capabilities`` and the optional counter method."""

    def __init__(
        self,
        capabilities: BackendCapabilities,
        *,
        count: int = 0,
        classname: str = "_FakeBackend",
    ) -> None:
        self.capabilities = capabilities
        self._count = count
        self.calls: list[str] = []
        self.__class__ = type(classname, (_FakeBackend,), {})

    async def count_usage_events_month(self, tenant_id: str) -> int:
        self.calls.append(tenant_id)
        return self._count


# ── Tests ──────────────────────────────────────────────────────────


async def test_count_events_returns_backend_value_when_capable():
    db = _FakeBackend(SQLITE_CAPABILITIES, count=42)
    n = await usage.count_events_this_month(db=db, tenant_id="t1")
    assert n == 42
    assert db.calls == ["t1"]


async def test_count_events_raises_unsupported_when_not_capable():
    db = _FakeBackend(DYNAMODB_CAPABILITIES, count=0)
    with pytest.raises(UnsupportedCapability) as excinfo:
        await usage.count_events_this_month(db=db, tenant_id="t1")
    assert excinfo.value.capability == "usage_metering"
    assert excinfo.value.method == "count_usage_events_month"
    # Verify the backend method was never invoked.
    assert db.calls == []
