"""Tests for the bidirectional schema bridge.

The canonical wire shape is camelCase (``ogTitle``, ``ogDescription``,
``ogImage``, ``ogSiteName``, ``aiSummary``, ``aiTags``, ``aiContent``,
``aiWordCount``, ``bookmarkType``, ``savedAt``); legacy mcp-bookmarks
clients read snake_case (``description``, ``image_url``, ``site_name``,
``summary``, ``content``, ``word_count``). This module verifies:

1. ``Bookmark`` model accepts EITHER naming convention as input.
2. ``Bookmark.model_dump(by_alias=True)`` emits camelCase.
3. ``_to_bookmark`` reads camelCase preferentially with snake_case fallback.
4. ``upsert_bookmark`` writes camelCase keys to DDB so subsequent reads see them.
5. The tool serializers (``save_bookmark``, ``read_bookmark``,
   ``search_bookmarks``) include the camelCase fields in their JSON output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_bookmarks.dynamodb import _to_bookmark
from mcp_bookmarks.models import Bookmark


# ── Bookmark model: dual-name fluency ───────────────────────────────


def test_bookmark_accepts_camelcase_input():
    b = Bookmark(
        url="https://x",
        ogTitle="T",
        ogImage="https://i",
        aiSummary="s",
        aiTags=["python"],
        aiWordCount=42,
        bookmarkType="read_later",
    )
    assert b.og_title == "T"
    assert b.og_image == "https://i"
    assert b.ai_summary == "s"
    assert b.ai_tags == ["python"]
    assert b.ai_word_count == 42
    assert b.bookmark_type == "read_later"


def test_bookmark_accepts_snakecase_input():
    b = Bookmark(
        url="https://x",
        og_title="T",
        og_image="https://i",
        ai_summary="s",
        ai_tags=["python"],
        ai_word_count=42,
        bookmark_type="read_later",
    )
    # Both attribute styles read back the same value.
    assert b.og_title == "T"
    assert b.ai_word_count == 42


def test_bookmark_dump_by_alias_emits_camelcase():
    b = Bookmark(url="https://x", og_image="https://i", ai_summary="s")
    dumped = b.model_dump(by_alias=True, exclude_none=True)
    assert dumped["ogImage"] == "https://i"
    assert dumped["aiSummary"] == "s"
    assert "og_image" not in dumped
    assert "ai_summary" not in dumped


def test_bookmark_dump_default_emits_snakecase():
    b = Bookmark(url="https://x", og_image="https://i", ai_summary="s")
    dumped = b.model_dump(exclude_none=True)
    assert dumped["og_image"] == "https://i"
    assert dumped["ai_summary"] == "s"
    assert "ogImage" not in dumped


# ── _to_bookmark: read both shapes from DDB ─────────────────────────


def test_to_bookmark_prefers_camelcase():
    """When the DDB item has both keys, camelCase wins."""
    item = {
        "id": "abc123",
        "url": "https://example.com",
        "title": "Title",
        "ogTitle": "OG Title",
        "ogDescription": "og desc",
        "ogImage": "https://i/og.jpg",
        "ogSiteName": "Example",
        "description": "legacy desc",
        "image_url": "https://i/legacy.jpg",
        "site_name": "Legacy",
        "aiSummary": "summary",
        "aiTags": ["python", "web"],
        "aiContent": "full text",
        "aiWordCount": 100,
        "aiStatus": "DONE",
        "bookmarkType": "read_later",
        "savedAt": "2026-05-06T00:00:00Z",
        "source": "web",
    }
    b = _to_bookmark(item)
    # camelCase OG wins for the snake_case-back-compat fields too.
    assert b.image_url == "https://i/og.jpg"
    assert b.site_name == "Example"
    assert b.description == "og desc"
    # Direct camelCase access:
    assert b.og_image == "https://i/og.jpg"
    assert b.ai_summary == "summary"
    assert b.ai_tags == ["python", "web"]
    assert b.ai_content == "full text"
    assert b.ai_word_count == 100
    assert b.bookmark_type == "read_later"
    assert b.saved_at == "2026-05-06T00:00:00Z"
    assert b.source == "web"


def test_to_bookmark_fallback_to_snakecase():
    """When the DDB item has only snake_case (legacy mcp-bookmarks writes), use those."""
    item = {
        "id": "abc",
        "url": "https://example.com",
        "title": "Title",
        "description": "legacy",
        "image_url": "https://i/legacy.jpg",
        "site_name": "Legacy",
    }
    b = _to_bookmark(item)
    assert b.image_url == "https://i/legacy.jpg"
    assert b.site_name == "Legacy"
    assert b.description == "legacy"
    assert b.og_image is None  # camelCase fields stay empty
    assert b.og_site_name is None


def test_to_bookmark_surfaces_ownership_fields():
    """Ownership + share metadata fields roundtrip cleanly through
    _to_bookmark and serialize back as camelCase."""
    item = {
        "id": "abc",
        "url": "https://example.com",
        "title": "T",
        "notes": "private notes",
        "mcpExposed": True,
        "visibility": "unlisted",
        "shareToken": "tok-123",
        "scrapingStatus": "done",
        "scrapedAt": "2026-05-06T00:30:00Z",
        "aiProcessedAt": "2026-05-06T00:35:00Z",
        "aiEnrichmentAttempts": 2,
    }
    b = _to_bookmark(item)
    assert b.notes == "private notes"
    assert b.mcp_exposed is True
    assert b.visibility == "unlisted"
    assert b.share_token == "tok-123"
    assert b.scraping_status == "done"
    assert b.scraped_at == "2026-05-06T00:30:00Z"
    assert b.ai_processed_at == "2026-05-06T00:35:00Z"
    assert b.ai_enrichment_attempts == 2

    dumped = b.model_dump(by_alias=True, exclude_none=True)
    assert dumped["mcpExposed"] is True
    assert dumped["visibility"] == "unlisted"
    assert dumped["shareToken"] == "tok-123"
    assert dumped["scrapingStatus"] == "done"
    assert dumped["scrapedAt"] == "2026-05-06T00:30:00Z"
    assert dumped["aiProcessedAt"] == "2026-05-06T00:35:00Z"
    assert dumped["aiEnrichmentAttempts"] == 2
    assert dumped["notes"] == "private notes"


def test_to_bookmark_handles_camelcase_legacy_shape():
    """A bookmark written by an external Lambda using camelCase keys surfaces correctly."""
    item = {
        "id": "abc",
        "url": "https://example.com",
        "ogTitle": "Article",
        "ogDescription": "About things",
        "ogImage": "https://i/cover.jpg",
        "ogSiteName": "Blog",
        "aiSummary": "TL;DR: things.",
        "aiTags": ["essays"],
        "aiContent": "Long form.",
        "aiWordCount": 1500,
        "aiStatus": "DONE",
        "bookmarkType": "knowledge",
        "savedAt": "2026-05-06T00:00:00Z",
        "source": "web",
        "userId": "user-X",
        "organization_id": "test-org",
    }
    b = _to_bookmark(item)
    assert b.og_title == "Article"
    assert b.og_image == "https://i/cover.jpg"
    assert b.ai_summary == "TL;DR: things."
    assert b.ai_tags == ["essays"]
    assert b.ai_content == "Long form."
    assert b.ai_word_count == 1500
    assert b.bookmark_type == "knowledge"
    # And the snake_case mirror still works for legacy callers.
    assert b.image_url == "https://i/cover.jpg"
    assert b.summary == "TL;DR: things."


# ── upsert_bookmark: writes camelCase keys ──────────────────────────


@pytest.mark.asyncio
async def test_upsert_bookmark_writes_camelcase_keys():
    """The DDB item written by upsert_bookmark must have camelCase OG keys
    (so a subsequent read sees ogImage/ogDescription/...)."""
    from mcp_bookmarks.dynamodb import DynamoDBDatabase

    captured: dict = {}

    fake_links = MagicMock()
    fake_tags = MagicMock()

    def fake_put_item(**kwargs):
        captured.update(kwargs.get("Item", {}))

    fake_links.put_item.side_effect = fake_put_item

    with patch("mcp_bookmarks.dynamodb._dynamo") as fake_dynamo:
        fake_dynamo.return_value.Table.side_effect = lambda name: (
            fake_links if "links" in name else fake_tags
        )
        db = DynamoDBDatabase()
        bookmark = await db.upsert_bookmark(
            url="https://example.com",
            title="Article",
            description="Hello",
            image_url="https://i/cover.jpg",
            site_name="Blog",
            bookmark_type="read_later",
            flow_id="flow-abc",
            source="share_target",
        )

    # camelCase keys present:
    assert captured["ogTitle"] == "Article"
    assert captured["ogDescription"] == "Hello"
    assert captured["ogImage"] == "https://i/cover.jpg"
    assert captured["ogSiteName"] == "Blog"
    assert captured["bookmarkType"] == "read_later"
    assert captured["flowId"] == "flow-abc"
    assert captured["source"] == "share_target"
    # ... and the returned Bookmark exposes them:
    assert bookmark.og_image == "https://i/cover.jpg"
    assert bookmark.bookmark_type == "read_later"
    assert bookmark.saved_at is not None


# ── Tool response shapes ────────────────────────────────────────────


def test_search_serializer_emits_camelcase_per_item():
    """search_bookmarks per-item shape includes the canonical camelCase fields."""
    b = Bookmark(
        url="https://x",
        title="T",
        og_title="T",
        og_image="https://i",
        og_description="d",
        ai_summary="s",
        ai_tags=["python"],
        ai_word_count=42,
        bookmark_type="read_later",
        saved_at="2026-05-06T00:00:00Z",
        source="web",
        dynamo_id="abc",
    )
    item = b.model_dump(by_alias=True, exclude_none=True)
    item.setdefault("id", b.dynamo_id or b.id)
    item["has_content"] = b.content is not None
    # External feed adapters expect all of these:
    for k in ("id", "url", "title", "ogImage", "ogDescription", "aiSummary",
              "aiTags", "aiWordCount", "bookmarkType", "savedAt", "source"):
        assert k in item, f"missing {k}"
    assert item["aiTags"] == ["python"]
    assert item["aiWordCount"] == 42


def test_read_serializer_emits_aicontent_alias():
    """read_bookmark must emit both 'content' (legacy) and 'aiContent' (canonical)
    so the dual-shape contract holds for content too."""
    b = Bookmark(
        url="https://x",
        title="T",
        content="Long article body.",
        word_count=3,
        ai_content="Long article body.",
        ai_word_count=3,
        dynamo_id="abc",
    )
    result = b.model_dump(by_alias=True, exclude_none=True)
    result.setdefault("id", b.dynamo_id or b.id)
    snake = b.model_dump(exclude_none=True)
    for k in ("description", "image_url", "site_name", "summary", "word_count"):
        if k in snake and k not in result:
            result[k] = snake[k]
    # Simulate the post-processing in the read_bookmark tool:
    if b.content:
        result["content"] = b.content
        result["aiContent"] = b.content
    assert result["content"] == "Long article body."
    assert result["aiContent"] == "Long article body."
    assert result["word_count"] == 3
    assert result["aiWordCount"] == 3
