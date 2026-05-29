"""Pydantic model validation — pure unit tests, no I/O.

Extracted from the original tests/test_smoke.py smoke script.
"""

from mcp_bookmarks.models import ArticleContent, Bookmark, OGMetadata, Tag, Tenant


def test_tag_defaults():
    tag = Tag(slug="test-tag", name="Test Tag", description="For testing")
    assert tag.usage_count == 0
    assert tag.id is None
    assert tag.created_at is None


def test_bookmark_defaults():
    b = Bookmark(url="https://example.com")
    assert b.tags == []
    assert b.content is None
    assert b.word_count is None
    assert b.summary is None


def test_article_content_default_method():
    a = ArticleContent(url="https://example.com", text="Hello world", word_count=2)
    assert a.extraction_method == "trafilatura"


def test_og_metadata_optional_fields():
    og = OGMetadata(url="https://example.com")
    assert og.title is None
    assert og.description is None
    assert og.image is None
    assert og.site_name is None


def test_tenant_defaults():
    t = Tenant(organization_id="org-1")
    assert t.user_id is None
    assert t.scopes == []
