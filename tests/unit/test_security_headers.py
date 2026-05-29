"""SecurityHeadersMiddleware contract tests.

Pure unit tests against a minimal Starlette app so we don't pay the
combined-app's MCP lifespan startup cost on every assertion.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_bookmarks.security_headers import (
    SecurityHeadersMiddleware,
    compute_script_hash,
)


_HASH = compute_script_hash("(function(){var x=1;})();")


def _build_app() -> Starlette:
    async def html(request):
        return HTMLResponse("<p>hi</p>")

    async def stream(request):
        return PlainTextResponse("event: ping\n\n", media_type="text/event-stream")

    async def json_route(request):
        return JSONResponse({"ok": True})

    return Starlette(
        middleware=[Middleware(SecurityHeadersMiddleware, ai_gateway_script_hash=_HASH)],
        routes=[
            Route("/bookmarklet", html),
            Route("/ai-gateway", html),
            Route("/sse", stream),
            Route("/mcp", stream),
            Route("/api/stats", json_route),
            Route("/health", json_route),
        ],
    )


@pytest.fixture
def client():
    return TestClient(_build_app())


# ── Headers applied universally ────────────────────────────────────


def test_x_content_type_options_on_html(client):
    assert client.get("/bookmarklet").headers["X-Content-Type-Options"] == "nosniff"


def test_x_content_type_options_on_json(client):
    assert client.get("/api/stats").headers["X-Content-Type-Options"] == "nosniff"


def test_x_content_type_options_on_sse(client):
    assert client.get("/sse").headers["X-Content-Type-Options"] == "nosniff"


def test_referrer_policy_on_html(client):
    assert (
        client.get("/bookmarklet").headers["Referrer-Policy"]
        == "strict-origin-when-cross-origin"
    )


def test_permissions_policy_on_html(client):
    val = client.get("/bookmarklet").headers["Permissions-Policy"]
    assert "camera=()" in val
    assert "microphone=()" in val
    assert "geolocation=()" in val


# ── CSP applied to HTML pages only ─────────────────────────────────


def test_csp_present_on_bookmarklet(client):
    csp = client.get("/bookmarklet").headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # No script hash on the bookmarklet page — it ships no inline script.
    assert _HASH not in csp


def test_csp_present_on_ai_gateway_with_script_hash(client):
    csp = client.get("/ai-gateway").headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert _HASH in csp
    assert "script-src 'self'" in csp


def test_csp_skipped_on_sse(client):
    # CSP on a streaming event endpoint serves no purpose and risks confusing clients.
    assert "Content-Security-Policy" not in client.get("/sse").headers


def test_csp_skipped_on_mcp(client):
    assert "Content-Security-Policy" not in client.get("/mcp").headers


def test_csp_skipped_on_api_routes(client):
    # /api/* returns JSON, not HTML; CSP is not load-bearing there. The
    # current policy is to ship CSP only on the two HTML page paths.
    assert "Content-Security-Policy" not in client.get("/api/stats").headers


# ── compute_script_hash ────────────────────────────────────────────


def test_compute_script_hash_deterministic():
    body = "(function(){var x=1;})();"
    assert compute_script_hash(body) == compute_script_hash(body)


def test_compute_script_hash_changes_on_body_edit():
    a = compute_script_hash("(function(){var x=1;})();")
    b = compute_script_hash("(function(){var x=2;})();")
    assert a != b


def test_compute_script_hash_format():
    h = compute_script_hash("alert(1)")
    assert h.startswith("sha256-")
    # base64 + standard padding length for SHA-256 is 44 chars after the prefix
    assert len(h) == len("sha256-") + 44
