"""LIVE: scraper hits real public URLs.

Offline equivalent: ``tests/integration/test_scraper.py`` uses the
``offline_scraper`` fixture to feed canned HTML to the same functions.
"""

from __future__ import annotations

import pytest

from mcp_bookmarks.scraper import extract_article_content, extract_og_metadata

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


async def test_og_metadata_against_github():
    og = await extract_og_metadata("https://github.com")
    assert og.title is not None and len(og.title) > 0


async def test_article_extraction_against_known_long_page():
    article = await extract_article_content(
        "https://modelcontextprotocol.io/docs/concepts/architecture"
    )
    assert article.word_count > 0
    assert len(article.text) > 100
    assert article.extraction_method == "trafilatura"
