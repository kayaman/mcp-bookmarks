"""BookmarkService — save / extract / set-summary / delete orchestration.

Fast unit tests with hand-rolled fake backend + monkey-patched scraper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from mcp_bookmarks.models import ArticleContent, Bookmark, OGMetadata
from mcp_bookmarks.services import bookmark as bookmark_service


@dataclass
class _FakeBackend:
    """Tracks calls; returns canned Bookmark / bool replies."""

    capabilities: Any = None
    saved: dict[str, Any] = field(default_factory=dict)
    content_calls: list[tuple[int | str, str, int]] = field(default_factory=list)
    summary_calls: list[tuple[int | str, str]] = field(default_factory=list)
    get_response: Bookmark | None = None
    delete_response: bool = True

    async def upsert_bookmark(self, **kwargs: Any) -> Bookmark:
        self.saved = kwargs
        return Bookmark(id=99, url=kwargs["url"], title=kwargs.get("title"))

    async def set_bookmark_content(
        self, bookmark_id: int | str, content: str, word_count: int
    ) -> None:
        self.content_calls.append((bookmark_id, content, word_count))

    async def set_bookmark_summary(self, bookmark_id: int | str, summary: str) -> None:
        self.summary_calls.append((bookmark_id, summary))

    async def get_bookmark_by_id(self, bookmark_id: int | str) -> Bookmark | None:
        return self.get_response

    async def delete_bookmark(self, bookmark_id: int | str) -> bool:
        return self.delete_response


# ── save_with_metadata ─────────────────────────────────────────────


async def test_save_with_metadata_persists_og_fields(monkeypatch: pytest.MonkeyPatch):
    async def fake_og(url: str) -> OGMetadata:
        return OGMetadata(
            url="https://example.com/article",
            title="Original Title",
            description="A description.",
            image="https://example.com/img.png",
            site_name="example.com",
        )

    monkeypatch.setattr("mcp_bookmarks.services.bookmark.extract_og_metadata", fake_og)
    db = _FakeBackend()
    bookmark, og = await bookmark_service.save_with_metadata(
        db=db,  # type: ignore[arg-type]
        url="https://example.com/article",
        bookmark_type="article",
        flow_id="flow-1",
        source="bookmarklet",
    )
    assert bookmark.id == 99
    assert og.title == "Original Title"
    assert db.saved == {
        "url": "https://example.com/article",
        "title": "Original Title",
        "description": "A description.",
        "image_url": "https://example.com/img.png",
        "site_name": "example.com",
        "bookmark_type": "article",
        "flow_id": "flow-1",
        "source": "bookmarklet",
    }


async def test_save_with_metadata_caller_title_overrides_og(monkeypatch: pytest.MonkeyPatch):
    async def fake_og(url: str) -> OGMetadata:
        return OGMetadata(url=url, title="OG Title")

    monkeypatch.setattr("mcp_bookmarks.services.bookmark.extract_og_metadata", fake_og)
    db = _FakeBackend()
    bookmark, _ = await bookmark_service.save_with_metadata(
        db=db,  # type: ignore[arg-type]
        url="https://example.com",
        title="Caller Title",
    )
    assert db.saved["title"] == "Caller Title"
    assert bookmark.title == "Caller Title"


@pytest.mark.parametrize("exc", [OSError("conn reset"), ValueError("bad html")])
async def test_save_with_metadata_falls_back_on_scraper_error(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
):
    """OG extraction failure must not poison the save — log + persist URL-only."""

    async def fake_og(url: str) -> OGMetadata:
        raise exc

    monkeypatch.setattr("mcp_bookmarks.services.bookmark.extract_og_metadata", fake_og)
    db = _FakeBackend()
    bookmark, og = await bookmark_service.save_with_metadata(
        db=db,
        url="https://flaky.example.com",  # type: ignore[arg-type]
    )
    # Fallback OG carries just the URL
    assert og.url == "https://flaky.example.com"
    assert og.title is None
    # Bookmark still persisted
    assert db.saved["url"] == "https://flaky.example.com"
    assert bookmark.id == 99


# ── extract_and_persist_content ────────────────────────────────────


async def test_extract_and_persist_content_writes_word_count(monkeypatch: pytest.MonkeyPatch):
    async def fake_extract(url: str) -> ArticleContent:
        return ArticleContent(url=url, text="hello world", word_count=2)

    monkeypatch.setattr("mcp_bookmarks.services.bookmark.extract_article_content", fake_extract)
    db = _FakeBackend()
    word_count = await bookmark_service.extract_and_persist_content(
        db=db,
        bookmark_id=42,
        url="https://example.com",  # type: ignore[arg-type]
    )
    assert word_count == 2
    assert db.content_calls == [(42, "hello world", 2)]


@pytest.mark.parametrize("exc", [OSError("timeout"), ValueError("not html")])
async def test_extract_and_persist_returns_zero_on_failure(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
):
    async def fake_extract(url: str) -> ArticleContent:
        raise exc

    monkeypatch.setattr("mcp_bookmarks.services.bookmark.extract_article_content", fake_extract)
    db = _FakeBackend()
    word_count = await bookmark_service.extract_and_persist_content(
        db=db,
        bookmark_id=42,
        url="https://example.com",  # type: ignore[arg-type]
    )
    assert word_count == 0
    # No content write on extraction failure
    assert db.content_calls == []


# ── set_summary / get_or_none / delete / set_body ──────────────────


async def test_set_summary_passes_through_to_backend():
    db = _FakeBackend()
    await bookmark_service.set_summary(db=db, bookmark_id=1, summary="A summary.")  # type: ignore[arg-type]
    assert db.summary_calls == [(1, "A summary.")]


async def test_get_or_none_returns_backend_value():
    expected = Bookmark(id=7, url="https://example.com")
    db = _FakeBackend(get_response=expected)
    found = await bookmark_service.get_or_none(db=db, bookmark_id=7)  # type: ignore[arg-type]
    assert found is expected


async def test_get_or_none_returns_none_when_backend_missing():
    db = _FakeBackend(get_response=None)
    found = await bookmark_service.get_or_none(db=db, bookmark_id=404)  # type: ignore[arg-type]
    assert found is None


@pytest.mark.parametrize("backend_returns", [True, False])
async def test_delete_propagates_backend_result(backend_returns: bool):
    db = _FakeBackend(delete_response=backend_returns)
    deleted = await bookmark_service.delete(db=db, bookmark_id=1)  # type: ignore[arg-type]
    assert deleted is backend_returns


async def test_set_body_counts_words_with_split():
    db = _FakeBackend()
    word_count = await bookmark_service.set_body(
        db=db,
        bookmark_id=1,
        text="hello   world\n\nfoo bar",  # type: ignore[arg-type]
    )
    # split() collapses runs of whitespace
    assert word_count == 4
    assert db.content_calls == [(1, "hello   world\n\nfoo bar", 4)]
