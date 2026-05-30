"""UsageService — capability-gated usage reporting.

`api_usage` previously called `db.count_usage_events_month` directly,
which only SQLite implements. Centralized here so the `usage_metering`
capability gate lives next to the call, and adding a DynamoDB-side
counter (via `DYNAMODB_USAGE_TABLE`) is a one-file change.
"""

from __future__ import annotations

from typing import Any, cast

from ..backend import BookmarkBackend, UnsupportedCapability, require_capability


async def count_events_this_month(*, db: BookmarkBackend, tenant_id: str) -> int:
    """Return the running event count for ``tenant_id`` this month.

    Raises :class:`UnsupportedCapability` when the backend lacks
    ``usage_metering`` (DynamoDB today). Caller serializes the
    structured envelope.
    """
    require_capability(db, "usage_metering", method="count_usage_events_month")
    return await cast(Any, db).count_usage_events_month(tenant_id)


__all__ = ["UnsupportedCapability", "count_events_this_month"]
