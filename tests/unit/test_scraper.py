"""Unit tests for the scraper module.

Network is mocked via httpx.MockTransport — these tests are fast and
deterministic. The integration version (tests/integration/test_scraper.py)
exercises the same module against pre-baked HTML on disk.
"""

from __future__ import annotations

import httpx
import pytest

from mcp_bookmarks import scraper
from mcp_bookmarks.models import OGMetadata


def _patch_async_client_to_handler(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Replace httpx.AsyncClient with a transport-mocked variant."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class _Patched(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)


# ── fetch_html ────────────────────────────────────────────────────


async def test_fetch_html_returns_text_body(monkeypatch: pytest.MonkeyPatch):
    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html><body>hi</body></html>")

    _patch_async_client_to_handler(monkeypatch, _h)
    html = await scraper.fetch_html("https://example.com")
    assert "<body>hi</body>" in html


async def test_fetch_html_raises_on_4xx(monkeypatch: pytest.MonkeyPatch):
    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    _patch_async_client_to_handler(monkeypatch, _h)
    with pytest.raises(httpx.HTTPStatusError):
        await scraper.fetch_html("https://example.com/missing")


async def test_fetch_html_raises_on_timeout(monkeypatch: pytest.MonkeyPatch):
    def _h(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    _patch_async_client_to_handler(monkeypatch, _h)
    with pytest.raises(httpx.TimeoutException):
        await scraper.fetch_html("https://slow.example.com")


# ── extract_og_metadata ────────────────────────────────────────────


async def test_extract_og_metadata_prefers_og_tags(monkeypatch: pytest.MonkeyPatch):
    html = """
    <html>
      <head>
        <title>HTML Title</title>
        <meta name="description" content="HTML desc.">
        <meta property="og:title" content="OG Title">
        <meta property="og:description" content="OG desc.">
        <meta property="og:image" content="https://example.com/img.png">
        <meta property="og:site_name" content="Example">
        <meta property="og:type" content="article">
        <meta name="author" content="Jane Doe">
      </head>
    </html>
    """

    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html)

    _patch_async_client_to_handler(monkeypatch, _h)
    og = await scraper.extract_og_metadata("https://example.com")
    assert isinstance(og, OGMetadata)
    assert og.title == "OG Title"
    assert og.description == "OG desc."
    assert og.image == "https://example.com/img.png"
    assert og.site_name == "Example"
    assert og.og_type == "article"
    assert og.author == "Jane Doe"


async def test_extract_og_metadata_falls_back_to_title_and_meta(
    monkeypatch: pytest.MonkeyPatch,
):
    html = """
    <html>
      <head>
        <title>Plain Title</title>
        <meta name="description" content="Plain desc.">
      </head>
    </html>
    """

    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html)

    _patch_async_client_to_handler(monkeypatch, _h)
    og = await scraper.extract_og_metadata("https://example.com")
    assert og.title == "Plain Title"
    assert og.description == "Plain desc."
    assert og.image is None
    assert og.site_name is None


async def test_extract_og_metadata_returns_url_only_on_empty_html(
    monkeypatch: pytest.MonkeyPatch,
):
    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html></html>")

    _patch_async_client_to_handler(monkeypatch, _h)
    og = await scraper.extract_og_metadata("https://example.com/empty")
    assert og.url == "https://example.com/empty"
    assert og.title is None
    assert og.description is None


# ── extract_article_content (trafilatura primary + BS4 fallback) ──


async def test_extract_article_uses_beautifulsoup_fallback_when_trafilatura_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    """When trafilatura returns empty/short text, fall back to BS4 <p> extraction."""
    # Pre-existing html with no article structure trafilatura recognizes —
    # but with enough <p> paragraphs (each > 30 chars) for the BS4 fallback.
    html = """
    <html><body>
      <nav>Some nav links here that should be stripped by the fallback</nav>
      <p>This is the first substantive paragraph that exceeds the 30 char floor.</p>
      <p>This is the second substantive paragraph; also exceeds the floor easily.</p>
      <script>console.log('should be stripped');</script>
    </body></html>
    """

    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html)

    _patch_async_client_to_handler(monkeypatch, _h)

    # Force trafilatura.extract to return None so the fallback fires.
    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: None)

    article = await scraper.extract_article_content("https://example.com/short")
    assert article.extraction_method == "beautifulsoup-fallback"
    # Both paragraphs joined
    assert "first substantive paragraph" in article.text
    assert "second substantive paragraph" in article.text
    # Script content stripped
    assert "should be stripped" not in article.text
    assert article.word_count > 0


async def test_extract_article_uses_trafilatura_when_long_enough(
    monkeypatch: pytest.MonkeyPatch,
):
    """When trafilatura returns substantial text, use it as-is."""
    long_text = "Article body. " * 50  # ~650 chars, well over the 100 floor

    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=f"<html><body><p>{long_text}</p></body></html>")

    _patch_async_client_to_handler(monkeypatch, _h)

    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: long_text)

    article = await scraper.extract_article_content("https://example.com/article")
    assert article.extraction_method == "trafilatura"
    assert article.text == long_text
    assert article.word_count == len(long_text.split())


async def test_extract_article_returns_empty_when_both_paths_yield_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """Trafilatura None + no <p> tags → empty text, word_count 0, BS4-fallback method."""

    def _h(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html><body></body></html>")

    _patch_async_client_to_handler(monkeypatch, _h)

    import trafilatura

    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: None)

    article = await scraper.extract_article_content("https://example.com/empty")
    assert article.text == ""
    assert article.word_count == 0
    assert article.extraction_method == "beautifulsoup-fallback"
