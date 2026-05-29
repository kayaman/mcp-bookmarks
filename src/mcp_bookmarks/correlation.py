"""Per-request correlation ID middleware (WDN-397 / OSS-7).

Reads the inbound ``X-Correlation-ID`` header (lowercase or any case) when
present, otherwise mints a UUID4. Stores it on ``request.state.correlation_id``
**and** sets the :data:`mcp_bookmarks.logging_config.correlation_id_var`
contextvar so every log record emitted under that request carries the id.

Always echoes the resolved id back on the response via ``X-Correlation-ID``,
giving clients a value they can quote in support tickets.

The middleware deliberately runs **outer-most** on the combined app so the
contextvar is in scope even when later middleware (auth, quota) emits its
own log records before reaching a route handler.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .logging_config import correlation_id_var


_HEADER = "x-correlation-id"


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get(_HEADER) or request.headers.get("X-Correlation-ID")
        if not cid:
            cid = uuid.uuid4().hex
        request.state.correlation_id = cid
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Correlation-ID"] = cid
        return response


__all__ = ["CorrelationMiddleware"]
