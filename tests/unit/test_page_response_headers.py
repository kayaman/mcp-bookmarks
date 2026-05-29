"""Cache-Control + Vary on /bookmarklet and /ai-gateway, plus the static
font asset's long-cache header. Drives the real handlers (no mocks) via
Starlette's TestClient against a minimal app that mounts the three routes.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_bookmarks.api import (
    ai_gateway_page,
    bookmarklet_page,
    static_font_jetbrains_mono,
)


@pytest.fixture
def client():
    app = Starlette(
        routes=[
            Route("/bookmarklet", bookmarklet_page),
            Route("/ai-gateway", ai_gateway_page),
            Route("/static/jetbrains-mono.woff2", static_font_jetbrains_mono),
        ]
    )
    return TestClient(app)


def test_bookmarklet_cache_control(client):
    resp = client.get("/bookmarklet")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, max-age=600"
    # Host header is baked into the bookmarklet payload; cache by Host.
    assert "host" in resp.headers["Vary"].lower()


def test_ai_gateway_cache_control(client):
    resp = client.get("/ai-gateway")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=300, must-revalidate"


def test_static_font_long_cache_immutable(client):
    resp = client.get("/static/jetbrains-mono.woff2")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "font/woff2"
    assert "immutable" in resp.headers["Cache-Control"]
    assert "max-age=31536000" in resp.headers["Cache-Control"]
    # CORS Access-Control-Allow-Origin so the @font-face preload doesn't
    # trip a cross-origin font-fetch error when the page is loaded from a
    # CDN that proxies different hostnames.
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    # The shipped woff2 file is ~21 KB.
    assert 10_000 < len(resp.content) < 100_000


def test_bookmarklet_html_has_seo_metadata(client):
    body = client.get("/bookmarklet").text
    assert '<meta name="description"' in body
    assert '<meta name="theme-color"' in body
    assert '<link rel="canonical"' in body
    assert '<html lang="en">' in body


def test_ai_gateway_html_has_seo_metadata(client):
    body = client.get("/ai-gateway").text
    assert '<meta name="description"' in body
    assert '<meta name="theme-color"' in body
    assert '<link rel="canonical"' in body
    assert '<html lang="en">' in body
    # Self-hosted font preload — Google Fonts URL must be gone.
    assert "/static/jetbrains-mono.woff2" in body
    assert "fonts.googleapis.com" not in body


def test_ai_gateway_inline_script_is_module(client):
    body = client.get("/ai-gateway").text
    # Module scripts defer by default; this is the render-blocking fix.
    assert '<script type="module">' in body


def test_bookmarklet_link_is_nofollow(client):
    body = client.get("/bookmarklet").text
    # crawlable-anchors SEO audit treats javascript: hrefs as informational;
    # rel=nofollow is the explicit signal that the link isn't crawlable.
    assert 'rel="nofollow"' in body
