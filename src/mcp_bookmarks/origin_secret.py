"""Origin shared-secret gate: reject requests that didn't come via CloudFront.

Defense-in-depth behind the security group (which already restricts ingress to
CloudFront's origin-facing prefix list). When ``ORIGIN_SHARED_SECRET`` is set,
the CloudFront distribution is configured to inject a matching custom header on
every origin request; this middleware 403s anything lacking it — so another
tenant's CloudFront distribution pointed at our origin IP can't reach the app.

Exemptions: local container/ALB health probes (``/health``, ``/ready``) never
carry the header, and ``OPTIONS`` preflight must pass for CORS. When the env
var is unset the middleware is a no-op, so existing deployments are unaffected.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_HEADER = "x-origin-secret"
_EXEMPT = frozenset({"/health", "/ready"})


class OriginSecretMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        secret = os.environ.get("ORIGIN_SHARED_SECRET", "").strip()
        if not secret:
            return await call_next(request)
        if request.method == "OPTIONS" or request.url.path in _EXEMPT:
            return await call_next(request)
        presented = request.headers.get(_HEADER, "")
        if not hmac.compare_digest(presented, secret):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)


__all__ = ["OriginSecretMiddleware"]
