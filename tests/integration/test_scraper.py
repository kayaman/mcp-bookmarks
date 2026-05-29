"""Scraper extraction against a canned HTML body — no live HTTP.

The ``offline_scraper`` fixture (tests/conftest.py) monkeypatches
``scraper.fetch_html`` to return a stable HTML string. Tests assert against
that fixed shape so they're deterministic in CI.

Real-network coverage of the scraper lives in tests/live/test_scraper_live.py
behind the ``live`` marker.
"""

from __future__ import annotations

import pytest

from mcp_bookmarks.scraper import extract_article_content, extract_og_metadata


pytestmark = pytest.mark.asyncio


async def test_extract_og_metadata_against_canned_html(offline_scraper):
    og = await extract_og_metadata("https://example.com/whatever")
    assert og.title == "Canned OG Title"
    assert og.description and "offline scraper" in og.description
    assert og.image == "https://example.com/cover.jpg"
    assert og.site_name == "Example"


async def test_extract_article_content_against_canned_html(offline_scraper):
    article = await extract_article_content("https://example.com/whatever")
    assert article.word_count > 0
    assert len(article.text) > 50
    assert article.extraction_method == "trafilatura"
