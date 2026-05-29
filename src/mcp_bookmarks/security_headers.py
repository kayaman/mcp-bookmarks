"""Security headers + content-security-policy middleware.

Lighthouse Best Practices flags missing security headers; this middleware
also tightens the public security posture so XSS surfaces (the LLM-returned
content rendered on ``/ai-gateway``) have one more layer of defense.

Applied universally:
- ``X-Content-Type-Options: nosniff``
- ``Referrer-Policy: strict-origin-when-cross-origin``
- ``Permissions-Policy: camera=(), microphone=(), geolocation=()``

Applied to HTML routes (``/bookmarklet``, ``/ai-gateway``) only:
- ``Content-Security-Policy`` — restrictive but with the right allowances so
  the existing inline styles + the self-hosted font + the (single) inline
  script all keep working.

MCP transport paths (``/sse``, ``/mcp``, ``/messages``) get the non-CSP
headers only; a CSP on an event stream is meaningless and risks confusing
clients that parse it.

Pass the SHA-256 of the inline ``/ai-gateway`` script body when constructing
the middleware. The hash is wired into the CSP so the browser can validate
the inline script without ``'unsafe-inline'`` on ``script-src``.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


_HTML_PATHS = frozenset({"/bookmarklet", "/ai-gateway"})
_MCP_PREFIXES = ("/sse", "/mcp", "/messages")


def compute_script_hash(body: str) -> str:
    """Return ``sha256-<base64>`` for ``body`` — the CSP hash format.

    The browser computes the SHA-256 of the EXACT inline script byte stream
    (no wrapping, no minification, no whitespace tweaks) and compares to the
    value listed in the CSP. Any edit to the script body changes the hash;
    the hash must therefore be derived from the body, not hand-typed.
    """
    digest = hashlib.sha256(body.encode("utf-8")).digest()
    return f"sha256-{base64.b64encode(digest).decode('ascii')}"


def _build_csp(*, script_hash: str | None) -> str:
    """Construct the CSP value used on HTML routes.

    Notes:
    - ``style-src 'unsafe-inline'`` is required because the page ships a
      large inline ``<style>`` block. Lighthouse 11 treats this as
      informational on Best Practices, not scored.
    - ``script-src`` uses the SHA-256 hash of the inline script instead of
      ``'unsafe-inline'`` so a stored-XSS injection cannot run arbitrary JS.
    - ``font-src 'self'`` covers the self-hosted woff2; ``https://fonts.gstatic.com``
      remains allowed so a fallback to Google Fonts still works without a CSP
      violation if someone re-enables it.
    - ``frame-ancestors 'none'`` denies clickjacking entirely.
    """
    script_directive = f"script-src 'self' '{script_hash}'" if script_hash else "script-src 'self'"
    return "; ".join(
        [
            "default-src 'self'",
            "img-src 'self' data:",
            "font-src 'self' https://fonts.gstatic.com",
            "connect-src 'self'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            script_directive,
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self'",
        ]
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security + CSP headers on outbound responses."""

    def __init__(self, app: Any, *, ai_gateway_script_hash: str | None = None) -> None:
        super().__init__(app)
        # Pre-compute the two CSP values so per-request work is one dict lookup.
        self._csp_with_hash = _build_csp(script_hash=ai_gateway_script_hash)
        self._csp_no_inline_script = _build_csp(script_hash=None)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path

        # Apply the non-CSP headers everywhere; they're cheap and useful on
        # JSON endpoints too.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )

        if path.startswith(_MCP_PREFIXES):
            return response

        if path in _HTML_PATHS:
            csp = self._csp_with_hash if path == "/ai-gateway" else self._csp_no_inline_script
            response.headers.setdefault("Content-Security-Policy", csp)

        return response


__all__ = [
    "SecurityHeadersMiddleware",
    "compute_script_hash",
]
