"""QuotaService — single entry point for "is this tenant over their monthly
limit, and record this event if not."

Wraps :mod:`mcp_bookmarks.usage_meter` and replaces the four small helpers
(``_check_rest_quota``, ``_mcp_quota_block``, ``_record_rest_usage``,
``_mcp_record``) that handlers used to call individually.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from starlette.responses import JSONResponse

from ..api_envelope import ErrorCode, error_response
from ..usage_meter import (
    check_quota_for_backend,
    monthly_limit_enabled,
    record_usage_for_backend,
)

log = logging.getLogger(__name__)

Surface = Literal["rest", "mcp"]


async def check(*, db_path: Path, tenant_id: str, surface: Surface) -> tuple[bool, int, int]:
    """Return ``(ok, used, limit)``. Emits ``quota_denied`` log when blocked."""
    if not monthly_limit_enabled():
        return True, 0, 0
    ok, used, limit = await check_quota_for_backend(db_path, tenant_id)
    if not ok:
        log.warning(
            "quota_denied",
            extra={"tenant_id": tenant_id, "used": used, "limit": limit, "surface": surface},
        )
    return ok, used, limit


def rest_block_response(used: int, limit: int) -> JSONResponse:
    """REST 429 envelope for a quota-exceeded request (WDN-396 shape)."""
    return error_response(
        ErrorCode.RATE_LIMITED,
        "Monthly usage quota exceeded for this tenant",
        status=429,
        details={"used": used, "limit": limit},
    )


def mcp_block_string(used: int, limit: int) -> str:
    """MCP-side JSON string returned in place of a tool result on block."""
    return json.dumps({"error": "monthly_quota_exceeded", "used": used, "limit": limit})


async def record(
    *,
    db_path: Path,
    event_type: str,
    tenant_id: str,
    metadata: dict | None = None,
) -> None:
    """Persist one usage event (after the successful tool/handler ran)."""
    await record_usage_for_backend(db_path, event_type, tenant_id, metadata)
